"""Exercise the local issuer through the real session, transport and workflow."""

import asyncio
from contextlib import aclosing
from dataclasses import replace
import hashlib

import pytest

from backend.agent.document_workflow import run_document_workflow
from backend.auth.models import ClearanceLevel
from backend.auth.request_context import effective_owner, request_tools_enabled
from backend.multimodal import document_source_authority as registry, document_transport as transport
from backend.multimodal.document_reasoning import ReasoningError
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_docx, sample_pdf, save_office
from test_r848_document_agent_lease_offline import PASSAGE, Upload, app_engine, host  # noqa: F401


CONVERSATION = 'a-conversation'


def approved(filename, data, *, owner='a', clearance=ClearanceLevel.L2):
    return registry.ApprovedLocalSource(
        owner, 'revision-1', transport.DocumentGrant(filename, len(data), hashlib.sha256(data).hexdigest(), clearance),
    )


def policy(app, records):
    identity = app.state.engine._memory.stream_identity(CONVERSATION)

    def lookup(reference, owner, conversation, generation):
        if conversation != CONVERSATION or (owner, generation) != identity:
            return None
        return records.get(reference)

    return lookup


def must_fail(code, operation, *args, **kwargs):
    with pytest.raises(ReasoningError) as rejected:
        operation(*args, **kwargs)
    assert rejected.value.code == code


def test_issued_identity_survives_admission_in_order(host, app_engine):
    files = [('check.pdf', sample_pdf()), ('minutes.docx', save_office(sample_docx()))]
    upload = Upload(host, files)

    async def run():
        await upload.authenticate()
        records = {str(i): approved(name, data) for i, (name, data) in enumerate(files)}
        lookup = policy(host[0], records)
        with registry.open_local_source_authority(upload.request, CONVERSATION, lookup=lookup) as source:
            assert source('a', CONVERSATION) == ()
            handles = tuple(source.register(str(i), data) for i, (_, data) in enumerate(files))
            assert len({handle.source_id for handle in handles}) == 2
            assert len({handle.request_id for handle in handles}) == 1
            grants = source('a', CONVERSATION)
            assert [(g.sha256, g.size) for g in grants] == [
                (hashlib.sha256(data).hexdigest(), len(data)) for _, data in files
            ]
            assert source('a', 'b-conversation') == () and source('b', CONVERSATION) == ()
            async with transport.open_document_transport(upload.request, CONVERSATION, source_authority=source) as lease:
                assert all(actual is expected for actual, expected in zip(lease.sources(), handles, strict=True))
                assert lease.request_id == handles[0].request_id
                assert lease.source(handles[1]).format == 'docx'
                assert lease.resolve(handles[0]) == lease.source(handles[0]).source_id
                must_fail('REQUEST_CLOSED', source.register, '2', files[0][1])
            assert source('a', CONVERSATION) == ()
            must_fail('REQUEST_CLOSED', lease.sources)
            must_fail('REQUEST_CLOSED', registry.open_local_source_authority(
                upload.request, CONVERSATION, lookup=lookup,
            ).__enter__)

    run_offline(run())


@pytest.mark.parametrize('denial', ['unauthenticated', 'wrong_conversation', 'claim', 'tools'])
def test_open_requires_original_authenticated_identity(host, app_engine, denial):
    upload = Upload(host, headers=[(b'x-user-id', b'b'), (b'x-user-clearance', b'admin')])

    async def run():
        if denial != 'unauthenticated':
            await upload.authenticate()
        if denial == 'claim':
            effective_owner.set('b')
        elif denial == 'tools':
            request_tools_enabled.set(False)
        target = 'b-conversation' if denial in {'claim', 'wrong_conversation'} else CONVERSATION
        must_fail('AUTHORIZATION_UNAVAILABLE', registry.open_local_source_authority(
            upload.request, target, lookup=lambda *_: pytest.fail('Unauthorized policy lookup'),
        ).__enter__)
        assert upload.reads == 0

    run_offline(run())


@pytest.mark.parametrize(('case', 'code'), [
    ('absent', 'AUTHORIZATION_UNAVAILABLE'), ('other_owner', 'AUTHORIZATION_UNAVAILABLE'),
    ('clearance', 'AUTHORIZATION_UNAVAILABLE'), ('untyped_clearance', 'AUTHORIZATION_UNAVAILABLE'),
    ('length', 'SOURCE_UNAVAILABLE'), ('digest', 'SOURCE_UNAVAILABLE'),
    ('qualified_name', 'SOURCE_UNAVAILABLE'), ('type', 'EXTRACTION_FAILED'),
    ('path_reference', 'SOURCE_UNAVAILABLE'), ('mutable', 'SOURCE_UNAVAILABLE'),
])
def test_registration_refuses_unapproved_metadata_or_bytes(host, app_engine, case, code):
    upload = Upload(host, headers=[(b'x-clearance', b'L3')])
    name, data = upload.files[0]
    entry = approved(name, data)
    if case == 'other_owner':
        entry = replace(entry, owner_id='b')
    elif case == 'clearance':
        entry = approved(name, data, clearance=ClearanceLevel.L3)
    elif case == 'untyped_clearance':
        entry = approved(name, data, clearance='L2')
    elif case == 'length':
        entry = replace(entry, grant=replace(entry.grant, size=len(data) + 1))
    elif case == 'digest':
        entry = approved(name, data[:-1] + bytes([data[-1] ^ 1]))
    elif case == 'qualified_name':
        entry = approved('../' + name, data)
    elif case == 'type':
        entry = approved('wrong.docx', data)
    records = {} if case == 'absent' else {'source': entry}

    async def run():
        await upload.authenticate()
        with registry.open_local_source_authority(upload.request, CONVERSATION, lookup=policy(host[0], records)) as source:
            reference = '../source' if case == 'path_reference' else 'source'
            must_fail(code, source.register, reference, bytearray(data) if case == 'mutable' else data)
            assert source('a', CONVERSATION) == ()
        assert upload.reads == 0

    run_offline(run())


@pytest.mark.parametrize('case', ['reference', 'digest', 'filename', 'generated_id'])
def test_duplicate_registration_never_replaces_the_original(host, app_engine, monkeypatch, case):
    name, data = 'check.pdf', sample_pdf()
    upload = Upload(host)
    other = save_office(sample_docx())
    records = {'one': approved(name, data)}
    if case in {'digest', 'reference'}:
        records['two'] = approved('other.pdf', data)
    else:
        records['two'] = approved(name.upper() if case == 'filename' else 'other.docx', other)
        if case == 'filename':
            # Name collisions are rejected before structural parsing.
            assert records['two'].grant.sha256 != records['one'].grant.sha256

    async def run():
        await upload.authenticate()
        with registry.open_local_source_authority(upload.request, CONVERSATION, lookup=policy(host[0], records)) as source:
            first = source.register('one', data)
            if case == 'generated_id':
                class RepeatedId:
                    hex = first.source_id
                monkeypatch.setattr(registry, 'uuid4', RepeatedId)
            original, = source('a', CONVERSATION)
            must_fail('SOURCE_UNAVAILABLE', source.register, 'one' if case == 'reference' else 'two',
                      data if case in {'reference', 'digest'} else other)
            assert source('a', CONVERSATION)[0] is original

    run_offline(run())


@pytest.mark.parametrize('bound', ['count', 'individual', 'aggregate'])
def test_registration_bounds_reject_before_decoding(host, app_engine, monkeypatch, bound):
    upload = Upload(host)
    inputs = [(f'{i}.pdf', upload.files[0][1] + b'\n' * i) for i in range(1, 6)]
    records = {str(i): approved(name, data) for i, (name, data) in enumerate(inputs)}
    if bound == 'aggregate':
        monkeypatch.setattr(registry, 'MAX_TOTAL_BYTES', sum(len(data) for _, data in inputs[:2]) - 1)
    if bound == 'individual':
        mime, _ = registry._TYPES['.pdf']
        monkeypatch.setitem(registry._TYPES, '.pdf', (mime, len(inputs[0][1]) - 1))

    async def run():
        await upload.authenticate()
        with registry.open_local_source_authority(upload.request, CONVERSATION, lookup=policy(host[0], records)) as source:
            valid = {'count': 4, 'aggregate': 1, 'individual': 0}[bound]
            for i, (_, data) in enumerate(inputs[:valid]):
                source.register(str(i), data)
            monkeypatch.setattr(registry, '_preflight', lambda *_: pytest.fail('Limit checked after parser'))
            must_fail('INPUT_LIMIT', source.register, str(valid), inputs[valid][1])
            assert len(source('a', CONVERSATION)) == valid

    run_offline(run())


@pytest.mark.parametrize('change', ['session', 'generation', 'role', 'clearance', 'withdrawn', 'revision', 'expiry'])
def test_issuance_rechecks_current_policy_and_stays_revoked(host, app_engine, monkeypatch, change):
    upload = Upload(host)
    name, data = upload.files[0]
    entry = approved(name, data)

    async def run():
        state = host[0].state
        # Keep both users eligible after a role downgrade. The change itself must invalidate the grant.
        if change == 'role':
            state.auth_store.update_user('a', role='admin')
        await upload.authenticate()
        records = {'source': entry}
        with registry.open_local_source_authority(upload.request, CONVERSATION, lookup=policy(host[0], records)) as source:
            source.register('source', data)
            assert len(source('a', CONVERSATION)) == 1
            if change == 'session':
                state.session_manager.revoke_session(host[1])
            elif change == 'generation':
                monkeypatch.setattr(app_engine._memory, 'stream_identity', lambda _: ('a', 'other-generation'))
            elif change == 'role':
                state.auth_store.update_user('a', role='operator')
            elif change == 'clearance':
                state.auth_store.update_user('a', clearance='L1')
            elif change == 'withdrawn':
                records.clear()
            elif change == 'revision':
                records['source'] = replace(entry, revision='revision-2')
            else:
                source._access.deadline = asyncio.get_running_loop().time()
            must_fail('TIMEOUT' if change == 'expiry' else 'AUTHORIZATION_UNAVAILABLE', source, 'a', CONVERSATION)
            records['source'] = entry
            assert source('a', CONVERSATION) == ()
            assert source._registrations == {}

    run_offline(run())


def test_transport_cannot_extend_registration_deadline(host, app_engine):
    upload = Upload(host)
    name, data = upload.files[0]

    async def run():
        await upload.authenticate()
        with registry.open_local_source_authority(upload.request, CONVERSATION,
                lookup=policy(host[0], {'source': approved(name, data)})) as source:
            source.register('source', data)
            source._access.deadline = now = asyncio.get_running_loop().time()
            with pytest.raises(ReasoningError) as rejected:
                async with transport.open_document_transport(upload.request, CONVERSATION,
                        source_authority=source, deadline=now + 60):
                    pytest.fail('Expired authority admitted')
            assert rejected.value.code == 'TIMEOUT'
            assert source('a', CONVERSATION) == () and upload.reads == 0

    run_offline(run())


@pytest.mark.parametrize('case', ['request', 'conversation', 'wrapped', 'revoked_cached', 'swapped'])
def test_transport_rejects_substitution(host, app_engine, case):
    files = [('source.pdf', sample_pdf()), ('another.docx', save_office(sample_docx()))]
    original = Upload(host, files)
    other = Upload(host, list(reversed(files)) if case == 'swapped' else files)
    upload = other if case in {'request', 'revoked_cached', 'swapped'} else original

    async def run():
        await upload.authenticate()
        if case in {'request', 'revoked_cached'}:
            await original.authenticate()
        lookup = policy(host[0], {str(i): approved(name, data) for i, (name, data) in enumerate(files)})
        with registry.open_local_source_authority(upload.request if case == 'swapped' else original.request,
                                                 CONVERSATION, lookup=lookup) as source:
            handles = [source.register(str(i), data) for i, (_, data) in enumerate(files)]
            cached = source('a', CONVERSATION)
            chosen = source
            if case in {'wrapped', 'revoked_cached'}:
                if case == 'revoked_cached':
                    source.revoke(handles[0])
                    await other.authenticate()
                chosen = lambda *_: cached
            target = 'b-conversation' if case == 'conversation' else CONVERSATION
            with pytest.raises(ReasoningError) as rejected:
                async with transport.open_document_transport(upload.request, target, source_authority=chosen):
                    pytest.fail('Substitution admitted')
            expected = ('REQUEST_CLOSED' if case in {'request', 'conversation'} else
                        'SOURCE_UNAVAILABLE' if case == 'swapped' else 'AUTHORIZATION_UNAVAILABLE')
            assert rejected.value.code == expected
            if case == 'swapped':
                assert source('a', CONVERSATION) == ()
            else:
                assert upload.reads == 0

    run_offline(run())


@pytest.mark.parametrize('failure', ['bytes', 'length', 'cleanup', 'cancel'])
def test_failed_admission_or_cleanup_invalidates_authority(host, app_engine, monkeypatch, failure):
    name, data = 'check.pdf', sample_pdf()
    tail = bytes([data[-1] ^ 1])
    sent = {'bytes': data[:-1] + tail, 'length': data[:-1]}.get(failure, data)
    upload = Upload(host, [(name, sent)])

    async def run():
        await upload.authenticate()
        with registry.open_local_source_authority(upload.request, CONVERSATION,
                lookup=policy(host[0], {'source': approved(name, data)}), cancelled=lambda: upload.cancelled) as source:
            source.register('source', data)
            if failure == 'cleanup':
                original = transport.DocumentTransport._close
                def broken(lease):
                    original(lease)
                    raise RuntimeError('cleanup failed')
                monkeypatch.setattr(transport.DocumentTransport, '_close', broken)
            with pytest.raises(RuntimeError if failure == 'cleanup' else ReasoningError) as rejected:
                async with transport.open_document_transport(upload.request, CONVERSATION, source_authority=source) as lease:
                    if failure == 'cancel':
                        upload.cancelled = True
                        lease.check()
            if failure != 'cleanup':
                assert rejected.value.code == ('CANCELLED' if failure == 'cancel' else 'SOURCE_UNAVAILABLE')
            assert source('a', CONVERSATION) == ()
            assert source._registrations == {} and not transport.has_active_transport()

    run_offline(run())


@pytest.mark.parametrize('mode', ['success', 'explicit_revoke', 'withdraw_after_terminal'])
def test_workflow_delivers_only_current_authorized_evidence(host, app_engine, tmp_path, caplog, monkeypatch, mode):
    upload = Upload(host)
    name, data = upload.files[0]
    records = {'source': approved(name, data)}
    memory = (tmp_path / 'memory.json').read_bytes()
    original = app_engine.run_document_task

    async def run():
        await upload.authenticate()
        with registry.open_local_source_authority(upload.request, CONVERSATION, lookup=policy(host[0], records)) as source:
            handle = source.register('source', data)

            async def observe(lease, *args, **kwargs):
                assert lease.sources()[0] is handle
                async with aclosing(original(lease, *args, **kwargs)) as events:
                    async for event in events:
                        yield event
                        if mode == 'explicit_revoke' and event['type'] == 'task_started':
                            source.revoke(handle)
                        if mode == 'withdraw_after_terminal' and event['type'] == 'task_completed':
                            records.clear()

            monkeypatch.setattr(app_engine, 'run_document_task', observe)
            events = await run_document_workflow(upload.request, CONVERSATION, PASSAGE, source_authority=source)
            final = events[-1]
            if mode == 'success':
                assert final['type'] == 'task_completed'
                assert '7.1 mm/s' in final['answer']['text']
            else:
                assert final['type'] == 'task_failed' and final['error_code'] == 'AUTHORIZATION_UNAVAILABLE'
                assert final['partial'] is None and all('answer' not in event for event in events)
            assert source('a', CONVERSATION) == ()
            assert app_engine._memory.numeric_receipt(CONVERSATION) is None

    run_offline(run())
    assert (tmp_path / 'memory.json').read_bytes() == memory
    assert not (tmp_path / 'tasks.db').exists() and '7.1 mm/s' not in caplog.text
