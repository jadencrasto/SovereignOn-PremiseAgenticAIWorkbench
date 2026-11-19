"""Same-process FastAPI lifespans and independent SQLite connections for R8.65."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing, closing
from dataclasses import replace
import json
import os
import sqlite3
from threading import Barrier
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette

from backend.agent.deployment_documents import _RecoveryWorker, get_deployment_documents, inspect_document_worker
from backend.agent.document_workflow import _DocumentRunActivity
from backend.health.capabilities import capability_report
from recovery_custody_fixture import restore_with_checkpoint

from backend.multimodal import document_source_maintenance as maintenance
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal.document_reasoning import ReasoningError
from test_a22_async_offline import run_offline
from test_r848_document_agent_lease_offline import PASSAGE, denied
from test_r857_deployment_composition_offline import CONVERSATION, create_app, establish, prepare, tools_allowed  # noqa: F401
from test_r863_source_maintenance_offline import authority  # noqa: F401
from test_r864_windows_recovery_offline import _Ntfs, _OPERATOR


def _app(database):
    app = Starlette()
    app.state.settings = SimpleNamespace(tasks_db_path=database)
    app.state.startup_complete = False
    return app


def _state(database):
    return recovery._read_shared_state(recovery._coordination_path(database))


def _prepare(app, database):
    recovery.quarantine_source_authority(app)
    state = _state(database)
    recovery.set_recovery_maintenance(database, state.generation, active=True)
    return state


def test_barrier_blocks_worker_restart_before_authorization_sqlite_startup(create_app, monkeypatch):
    from backend import main

    app = create_app()
    database = app.state.settings.tasks_db_path

    async def run():
        async with app.router.lifespan_context(app):
            first = app.state.source_recovery_registration
            assert first.worker_id == inspect_document_worker(app).worker_id
            assert first.startup_generation == _state(database).generation
            state = _prepare(app, database)
            assert recovery.inspect_recovery_maintenance(database, state) == recovery.MaintenanceObservation(True, 1, 1)
        assert app.state.source_recovery_registration is None
        assert recovery.inspect_recovery_maintenance(database, state) == recovery.MaintenanceObservation(True, 1, 0)
        with monkeypatch.context() as scope:
            scope.setattr(main, 'AuthStore', lambda **_: pytest.fail('Maintenance started the authorization DB'))
            refused = create_app()
            with pytest.raises(ReasoningError) as failure:
                async with refused.router.lifespan_context(refused):
                    pytest.fail('New worker entered the active maintenance window')
            assert failure.value.code == 'AUTHORIZATION_UNAVAILABLE'
        recovery.set_recovery_maintenance(database, state.generation, active=False)
        cancelled = _state(database)
        assert cancelled.pending and cancelled.generation != state.generation
        assert recovery.inspect_recovery_maintenance(database, cancelled) == recovery.MaintenanceObservation(False, 0, 0)
        restarted = create_app()
        async with restarted.router.lifespan_context(restarted):
            registration = restarted.state.source_recovery_registration
            assert registration.worker_id != first.worker_id
            assert registration.startup_generation == cancelled.generation
            with pytest.raises(ReasoningError):
                get_deployment_documents(restarted)

    run_offline(run())


@pytest.mark.parametrize('forgery', ['other_app', 'nonce', 'startup_generation'])
def test_registration_rejects_forged_owner_or_stale_startup_generation(authority, forgery):
    app, database = authority
    app.state.startup_complete = False

    async def run():
        worker = recovery.register_recovery_worker(app)
        state = _prepare(app, database)
        other = _app(database)
        try:
            if forgery == 'other_app':
                other.state.source_recovery_registration = worker
                with pytest.raises(ReasoningError):
                    recovery.acknowledge_recovery_worker_shutdown(other, worker)
            else:
                field = 'owner_nonce' if forgery == 'nonce' else 'startup_generation'
                forged = replace(worker, **{field: '0' * 32})
                app.state.source_recovery_registration = forged
                with pytest.raises(ReasoningError):
                    recovery.acknowledge_recovery_worker_shutdown(app, forged)
        finally:
            app.state.source_recovery_registration = worker
        assert recovery.inspect_recovery_maintenance(database, state).awaiting_shutdown == 1
        recovery.acknowledge_recovery_worker_shutdown(app, worker)
        with pytest.raises(ReasoningError):
            recovery.acknowledge_recovery_worker_shutdown(app, worker)
        assert recovery.inspect_recovery_maintenance(database, state).awaiting_shutdown == 0

    run_offline(run())


@pytest.mark.parametrize('undrained', ['operation', 'buffered', 'connection', 'close_failed', 'missing_observation'])
def test_worker_cannot_acknowledge_before_observed_scope_drains(authority, undrained):
    app, database = authority
    app.state.startup_complete = False

    async def run():
        worker = recovery.register_recovery_worker(app)
        state = _prepare(app, database)
        observation = _RecoveryWorker(asyncio.get_running_loop(), worker_id=worker.worker_id, closed=True)
        app.state.document_recovery_worker = observation
        if undrained == 'operation':
            observation.catalog_operations = 1
        elif undrained == 'buffered':
            observation.running.add(_DocumentRunActivity(buffered=True))
        elif undrained == 'connection':
            observation.sqlite.open_connections = 1
        elif undrained == 'close_failed':
            observation.sqlite.close_failed = True
        else:
            app.state.source_recovery_documents_installed = True
            app.state.document_recovery_worker = None
        with pytest.raises(ReasoningError):
            recovery.acknowledge_recovery_worker_shutdown(app, worker)
        assert recovery.inspect_recovery_maintenance(database, state).awaiting_shutdown == 1

    run_offline(run())


def test_missing_crash_ack_survives_coordinator_reopen_and_barrier_cancel(authority):
    app, database = authority

    async def run():
        crashed_app = _app(database)
        recovery.register_recovery_worker(crashed_app)
        # No completed lifespan shutdown runs after the simulated crash.
        state = _prepare(app, database)
        with pytest.raises(ReasoningError):
            recovery.register_recovery_worker(_app(database))
        assert recovery.inspect_recovery_maintenance(database, _state(database)).awaiting_shutdown == 1
        recovery.set_recovery_maintenance(database, state.generation, active=False)
        recovery.set_recovery_maintenance(database, _state(database).generation, active=True)
        assert recovery.inspect_recovery_maintenance(database, _state(database)) == recovery.MaintenanceObservation(True, 1, 1)

    run_offline(run())


def test_concurrent_declaration_commits_once_and_preserves_quarantine(authority):
    app, database = authority
    recovery.quarantine_source_authority(app)
    state = _state(database)
    barrier = Barrier(2)

    def declare():
        barrier.wait(timeout=5)
        try:
            recovery.set_recovery_maintenance(database, state.generation, active=True)
            return 'committed'
        except ReasoningError:
            return 'blocked'

    with ThreadPoolExecutor(max_workers=2) as pool:
        workers = [pool.submit(declare) for _ in range(2)]
        assert sorted(worker.result(timeout=5) for worker in workers) == ['blocked', 'committed']
    assert _state(database) == state
    with pytest.raises(ReasoningError):
        recovery.set_recovery_maintenance(database, '0' * 32, active=False)
    with pytest.raises(ReasoningError):
        recovery.quarantine_source_authority(app)
    assert recovery.inspect_recovery_maintenance(database, state).active


def test_checkpoint_validation_rechecks_barrier_at_commit(authority, monkeypatch):
    app, database = authority
    checkpoint = recovery.capture_source_checkpoint(app)
    recovery.quarantine_source_authority(app)
    state = _state(database)
    compute = recovery._scan_checkpoint

    def prepare_mid_validation(connection, deadline):
        result = compute(connection, deadline)
        recovery.set_recovery_maintenance(database, state.generation, active=True)
        return result

    with monkeypatch.context() as scope:
        scope.setattr(recovery, '_scan_checkpoint', prepare_mid_validation)
        with pytest.raises(ReasoningError):
            restore_with_checkpoint(app, trusted_checkpoint=checkpoint)
    assert _state(database) == state
    with pytest.raises(ReasoningError):
        restore_with_checkpoint(app, trusted_checkpoint=checkpoint)
    recovery.set_recovery_maintenance(database, state.generation, active=False)
    restore_with_checkpoint(app, trusted_checkpoint=checkpoint)
    assert not _state(database).pending and _state(database).generation != state.generation


def test_partial_maintenance_schema_blocks_startup_and_diagnostics(authority):
    app, database = authority
    state = _prepare(app, database)
    coordinator = recovery._coordination_path(database)
    with closing(sqlite3.connect(coordinator)) as connection:
        connection.execute('DROP TABLE source_recovery_workers')
    with pytest.raises(ReasoningError):
        recovery.inspect_recovery_maintenance(database, state)

    async def run():
        with pytest.raises(ReasoningError):
            recovery.register_recovery_worker(_app(database))

    run_offline(run())
    with closing(sqlite3.connect(coordinator)) as connection:
        assert connection.execute('SELECT generation FROM source_recovery_barrier').fetchone() == (state.generation,)


def test_preflight_stays_blocked_with_external_reader_after_known_worker_ack(authority, tmp_path):
    app, database = authority
    app.state.startup_complete = False
    backup_dir = tmp_path / 'bundle'
    maintenance._backup(database, backup_dir)
    checkpoint = tmp_path / 'proof'
    checkpoint.write_text(recovery.capture_source_checkpoint(app), encoding='ascii')
    coordinator = recovery._coordination_path(database)

    async def run():
        worker = recovery.register_recovery_worker(app)
        state = _prepare(app, database)
        args = (database, backup_dir / maintenance._BACKUP, checkpoint, state.generation)
        assert 'WORKER_SHUTDOWN_ACK_MISSING' in maintenance._preflight(*args).blockers
        recovery.acknowledge_recovery_worker_shutdown(app, worker)
        original = database.read_bytes(), coordinator.read_bytes()
        with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)) as unmanaged:
            unmanaged.execute('BEGIN')
            unmanaged.execute('SELECT COUNT(*) FROM sqlite_schema').fetchone()
            result = maintenance._preflight(*args)
            assert 'REGISTERED_WORKERS_ACKNOWLEDGED' in result.checks
            assert result.status == 'BLOCKED'
            assert 'SUPERVISOR_RESTART_UNVERIFIED' in result.blockers
            assert 'SQLITE_CLIENT_INVENTORY_UNVERIFIED' in result.blockers
        assert original == (database.read_bytes(), coordinator.read_bytes())

    run_offline(run())


def test_pending_buffered_evidence_is_withheld_before_worker_shutdown_ack(create_app, tools_allowed, monkeypatch):
    app = create_app()
    database = app.state.settings.tasks_db_path
    routes = tuple(app.routes)

    async def run():
        async with app.router.lifespan_context(app):
            documents = get_deployment_documents(app)
            owner, reviewer, decisions = await prepare(app)
            await establish(documents, owner, reviewer, decisions)
            execute = app.state.engine.run_document_task

            async def interrupt(lease, *args, **kwargs):
                async with aclosing(execute(lease, *args, **kwargs)) as stream:
                    async for event in stream:
                        yield event
                        if event['type'] == 'task_completed':
                            assert inspect_document_worker(app).pending_results == 1
                            state = _prepare(app, database)
                            assert recovery.inspect_recovery_maintenance(database, state).awaiting_shutdown == 1

            monkeypatch.setattr(app.state.engine, 'run_document_task', interrupt)
            history = app.state.settings.conversation_memory_path
            before = history.read_bytes()
            for _ in range(2):
                terminal, = await documents.run(
                    owner.request, CONVERSATION, PASSAGE, sources=(('report', owner.files[0][1]),),
                )
                denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
            assert history.read_bytes() == before
            assert recovery.inspect_recovery_maintenance(database, _state(database)).awaiting_shutdown == 1
            capabilities = capability_report(
                environment='production', ready=True, planning=True, persistence=True, probe_allowed=False,
                configured_models=dict.fromkeys(('default_chat', 'vision', 'image_chat')), observation=None,
            )
            assert capabilities['document_intelligence']['direct_chat'] == 'blocked'
            assert capabilities['document_intelligence']['artifact_publication'] == 'blocked'
            assert capabilities['capabilities']['document_rag']['state'] == 'blocked'
            assert capabilities['deployment_verified'] is False and tuple(app.routes) == routes
        assert recovery.inspect_recovery_maintenance(database, _state(database)).awaiting_shutdown == 0

    run_offline(run())


@pytest.mark.skipif(os.name != 'nt', reason='Windows path guard with simulated native calls')
def test_operator_commands_pin_only_coordinator_for_write(authority, monkeypatch, capsys):
    app, database = authority
    recovery.quarantine_source_authority(app)
    state = _state(database)

    class ControlNtfs(_Ntfs):
        def open_child(self, parent, name, directory, *, inspect_acl, share_write=False):
            if not directory:
                assert name == recovery._coordination_path(database).name
                opens.append(share_write)
            return super().open_child(parent, name, directory, inspect_acl=inspect_acl)

    opens = []
    api = ControlNtfs()
    monkeypatch.setattr(maintenance, 'WindowsKeyStoreNative', lambda: api)
    monkeypatch.setattr(maintenance, '_local_operator', lambda client: _OPERATOR)
    flags = ['--database', str(database), '--expected-generation', state.generation]
    assert maintenance.main(['prepare-maintenance', *flags]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared['status'] == 'MAINTENANCE_PREPARED' and prepared['restore_authorized'] is False
    assert prepared['sqlite_clients']['external_clients'] == 'UNKNOWN'
    assert maintenance.main(['status', '--database', str(database)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status['maintenance']['active'] and not status['supervisor_verified']
    assert maintenance.main(['cancel-maintenance', *flags]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'MAINTENANCE_CANCELLED', 'restore_authorized': False}
    cancelled = _state(database)
    assert cancelled.pending and cancelled.generation != state.generation and not api.handles
    assert opens == [True, False, True]
