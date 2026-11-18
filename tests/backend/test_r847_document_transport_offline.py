"""Authenticated internal transport using real ASGI body frames and the SQLite session store."""
import asyncio
import hashlib
import logging
import warnings
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from backend.auth.dependencies import bearer_scheme, get_current_user
from backend.auth.models import AuthStore, ClearanceLevel, User
from backend.auth.request_context import effective_owner
from backend.auth.security import SessionManager
from backend.multimodal import document_transport as dt
from backend.multimodal.document_reasoning import ReasoningError
from test_a21_conversation_offline import setup_engine
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_docx, sample_pdf, save_office
from test_r844_document_agent_offline import workbook
from test_r83_text_file_offline import Provider, app_with_text_profile
from test_r86_csv_preview_offline import client


@pytest.mark.parametrize('suffix', ['.pdf', '.docx', '.xlsx'])
@pytest.mark.parametrize('multipart_upload', [True, False])
def test_ordinary_chat_rejects_protected_documents(tmp_path, suffix, multipart_upload):
    provider = Provider()
    app = app_with_text_profile(tmp_path, provider)
    mime = dt._TYPES[suffix][0]
    data = b'synthetic-private-marker'

    async def run():
        async with client(app) as http:
            options = ({'files': {'file': ('check' + suffix, data, mime)}} if multipart_upload
                       else {'content': data, 'headers': {'Content-Type': mime}})
            response = await http.post('/api/chat', **options)
            assert response.status_code == 415
            assert data not in response.content
        assert not provider.requests
    run_offline(run())


@pytest.fixture
def host(tmp_path):
    app = FastAPI()
    state = app.state
    state.engine, _ = setup_engine(tmp_path)
    state.settings = SimpleNamespace(auth_enabled=True)
    state.auth_store = AuthStore(tmp_path / 'auth.db')
    state.auth_store.create_user(User(id='a', username='a', password_hash='unused', role='operator',
                                     clearance='L2', is_active=True, must_change_password=False,
                                     created_at='2026-01-01T00:00:00+00:00'))
    state.session_manager = SessionManager(state.auth_store)
    token, _ = state.session_manager.create_session('a')
    for owner in ('a', 'b'):
        marker = effective_owner.set(owner)
        try:
            state.engine._memory.prepare_new_chat(owner + '-conversation', owner, 'Ordinary chat')
        finally:
            effective_owner.reset(marker)
    return app, token


def grant(name, data):
    return dt.DocumentGrant(name, len(data), hashlib.sha256(data).hexdigest(), ClearanceLevel.L2)


def multipart(files):
    chunks = []
    for name, data in files:
        mime = dt._TYPES['.' + name.split('.')[-1]][0]
        chunks.extend([f'--edge\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                       f'Content-Type: {mime}\r\n\r\n'.encode(), data, b'\r\n'])
    return b''.join([*chunks, b'--edge--\r\n'])


class Upload:
    def __init__(self, host, files=None, *, body=None, headers=(), receive=None, chunk_size=8192):
        self.app, self.token = host
        self.files = files if files is not None else [('check.pdf', sample_pdf())]
        self.grants = tuple(grant(*item) for item in self.files)
        self.body = multipart(self.files) if body is None else body
        self.reads = 0
        self.cancelled = False

        async def read():
            start = self.reads * chunk_size
            self.reads += 1
            return {'type': 'http.request', 'body': self.body[start:start + chunk_size],
                    'more_body': start + chunk_size < len(self.body)}

        self.request = Request({'type': 'http', 'method': 'POST', 'app': self.app, 'headers': [
            (b'authorization', ('Bearer ' + self.token).encode()),
            (b'content-type', b'multipart/form-data; boundary=edge'), *headers,
        ]}, receive=receive or read)

    async def authenticate(self):
        await get_current_user(self.request, await bearer_scheme(self.request), None, None, None)

    def open(self, conversation='a-conversation', **options):
        def authority(owner, actual_conversation):
            return self.grants if (owner, actual_conversation) == ('a', 'a-conversation') else ()
        return dt.open_document_transport(self.request, conversation, source_authority=authority,
                                          cancelled=lambda: self.cancelled, **options)


@pytest.mark.filterwarnings('error')
def test_verified_batch_and_cleanup(host, tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger='pdfminer')
    upload = Upload(host, [('check.pdf', sample_pdf()), ('record.docx', save_office(sample_docx())),
                           ('readings.xlsx', workbook())], chunk_size=73)
    memory = host[0].state.engine._memory
    before = (tmp_path / 'memory.json').read_bytes()
    names = {p.name for p in tmp_path.iterdir()}

    async def run():
        await upload.authenticate()
        async with upload.open() as batch:
            pdf, docx, xlsx = batch.sources()
            assert len({s.source_id for s in (pdf, docx, xlsx)}) == 3
            assert [s.filename for s in (pdf, docx, xlsx)] == [p[0] for p in upload.files]
            reasoner = batch.reasoning()
            citation, = reasoner.find_passages(batch.resolve(pdf), 'pump vibration')
            assert '7.1 mm/s' in citation.value and citation.location.page == 1
            assert citation.filename == pdf.filename and batch.source(pdf).warnings
            assert batch.source(xlsx).locations[0].sheet == 'Sheet1'
            reasoner.verify_citations(citation)
            batch.check()
        with pytest.raises(ReasoningError, match='closed'):
            batch.resolve(pdf)
        with pytest.raises(ReasoningError, match='closed'):
            reasoner.verify_citations(citation)
        with pytest.raises(ReasoningError, match='closed'):
            async with upload.open():
                pytest.fail('Reused upload')
        assert memory.numeric_receipt('a-conversation') is None
    run_offline(run())
    assert (tmp_path / 'memory.json').read_bytes() == before
    assert {p.name for p in tmp_path.iterdir()} == names
    assert all(token not in caplog.text for token in ('7.1 mm/s', 'check.pdf', 'ignore instructions'))
    assert any(record.name.startswith('pdfminer.') and record.getMessage() == 'Protected document decoder diagnostic.'
               for record in caplog.records)


@pytest.mark.parametrize('case', [
    'no_session', 'spoofed_identity', 'copied_scope_state', 'other_owner', 'missing_authority', 'clearance', 'role',
    'synthetic_mode', 'count', 'individual_size', 'aggregate_size', 'bad_policy_filename', 'revoked',
])
def test_denied_before_read(host, case):
    upload = Upload(host, headers=[(b'x-user-role', b'admin'), (b'x-clearance-level', b'L3')])

    async def run():
        if case != 'no_session':
            await upload.authenticate()
        if case in ('spoofed_identity', 'copied_scope_state'):
            # A second ASGI scope on the same task has no authentication binding of its own.
            other = Upload(host)
            if case == 'copied_scope_state':
                other.request.scope['state'] = upload.request.scope['state'].copy()
            upload.request = other.request
        elif case == 'missing_authority':
            upload.grants = ()
        elif case == 'clearance':
            upload.grants = (replace(upload.grants[0], clearance=ClearanceLevel.L3),)
        elif case == 'role':
            host[0].state.auth_store.update_user('a', role='viewer')
        elif case == 'synthetic_mode':
            host[0].state.settings.auth_enabled = False
        elif case == 'count':
            upload.grants *= dt.MAX_INPUTS + 1
        elif case == 'individual_size':
            upload.grants = (replace(upload.grants[0], size=dt.MAX_PDF_BYTES + 1),)
        elif case == 'aggregate_size':
            upload.grants = (replace(upload.grants[0], size=dt.MAX_PDF_BYTES),) * 2
        elif case == 'bad_policy_filename':
            upload.grants = (replace(upload.grants[0], filename='../check.pdf'),)
        elif case == 'revoked':
            host[0].state.session_manager.revoke_session(host[1])
        with pytest.raises(ReasoningError) as failure:
            async with upload.open('b-conversation' if case == 'other_owner' else 'a-conversation'):
                pytest.fail('Unexpected admission')
        assert failure.value.code == 'AUTHORIZATION_UNAVAILABLE'
        assert upload.reads == 0
    run_offline(run())


@pytest.mark.parametrize(('case', 'code'), [
    ('wrong_mime', 'SOURCE_UNAVAILABLE'), ('traversal', 'SOURCE_UNAVAILABLE'),
    ('wrong_digest', 'SOURCE_UNAVAILABLE'), ('inserted_id', 'INVALID_FORM'),
    ('missing_end', 'INVALID_FORM'), ('epilogue', 'INVALID_FORM'),
    ('extra_file', 'INPUT_LIMIT'), ('overflow', 'INPUT_LIMIT'), ('short', 'SOURCE_UNAVAILABLE'),
    ('framing', 'INPUT_LIMIT'), ('declared_length', 'INVALID_FORM'),
])
def test_wire_validation_precedes_extraction(host, monkeypatch, case, code):
    upload = Upload(host, headers=[(b'content-length', b'1')] if case == 'declared_length' else [])
    raw = upload.body
    if case == 'wrong_mime':
        upload.body = raw.replace(b'application/pdf', b'text/plain')
    elif case == 'traversal':
        upload.body = raw.replace(b'filename="', b'filename="C:\\')
    elif case == 'wrong_digest':
        upload.body = raw.replace(b'%PDF-', b'%XXX-', 1)
    elif case == 'inserted_id':
        upload.body = raw.replace(b'Content-Type:', b'X-Source-ID: 1\r\nContent-Type:')
    elif case == 'missing_end':
        upload.body = raw[:-7]
    elif case == 'epilogue':
        upload.body += b'unaccepted'
    elif case == 'extra_file':
        upload.body = multipart(upload.files * 2)
    elif case == 'overflow':
        upload.body = raw.replace(b'%PDF-', b'X%PDF-', 1)
    elif case == 'short':
        upload.body = raw.replace(b'%PDF-', b'PDF-', 1)
    elif case == 'framing':
        upload.body = raw.replace(b'Content-Type:', b'X: ' + b'x' * dt._FRAMING + b'\r\nContent-Type:')
    monkeypatch.setattr(dt, '_preflight', lambda *_: pytest.fail('Reached structure preflight'))

    async def run():
        await upload.authenticate()
        with pytest.raises(ReasoningError) as failure:
            async with upload.open():
                pytest.fail('Unexpected admission')
        assert failure.value.code == code
    run_offline(run())


def test_invalid_last_document_prevents_all_extraction(host, monkeypatch):
    upload = Upload(host, [('one.pdf', sample_pdf()), ('two.docx', b'PK-invalid')])
    monkeypatch.setattr(dt, 'open_document_reasoning', lambda *a, **kw: pytest.fail('Partial extraction'))

    async def run():
        await upload.authenticate()
        with pytest.raises(ReasoningError) as failure:
            async with upload.open():
                pytest.fail('Partial admission')
        assert failure.value.code == 'EXTRACTION_FAILED'
    run_offline(run())


@pytest.mark.filterwarnings('error')
@pytest.mark.parametrize('case', ['during_parse', 'before_extract', 'before_return', 'clearance_loss',
                                  'changed_policy', 'session_replaced', 'new_generation', 'conversation_deleted',
                                  'cancel', 'deadline'])
def test_lost_authority_or_lifetime_withholds_lease(host, monkeypatch, case):
    upload = Upload(host)
    state = host[0].state
    def revoke():
        state.session_manager.revoke_session(host[1])

    if case == 'during_parse':
        original = dt.DocumentParser.parse
        def parse(name, data):
            doc = original(name, data)
            revoke()
            return doc
        monkeypatch.setattr(dt.DocumentParser, 'parse', parse)
    if case == 'before_extract':
        original_preflight = dt._preflight
        def preflight(files, access):
            original_preflight(files, access)
            revoke()
        monkeypatch.setattr(dt, '_preflight', preflight)
        monkeypatch.setattr(dt.DocumentParser, 'parse', lambda *_: pytest.fail('Extracted after revocation'))

    async def run():
        await upload.authenticate()
        batch = None
        with pytest.raises(ReasoningError) as failure:
            async with upload.open() as batch:
                source, = batch.sources()
                if case == 'before_return':
                    revoke()
                elif case == 'clearance_loss':
                    state.auth_store.update_user('a', clearance='L1')
                elif case == 'changed_policy':
                    upload.grants = ()
                elif case == 'session_replaced':
                    state.session_manager = SessionManager(state.auth_store)
                elif case == 'new_generation':
                    session = state.session_manager.validate_session(host[1])
                    created_at = datetime.fromisoformat(session.created_at) - timedelta(seconds=1)
                    state.auth_store.save_session(session.model_copy(update={'created_at': created_at.isoformat()}))
                elif case == 'conversation_deleted':
                    state.engine._memory.delete_session('a-conversation')
                elif case == 'cancel':
                    upload.cancelled = True
                elif case == 'deadline':
                    batch._access.deadline = asyncio.get_running_loop().time()
                elif case != 'during_parse':
                    pytest.fail('Evidence was exposed')
                batch.resolve(source)
        expected = {'cancel': 'CANCELLED', 'deadline': 'TIMEOUT'}.get(case, 'AUTHORIZATION_UNAVAILABLE')
        assert failure.value.code == expected
        if batch is not None:
            with pytest.raises(ReasoningError, match='closed'):
                batch.sources()
    run_offline(run())


def test_same_name_cross_request_and_forged_source_rejected(host):
    async def run():
        data = sample_pdf()
        first = Upload(host, [('same.pdf', data), ('same.pdf', data)])
        await first.authenticate()
        async with first.open() as one:
            a, b = one.sources()
            assert a.filename == b.filename and a.source_id != b.source_id
            with pytest.raises(ReasoningError):
                one.resolve(replace(a, source_id=b.source_id))
        second = Upload(host, [('same.pdf', data)])
        await second.authenticate()
        async with second.open() as two:
            assert two.request_id != one.request_id
            with pytest.raises(ReasoningError):
                two.resolve(a)
    run_offline(run())


def test_colliding_generated_source_ids_reject_entire_batch(host, monkeypatch):
    files = [('one.pdf', sample_pdf()), ('two.xlsx', workbook())]
    upload = Upload(host, files)
    monkeypatch.setattr(dt, 'uuid4', lambda: SimpleNamespace(hex='repeated'))

    async def run():
        await upload.authenticate()
        with pytest.raises(ReasoningError) as failure:
            async with upload.open():
                pytest.fail('Accepted ambiguous handles')
        assert failure.value.code == 'SOURCE_UNAVAILABLE'
    run_offline(run())


def test_distinct_bytes_under_identical_filenames_cannot_swap_slots(host):
    first = ('same.pdf', sample_pdf())
    # Same name, length and media type; the digest is the deciding evidence.
    second = ('same.pdf', first[1].replace(b'%PDF-1.', b'%PDF-2.', 1))
    assert len(first[1]) == len(second[1]) and first != second
    upload = Upload(host, [first, second], body=multipart([second, first]))

    async def run():
        await upload.authenticate()
        with pytest.raises(ReasoningError) as failure:
            async with upload.open():
                pytest.fail('Accepted swapped sources')
        assert failure.value.code == 'SOURCE_UNAVAILABLE'
    run_offline(run())


@pytest.mark.parametrize('value', [0.0, float('nan'), float('inf')])
def test_invalid_or_elapsed_parent_deadline_consumes_no_body(host, value):
    upload = Upload(host)

    async def run():
        await upload.authenticate()
        with pytest.raises(ReasoningError) as failure:
            async with upload.open(deadline=value):
                pytest.fail('Accepted an invalid deadline')
        assert failure.value.code == 'TIMEOUT' and upload.reads == 0
    run_offline(run())


def test_cookie_session_requires_existing_csrf_check_before_ingress(host):
    upload = Upload(host)
    headers = upload.request.scope['headers']
    headers[:] = [h for h in headers if h[0] != b'authorization']
    headers.append((b'cookie', ('session_token=' + host[1]).encode()))

    async def run():
        with pytest.raises(HTTPException) as failure:
            await get_current_user(upload.request, None, None, None, None)
        assert failure.value.status_code == 403
        with pytest.raises(ReasoningError):
            async with upload.open():
                pytest.fail('Accepted failed cookie authentication')
        assert upload.reads == 0
        await get_current_user(upload.request, None, 'XMLHttpRequest', None, None)
        async with upload.open() as lease:
            assert lease.sources()[0].filename == 'check.pdf'
    run_offline(run())


def test_workbook_specific_warning_policy_suffices(host):
    upload = Upload(host, [('readings.xlsx', workbook())])

    async def run():
        await upload.authenticate()
        with warnings.catch_warnings():
            warnings.filterwarnings('error', module=r'^openpyxl(?:\.|$)')
            async with upload.open() as lease:
                handle, = lease.sources()
                assert lease.source(handle).locations[0].sheet == 'Sheet1'
    run_offline(run())


@pytest.mark.parametrize('case', ['ambiguous_length', 'encoded_body'])
def test_rejects_conflicting_framing_before_receive(host, case):
    headers = ([(b'content-length', b'99'), (b'transfer-encoding', b'chunked')] if case == 'ambiguous_length'
               else [(b'content-encoding', b'gzip')])
    upload = Upload(host, headers=headers)

    async def run():
        await upload.authenticate()
        with pytest.raises(ReasoningError) as failure:
            async with upload.open():
                pytest.fail('Accepted conflicting framing')
        assert failure.value.code == 'INVALID_FORM' and upload.reads == 0
    run_offline(run())


def test_synchronous_parser_returning_after_deadline_cannot_publish(host, monkeypatch):
    upload = Upload(host)
    original = dt.DocumentParser.parse

    async def run():
        await upload.authenticate()
        with pytest.raises(ReasoningError) as failure:
            async with upload.open() as lease:
                def late_parse(name, data):
                    result = original(name, data)
                    lease._access.deadline = asyncio.get_running_loop().time()
                    return result
                monkeypatch.setattr(dt.DocumentParser, 'parse', late_parse)
                lease.reasoning()
        assert failure.value.code == 'TIMEOUT'
        assert lease._files == () and lease._scope is None
    run_offline(run())


def test_ingress_works_without_overriding_decoder_warning_policy(host):
    upload = Upload(host, [('readings.xlsx', workbook())])

    async def run():
        await upload.authenticate()
        with warnings.catch_warnings():
            warnings.simplefilter('default')
            policy = list(warnings.filters)
            with pytest.raises(ReasoningError) as failure:
                async with upload.open() as lease:
                    handle, = lease.sources()
                    assert handle.filename == 'readings.xlsx'
                    lease.reasoning()
            assert failure.value.code == 'PARSER_DIAGNOSTICS_UNAVAILABLE'
            assert policy == warnings.filters
            with pytest.raises(ReasoningError, match='closed'):
                lease.sources()
    run_offline(run())


@pytest.mark.filterwarnings('error')
def test_decoder_failure_clears_lease_and_withholds_private_diagnostic(host, caplog):
    # Valid structural package; an out-of-range Excel date raises a source-bearing
    # library warning when the rich reader loads the typed cell.
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet['A1'] = 1234567890123
    sheet['A1'].number_format = 'yyyy-mm-dd'
    data = save_office(book)
    book.close()
    upload = Upload(host, [('readings.xlsx', data)])

    async def run():
        await upload.authenticate()
        with pytest.raises(ReasoningError) as failure:
            async with upload.open() as lease:
                lease.reasoning()
        assert failure.value.code == 'EXTRACTION_FAILED'
        assert lease._files == () and lease._scope is None
        assert '1234567890123' not in str(failure.value) + caplog.text
    run_offline(run())


@pytest.mark.parametrize('case', ['task_cancel', 'deadline', 'disconnect'])
def test_interrupted_receive_closes_every_memory_file(host, monkeypatch, case):
    opened = []
    original = dt._Parts.on_headers_finished
    def observe(parser):
        original(parser)
        opened.append(parser._current_part.file.file)
    monkeypatch.setattr(dt._Parts, 'on_headers_finished', observe)

    async def run():
        waiting = asyncio.Event()
        async def receive():
            if not opened:
                return {'type': 'http.request', 'body': upload.body[:250], 'more_body': True}
            waiting.set()
            if case == 'disconnect':
                return {'type': 'http.disconnect'}
            await asyncio.Event().wait()

        upload = Upload(host, receive=receive)
        async def ingest():
            await upload.authenticate()
            async with upload.open():
                pytest.fail('Incomplete stream was accepted')

        if case == 'deadline':
            monkeypatch.setattr(dt, '_SECONDS', 0.2)
        task = asyncio.create_task(ingest())
        await asyncio.wait_for(waiting.wait(), 2)
        if case == 'task_cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ReasoningError) as failure:
                await task
            assert failure.value.code == ('TIMEOUT' if case == 'deadline' else 'CANCELLED')
        assert len(opened) == 1 and opened[0].closed and not opened[0]._rolled
    run_offline(run())
