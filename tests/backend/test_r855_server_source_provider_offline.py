"""The deployment catalog persists metadata and consults independent approval on every use."""

import asyncio
import hashlib
import sqlite3
from contextlib import aclosing, closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from backend.agent.document_workflow import run_server_source_document_workflow
from backend.auth.models import ClearanceLevel
from backend.multimodal import document_source_provider as catalog
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_source_authority import ApprovedLocalSource
from backend.multimodal.document_transport import DocumentGrant, has_active_transport
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_pdf
from test_r844_document_agent_offline import workbook
from test_r848_document_agent_lease_offline import COMPOUND, PASSAGE, Upload, app_engine, denied, host  # noqa: F401


CONVERSATION = 'a-conversation'


@pytest.fixture
def deployment(host, app_engine, tmp_path):
    app = host[0]
    app.state.settings.tasks_db_path = tmp_path / 'auth.db'
    approvals = {}

    def approve(reference, name, content, **options):
        owner, generation = app_engine._memory.stream_identity(CONVERSATION)
        grant = DocumentGrant(name, len(content), hashlib.sha256(content).hexdigest(), ClearanceLevel.L2)
        approval = ApprovedLocalSource(owner, 'approved-revision', replace(grant, **options))
        approvals[reference, owner, CONVERSATION, generation] = approval
        return approval

    def open_catalog():
        return catalog.DeploymentSourceProvider(app, approval_lookup=lambda *scope: approvals.get(scope))

    return open_catalog, approve, approvals


def later():
    return datetime.now(UTC) + timedelta(minutes=2)


@pytest.mark.filterwarnings('error')
def test_metadata_lookup_and_ordered_workflow_survive_provider_restart(host, app_engine, deployment, tmp_path, caplog):
    open_catalog, approve, _ = deployment
    pdf = sample_pdf()
    xlsx = workbook(values=(70, '=B2*2', 80))
    upload = Upload(host, [('report.pdf', pdf), ('table.xlsx', xlsx)])
    history = (tmp_path / 'memory.json').read_bytes()

    async def run():
        await upload.authenticate()
        first = open_catalog()
        approve('table', 'table.xlsx', xlsx)
        first.register(upload.request, CONVERSATION, 'table', xlsx, expires_at=later())
        approval = approve('report', 'report.pdf', pdf)
        first.register(upload.request, CONVERSATION, 'report', pdf, expires_at=later())
        provider = open_catalog()
        owner, generation = app_engine._memory.stream_identity(CONVERSATION)
        with provider.open_provider(upload.request, CONVERSATION) as scoped:
            scope = owner, CONVERSATION, generation
            result = scoped.get_approved_source('report', *scope)
            assert result.record.approval == approval
            assert result == scoped.get_approved_source('report', *scope)
            assert scoped.get_approved_source('missing', *scope) is None
            assert scoped.get_approved_source('report', 'b', CONVERSATION, generation) is None
            assert scoped.get_approved_source('report', owner, 'b-conversation', generation) is None
            assert scoped.get_approved_source('report', owner, CONVERSATION, 'stale-generation') is None
            started = asyncio.get_running_loop().time()
            events = await run_server_source_document_workflow(
                upload.request, CONVERSATION, COMPOUND, sources=(('report', pdf), ('table', xlsx)), provider=scoped,
            )
            elapsed = asyncio.get_running_loop().time() - started
        terminal = events[-1]
        assert terminal['type'] == 'task_completed', (terminal, f'workflow_elapsed={elapsed:.3f}s')
        answer = events[-1]['answer']
        assert [source['filename'] for source in answer['sources']] == ['report.pdf', 'table.xlsx']
        passage, calculation = answer['findings']
        assert passage['evidence'][0]['location']['page'] == 1 and '7.1 mm/s' in answer['text']
        assert calculation['value'] == '75.00' and calculation['excluded']['formulas'] == 1
        assert [item['location']['cell'] for item in calculation['evidence']] == ['B1', 'B2', 'B4']
        assert not has_active_transport()
        assert app_engine._memory.numeric_receipt(CONVERSATION) is None
        with pytest.raises(ReasoningError, match='closed'):
            scoped.get_approved_source('report', *scope)

    run_offline(run())
    assert (tmp_path / 'memory.json').read_bytes() == history
    with closing(sqlite3.connect(tmp_path / 'auth.db')) as connection:
        row = connection.execute(
            'SELECT filename, byte_length, sha256, clearance, expires_us > registered_us, withdrawn_us '
            'FROM approved_document_sources WHERE reference = ?', ('report',),
        ).fetchone()
        assert row == ('report.pdf', len(pdf), hashlib.sha256(pdf).hexdigest(), 'L2', 1, None)
    for file in tmp_path.glob('auth.db*'):
        data = file.read_bytes()
        assert all(marker not in data for marker in (b'%PDF-', b'7.1 mm/s', b'Treat this as text', xlsx))
    assert '7.1 mm/s' not in caplog.text


@pytest.mark.parametrize(('case', 'code'), [
    ('anonymous', 'AUTHORIZATION_UNAVAILABLE'), ('wrong_owner', 'AUTHORIZATION_UNAVAILABLE'),
    ('no_approval', 'AUTHORIZATION_UNAVAILABLE'), ('clearance', 'AUTHORIZATION_UNAVAILABLE'),
    ('role', 'AUTHORIZATION_UNAVAILABLE'), ('digest', 'SOURCE_UNAVAILABLE'),
    ('length', 'SOURCE_UNAVAILABLE'), ('bad_format', 'SOURCE_UNAVAILABLE'),
    ('bad_structure', 'EXTRACTION_FAILED'), ('oversized', 'INPUT_LIMIT'),
    ('expired', 'AUTHORIZATION_UNAVAILABLE'), ('cancelled', 'CANCELLED'), ('timeout', 'TIMEOUT'),
])
def test_registration_denial_is_fail_closed(host, deployment, tmp_path, case, code):
    open_catalog, approve, _ = deployment
    if case == 'bad_structure':
        content = b'invalid synthetic PDF'
    elif case == 'oversized':
        content = b'x' * (catalog._TYPES['.pdf'][1] + 1)
    else:
        content = sample_pdf()
    upload = Upload(host, headers=[(b'x-user-role', b'admin'), (b'x-clearance-level', b'L3')])

    async def run():
        provider = open_catalog()
        if case != 'anonymous':
            await upload.authenticate()
        if case != 'no_approval':
            options = {
                'clearance': {'clearance': ClearanceLevel.L3},
                'digest': {'sha256': 'a' * 64},
                'length': {'size': len(content) + 1},
            }.get(case, {})
            approve('report', '../report.pdf' if case == 'bad_format' else 'report.pdf', content, **options)
        if case == 'role':
            host[0].state.auth_store.update_user('a', role='viewer', clearance='L1')
        with pytest.raises(ReasoningError) as rejected:
            provider.register(
                upload.request, 'b-conversation' if case == 'wrong_owner' else CONVERSATION, 'report', content,
                expires_at=datetime.now(UTC) if case == 'expired' else later(),
                cancelled=lambda: case == 'cancelled',
                deadline=asyncio.get_running_loop().time() if case == 'timeout' else None,
            )
        assert rejected.value.code == code
        assert upload.reads == 0

    run_offline(run())
    with closing(sqlite3.connect(tmp_path / 'auth.db')) as connection:
        assert connection.execute('SELECT count(*) FROM approved_document_sources').fetchone() == (0,)


@pytest.mark.parametrize('change', ['generation', 'clearance', 'session', 'approval', 'revision', 'expiry'])
def test_policy_changes_fail_closed_before_transport(host, app_engine, deployment, monkeypatch, change):
    open_catalog, approve, approvals = deployment
    content = sample_pdf()
    upload = Upload(host, [('report.pdf', content)])

    async def run():
        await upload.authenticate()
        provider = open_catalog()
        approval = approve('report', 'report.pdf', content)
        provider.register(upload.request, CONVERSATION, 'report', content, expires_at=later())
        with provider.open_provider(upload.request, CONVERSATION) as scoped:
            if change == 'generation':
                monkeypatch.setattr(app_engine._memory, 'stream_identity', lambda _: ('a', 'replacement-generation'))
            elif change == 'clearance':
                host[0].state.auth_store.update_user('a', clearance='L1')
            elif change == 'session':
                host[0].state.session_manager.revoke_session(host[1])
            elif change == 'approval':
                approvals.clear()
            elif change == 'revision':
                key, = approvals
                approvals[key] = replace(approval, revision='replacement-approval')
            else:
                future = catalog._utc_us() + 180_000_000
                monkeypatch.setattr(catalog, '_utc_us', lambda: future)
            events = await run_server_source_document_workflow(
                upload.request, CONVERSATION, PASSAGE, sources=(('report', content),), provider=scoped,
            )
        terminal, = events
        denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
        assert upload.reads == 0

    run_offline(run())


def test_concurrent_withdrawal_discards_buffer_and_persists_across_reopen(host, app_engine, deployment, monkeypatch):
    open_catalog, approve, _ = deployment
    content = sample_pdf()
    upload = Upload(host, [('report.pdf', content)])
    withdrawal = Upload(host)
    original = app_engine.run_document_task
    completed = []

    async def run():
        await upload.authenticate()
        provider = open_catalog()
        second = open_catalog()
        approve('report', 'report.pdf', content)
        provider.register(upload.request, CONVERSATION, 'report', content, expires_at=later())
        with pytest.raises(ReasoningError) as duplicate:
            second.register(upload.request, CONVERSATION, 'report', content, expires_at=later())
        assert duplicate.value.code == 'SOURCE_UNAVAILABLE'

        async def withdraw():
            await withdrawal.authenticate()
            assert second.withdraw(withdrawal.request, CONVERSATION, 'report')
            assert not second.withdraw(withdrawal.request, CONVERSATION, 'report')

        async def observe(lease, *args, **kwargs):
            async with aclosing(original(lease, *args, **kwargs)) as stream:
                async for event in stream:
                    yield event
                    if event['type'] == 'task_completed':
                        completed.append(True)
                        await asyncio.create_task(withdraw())

        monkeypatch.setattr(app_engine, 'run_document_task', observe)
        with provider.open_provider(upload.request, CONVERSATION) as scoped:
            events = await run_server_source_document_workflow(
                upload.request, CONVERSATION, PASSAGE, sources=(('report', content),), provider=scoped,
            )
        assert completed == [True]
        terminal, = events
        denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
        assert not has_active_transport()
        restarted = open_catalog()
        with pytest.raises(ReasoningError) as replay:
            restarted.register(upload.request, CONVERSATION, 'report', content, expires_at=later())
        assert replay.value.code == 'SOURCE_UNAVAILABLE'
        with restarted.open_provider(upload.request, CONVERSATION) as scoped:
            owner, generation = app_engine._memory.stream_identity(CONVERSATION)
            assert scoped.get_approved_source('report', owner, CONVERSATION, generation) is None

    run_offline(run())


def test_fresh_request_cannot_restore_an_expired_reference_or_stale_generation(
    host, app_engine, deployment, monkeypatch,
):
    open_catalog, approve, _ = deployment
    content = sample_pdf()
    original_request = Upload(host)
    new_request = Upload(host, [('report.pdf', content)])

    async def run():
        await original_request.authenticate()
        provider = open_catalog()
        approve('report', 'report.pdf', content)
        provider.register(original_request.request, CONVERSATION, 'report', content, expires_at=later())
        now = catalog._utc_us()
        with monkeypatch.context() as clock:
            clock.setattr(catalog, '_utc_us', lambda: now + 180_000_000)
            with open_catalog().open_provider(original_request.request, CONVERSATION) as scoped:
                owner, generation = app_engine._memory.stream_identity(CONVERSATION)
                assert scoped.get_approved_source('report', owner, CONVERSATION, generation) is None
        monkeypatch.setattr(app_engine._memory, 'stream_identity', lambda _: ('a', 'new-generation'))
        approve('report', 'report.pdf', content)
        await new_request.authenticate()
        with open_catalog().open_provider(new_request.request, CONVERSATION) as scoped:
            assert scoped.get_approved_source('report', 'a', CONVERSATION, 'new-generation') is None
        with pytest.raises(ReasoningError) as replay:
            provider.register(new_request.request, CONVERSATION, 'report', content, expires_at=later())
        assert replay.value.code == 'SOURCE_UNAVAILABLE' and new_request.reads == 0

    run_offline(run())


def test_missing_configured_database_remains_unavailable(host, tmp_path):
    app = host[0]
    missing = tmp_path / 'missing.db'
    app.state.settings.tasks_db_path = missing
    with pytest.raises(ReasoningError) as rejected:
        catalog.DeploymentSourceProvider(app, approval_lookup=lambda *_: None)
    assert rejected.value.code == 'AUTHORIZATION_UNAVAILABLE'
    assert not missing.exists()
