"""Validate durable declared-restore quarantine using disposable local SQLite."""

from contextlib import aclosing, closing
from types import SimpleNamespace
import sqlite3

import pytest
from starlette.applications import Starlette

from backend.multimodal import document_source_recovery as recovery
from backend.multimodal import document_transport
from backend.multimodal.document_source_approvals import DeploymentSourceApprovals
from backend.multimodal.document_source_authority import LocalSourceAuthority
from test_a22_async_offline import run_offline
from test_r848_document_agent_lease_offline import app_engine, denied, host  # noqa: F401
from test_r856_source_approvals_offline import deployment, establish, execute  # noqa: F401
from test_r859_cross_worker_offline import assert_no_evidence, open_worker, process_tests
from test_r860_source_restore_offline import unavailable


def maintenance_app(path):
    app = Starlette()
    app.state.settings = SimpleNamespace(tasks_db_path=path, document_source_restore_pending=False)
    return app


def test_other_app_and_startup_stay_quarantined_until_checkpoint_matches(deployment):
    approvals, _, owner, _, review = deployment
    app = owner.app
    other = maintenance_app(app.state.settings.tasks_db_path)

    async def run():
        provider = await establish(deployment)
        checkpoint = recovery.capture_source_checkpoint(other)
        with provider.open_provider(owner.request, 'a-conversation') as bound:
            arguments = ('report', 'a', 'a-conversation', review['generation'])
            assert bound.get_approved_source(*arguments) is not None
            recovery.quarantine_source_authority(other)
            unavailable(lambda: bound.get_approved_source(*arguments))
            unavailable(lambda: DeploymentSourceApprovals(app))
            unavailable(lambda: DeploymentSourceApprovals(maintenance_app(other.state.settings.tasks_db_path)))
            unavailable(lambda: recovery.validate_source_restore(other, trusted_checkpoint=None))
            unavailable(lambda: recovery.validate_source_restore(other, trusted_checkpoint='0' * 64))
            unavailable(lambda: DeploymentSourceApprovals(app))
            recovery.validate_source_restore(other, trusted_checkpoint=checkpoint)
            unavailable(lambda: bound.get_approved_source(*arguments))
        unavailable(approvals.source_provider)
        fresh = DeploymentSourceApprovals(app).source_provider()
        with fresh.open_provider(owner.request, 'a-conversation') as bound:
            assert bound.get_approved_source(*arguments) is not None

    run_offline(run())


def test_validation_cannot_clear_a_newer_declaration(deployment, monkeypatch):
    _, _, owner, _, _ = deployment
    app = owner.app
    other = maintenance_app(app.state.settings.tasks_db_path)
    original = recovery._checkpoint

    async def run():
        await establish(deployment)
        checkpoint = recovery.capture_source_checkpoint(app)
        recovery.quarantine_source_authority(app)

        def redeclare(database):
            result = original(database)
            recovery.quarantine_source_authority(other)
            return result

        with monkeypatch.context() as interleaved:
            interleaved.setattr(recovery, '_checkpoint', redeclare)
            unavailable(lambda: recovery.validate_source_restore(app, trusted_checkpoint=checkpoint))
        unavailable(lambda: DeploymentSourceApprovals(app))
        recovery.validate_source_restore(other, trusted_checkpoint=checkpoint)
        assert isinstance(DeploymentSourceApprovals(app), DeploymentSourceApprovals)

    run_offline(run())


@pytest.mark.parametrize('problem', ['missing', 'corrupt', 'writer'])
def test_missing_corrupt_or_locked_shared_state_denies_grants(deployment, problem):
    _, _, owner, _, review = deployment
    app = owner.app
    path = recovery._coordination_path(app.state.settings.tasks_db_path)

    async def run():
        provider = await establish(deployment)
        with provider.open_provider(owner.request, 'a-conversation') as bound:
            arguments = ('report', 'a', 'a-conversation', review['generation'])
            if problem == 'missing':
                path.unlink()
                unavailable(lambda: bound.get_approved_source(*arguments))
                unavailable(lambda: DeploymentSourceApprovals(app))
                assert not path.exists()
            else:
                with closing(sqlite3.connect(path, timeout=1)) as connection:
                    if problem == 'corrupt':
                        connection.execute("UPDATE source_restore_state SET generation = 'invalid'")
                        connection.commit()
                    else:
                        connection.execute('BEGIN EXCLUSIVE')
                    unavailable(lambda: bound.get_approved_source(*arguments))
                    unavailable(lambda: DeploymentSourceApprovals(app))

    run_offline(run())


def test_declaration_by_other_app_withholds_finished_evidence(deployment, app_engine, monkeypatch):
    _, _, owner, content, _ = deployment
    other = maintenance_app(owner.app.state.settings.tasks_db_path)
    original_engine = app_engine.run_document_task
    close = LocalSourceAuthority.close
    completed = []

    async def watch(lease, *args, **kwargs):
        async with aclosing(original_engine(lease, *args, **kwargs)) as stream:
            async for event in stream:
                if event['type'] == 'task_completed':
                    completed.append(True)
                yield event

    def declare_after_cleanup(authority):
        claimed = authority._claimed and authority._access is not None
        close(authority)
        if claimed:
            recovery.quarantine_source_authority(other)

    async def run():
        provider = await establish(deployment)
        monkeypatch.setattr(app_engine, 'run_document_task', watch)
        monkeypatch.setattr(LocalSourceAuthority, 'close', declare_after_cleanup)
        terminal, = await execute(provider, owner, content)
        denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
        assert completed == [True] and not document_transport.has_active_transport()

    run_offline(run())


@process_tests
def test_worker_restart_and_buffered_release_respect_shared_quarantine(tmp_path):
    database = tmp_path / 'tasks' / 'tasks.db'
    maintenance = maintenance_app(database)
    with open_worker(tmp_path) as initial:
        seed = initial.call('seed')
    checkpoint = recovery.capture_source_checkpoint(maintenance)
    with open_worker(tmp_path) as first, open_worker(tmp_path) as second:
        assert first.process.pid != second.process.pid
        assert second.call('attach', **seed) == {'state': 'attached'}
        assert second.call('run')['evidence'] is True
        assert second.call('pause_release') == {'state': 'pending'}
        assert first.call('quarantine') == {'state': 'quarantined'}
        pending = second.call('continue')
        assert pending['computed'] is True
        assert_no_evidence(pending)
        assert_no_evidence(second.call('run'))
    with open_worker(tmp_path, startup_state='quarantined') as restarted:
        assert restarted.call('attach', **seed) == {'state': 'attached'}
        blocked = restarted.call('run')
        assert_no_evidence(blocked)
        assert blocked['reads'] == 0
    unavailable(lambda: recovery.validate_source_restore(maintenance, trusted_checkpoint=None))
    recovery.validate_source_restore(maintenance, trusted_checkpoint=checkpoint)
    with open_worker(tmp_path) as recovered:
        assert recovered.call('attach', **seed) == {'state': 'attached'}
        assert recovered.call('run')['evidence'] is True
    for path in database.parent.glob('tasks.db*'):
        assert b'7.1 mm/s' not in path.read_bytes()
