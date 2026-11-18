"""Exercise deployment quarantine with disposable WAL-backed SQLite files."""

from contextlib import aclosing, closing
import sqlite3

import pytest

from backend.multimodal import document_transport
from backend.agent.deployment_documents import get_deployment_documents
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_source_approvals import DeploymentSourceApprovals
from backend.multimodal.document_source_authority import LocalSourceAuthority
from backend.multimodal.document_source_recovery import (
    capture_source_checkpoint, quarantine_source_authority, validate_source_restore,
)
from backend.security.provenance_foundation import document_content_allowed
from test_a22_async_offline import run_offline
from test_r848_document_agent_lease_offline import PASSAGE, Upload, app_engine, denied, host  # noqa: F401
from test_r856_source_approvals_offline import deployment, establish, execute  # noqa: F401
from test_r857_deployment_composition_offline import (
    CONVERSATION, create_app, establish as establish_deployment, prepare, tools_allowed,  # noqa: F401
)


def backup(source, destination):
    with closing(sqlite3.connect(source)) as original, closing(sqlite3.connect(destination)) as copy:
        original.backup(copy)


def unavailable(operation):
    with pytest.raises(ReasoningError) as rejected:
        operation()
    assert rejected.value.code == 'AUTHORIZATION_UNAVAILABLE'


@pytest.mark.parametrize('withdrawn', [False, True])
def test_normal_reopen_and_current_restore_preserve_authority_state(deployment, tmp_path, withdrawn):
    approvals, reviewer, owner, content, _ = deployment
    app = owner.app
    database = app.state.settings.tasks_db_path
    known_current = tmp_path / 'known-current.db'

    async def assert_authority():
        await owner.authenticate()
        provider = DeploymentSourceApprovals(app).source_provider()
        events = await execute(provider, owner, content)
        if withdrawn:
            terminal, = events
            denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
            assert owner.reads == 0
        else:
            assert events[-1]['type'] == 'task_completed', events[-1]
            assert events[-1]['answer']['sources'][0]['filename'] == 'report.pdf'

    async def run():
        await establish(deployment)
        checkpoint = capture_source_checkpoint(app)
        backup(database, known_current)
        if withdrawn:
            await reviewer.authenticate()
            assert approvals.withdraw(reviewer.request, 'report')
            latest = capture_source_checkpoint(app)
            assert latest != checkpoint
            checkpoint = latest
            backup(database, known_current)
        # Reopening metadata connections requires no restore declaration.
        if withdrawn:
            await assert_authority()
        quarantine_source_authority(app)
        backup(known_current, database)
        validate_source_restore(app, trusted_checkpoint=checkpoint)
        await assert_authority()

    run_offline(run())
    assert b'7.1 mm/s' not in (tmp_path / 'memory.json').read_bytes()
    for path in tmp_path.glob('auth.db*'):
        assert b'7.1 mm/s' not in path.read_bytes() and content not in path.read_bytes()
    assert not document_content_allowed()


def test_retained_post_withdrawal_checkpoint_rejects_pre_withdrawal_backup(deployment, tmp_path):
    approvals, reviewer, owner, _, review = deployment
    app = owner.app
    database = app.state.settings.tasks_db_path
    older = tmp_path / 'pre-withdrawal.db'

    async def run():
        old_provider = await establish(deployment)
        backup(database, older)
        await reviewer.authenticate()
        assert approvals.withdraw(reviewer.request, 'report')
        checkpoint = capture_source_checkpoint(app)
        quarantine_source_authority(app)
        backup(older, database)
        # A valid SQLite backup can restore both active rows with their original generation.
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                'SELECT generation, withdrawn_at FROM document_source_approvals',
            ).fetchone() == (review['generation'], None)
            assert connection.execute('SELECT withdrawn_us FROM approved_document_sources').fetchone() == (None,)
        unavailable(lambda: validate_source_restore(app, trusted_checkpoint=checkpoint))
        unavailable(lambda: DeploymentSourceApprovals(app))
        unavailable(lambda: capture_source_checkpoint(app))
        await owner.authenticate()
        with pytest.raises(ReasoningError) as denied_provider:
            with old_provider.open_provider(owner.request, 'a-conversation'):
                pytest.fail('Pre-restore provider reopened')
        assert denied_provider.value.code == 'AUTHORIZATION_UNAVAILABLE'
        assert owner.reads == 0

    run_offline(run())


@pytest.mark.parametrize('state', ['missing_checkpoint', 'invalid_checkpoint', 'missing_table', 'corrupt_row', 'schema'])
def test_restore_declaration_fails_closed_on_missing_or_inconsistent_metadata(deployment, state):
    _, _, owner, _, _ = deployment
    app = owner.app

    async def run():
        await establish(deployment)
        checkpoint = capture_source_checkpoint(app)
        app.state.settings.document_source_restore_pending = True
        unavailable(lambda: DeploymentSourceApprovals(app))
        if state == 'missing_checkpoint':
            checkpoint = None
        elif state == 'invalid_checkpoint':
            checkpoint = 'not-a-checkpoint'
        else:
            with closing(sqlite3.connect(app.state.settings.tasks_db_path)) as connection, connection:
                if state == 'missing_table':
                    connection.execute('DROP TABLE approved_document_sources')
                elif state == 'corrupt_row':
                    connection.execute('UPDATE document_source_approvals SET sha256 = ?', (b'invalid',))
                else:
                    connection.execute('CREATE INDEX unexpected_idx ON approved_document_sources (expires_us)')
        unavailable(lambda: validate_source_restore(app, trusted_checkpoint=checkpoint))
        unavailable(lambda: DeploymentSourceApprovals(app))
        if state == 'missing_table':
            with closing(sqlite3.connect(app.state.settings.tasks_db_path)) as connection:
                assert connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name = 'approved_document_sources'",
                ).fetchone() is None

    run_offline(run())


def test_successful_validation_keeps_all_pre_restore_instances_invalid_and_checks_generation(deployment):
    approvals, _, owner, content, review = deployment
    app = owner.app

    async def run():
        first = await establish(deployment)
        second = DeploymentSourceApprovals(app).source_provider()
        checkpoint = capture_source_checkpoint(app)
        with first.open_provider(owner.request, 'a-conversation') as admitted:
            assert admitted.get_approved_source('report', 'a', 'a-conversation', review['generation']) is not None
            quarantine_source_authority(app)
            app.state.startup_complete = True
            unavailable(lambda: validate_source_restore(app, trusted_checkpoint=checkpoint))
            app.state.startup_complete = False
            validate_source_restore(app, trusted_checkpoint=checkpoint)
            unavailable(lambda: admitted.get_approved_source('report', 'a', 'a-conversation', review['generation']))
        unavailable(approvals.source_provider)
        unavailable(lambda: second.withdraw(owner.request, 'a-conversation', 'report'))
        fresh = DeploymentSourceApprovals(app).source_provider()
        app.state.engine._memory._security_registry.revoke('a-conversation')
        with pytest.raises(ReasoningError) as denied_provider:
            await execute(fresh, owner, content)
        assert denied_provider.value.code == 'AUTHORIZATION_UNAVAILABLE'
        assert owner.reads == 0

    run_offline(run())


def test_quarantine_during_cleanup_withholds_computed_evidence(deployment, app_engine, monkeypatch, tmp_path):
    _, _, owner, content, _ = deployment
    app = owner.app
    engine_run = app_engine.run_document_task
    close = LocalSourceAuthority.close
    completions = []

    async def watch(lease, *args, **kwargs):
        async with aclosing(engine_run(lease, *args, **kwargs)) as stream:
            async for event in stream:
                if event['type'] == 'task_completed':
                    completions.append(True)
                yield event

    def closing_issuer(authority):
        claimed = authority._claimed and authority._access is not None
        close(authority)
        if claimed:
            quarantine_source_authority(app)

    async def run():
        provider = await establish(deployment)
        monkeypatch.setattr(app_engine, 'run_document_task', watch)
        monkeypatch.setattr(LocalSourceAuthority, 'close', closing_issuer)
        terminal, = await execute(provider, owner, content)
        denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
        assert completions == [True] and not document_transport.has_active_transport()

    run_offline(run())
    assert b'7.1 mm/s' not in (tmp_path / 'memory.json').read_bytes()


def test_declared_restore_blocks_lifespan_source_authority_until_offline_validation(create_app, tools_allowed):
    original = create_app()
    restarted = create_app()

    async def run():
        async with original.router.lifespan_context(original):
            owner, reviewer, decisions = await prepare(original)
            documents = get_deployment_documents(original)
            await establish_deployment(documents, owner, reviewer, decisions)
            await reviewer.authenticate()
            assert documents.withdraw_approval(reviewer.request, 'report')
        checkpoint = capture_source_checkpoint(original)

        restarted.state.settings.document_source_restore_pending = True
        async with restarted.router.lifespan_context(restarted):
            assert restarted.state.startup_complete
            unavailable(lambda: get_deployment_documents(restarted))
            unavailable(lambda: validate_source_restore(restarted, trusted_checkpoint=checkpoint))
        validate_source_restore(restarted, trusted_checkpoint=checkpoint)
        async with restarted.router.lifespan_context(restarted):
            upload = Upload((restarted, owner.token), owner.files)
            await upload.authenticate()
            terminal, = await get_deployment_documents(restarted).run(
                upload.request, CONVERSATION, PASSAGE, sources=(('report', owner.files[0][1]),),
            )
            denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
            assert upload.reads == 0

    run_offline(run())
