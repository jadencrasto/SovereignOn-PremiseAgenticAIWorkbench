"""SQLite coordination with a simulated, handle-owning process boundary."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
import sqlite3
from threading import Barrier
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette

from backend.health.capabilities import capability_report
from backend.multimodal import document_recovery_supervisor as supervisor
from backend.multimodal import document_source_maintenance as maintenance
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal.document_reasoning import ReasoningError
from backend.security.provenance_foundation import document_content_allowed
from backend.security.provenance_windows_native import WindowsKeyStoreNative
from test_a22_async_offline import run_offline
from test_r857_deployment_composition_offline import create_app, create_sessions  # noqa: F401
from test_r863_source_maintenance_offline import authority  # noqa: F401


class Boundary:
    def __init__(self, database, owner):
        self.view = supervisor.SupervisorSnapshot(database, owner, True, True, ())
        self.admissions = []
        self.signals = []
        self.on_shutdown = lambda _: None

    def observe(self):
        return self.view

    def launch(self, admission, *, timeout_seconds):
        assert timeout_seconds == 5
        self.admissions.append(admission)
        process = supervisor.ProcessIdentity(200 + len(self.admissions), 9000 + len(self.admissions))
        self.view = replace(self.view, workers=(*self.view.workers, supervisor.OwnedWorker(process, True)))
        return process

    def shutdown(self, identity, *, timeout_seconds):
        assert 0 < timeout_seconds <= 30
        self.signals.append(identity)
        self.on_shutdown(identity)
        self.exit(identity)

    def exit(self, identity):
        self.view = replace(self.view, workers=tuple(
            replace(worker, running=False) if worker.identity == identity else worker for worker in self.view.workers
        ))


@pytest.fixture
def platform(monkeypatch):
    owner = supervisor.ProcessIdentity(100, 8000)
    platform = SimpleNamespace(owner=owner, current=owner, live=True, authorized=True)

    def authorize(database):
        if not platform.authorized:
            raise maintenance.MaintenanceError('LOCAL_OPERATOR_REQUIRED')

    monkeypatch.setattr(supervisor, '_authorize_operator', authorize)
    monkeypatch.setattr(supervisor, '_process_identity', lambda: platform.current)
    monkeypatch.setattr(supervisor, '_live_owner', lambda candidate: platform.live and candidate == owner)
    monkeypatch.setattr(recovery, 'os', SimpleNamespace(getpid=lambda: platform.current.pid))
    return platform


@pytest.fixture
def controlled(authority, platform):
    app, database = authority
    boundary = Boundary(database, platform.owner)
    owner = supervisor.DeploymentSupervisor(database, boundary)
    return app, database, boundary, owner


def state(database):
    return recovery._read_shared_state(recovery._coordination_path(database))


def prepare(app, database):
    recovery.quarantine_source_authority(app)
    pending = state(database)
    recovery.set_recovery_maintenance(database, pending.generation, active=True)
    return pending


def worker_app(database, admission=None):
    worker = Starlette()
    worker.state.settings = SimpleNamespace(tasks_db_path=database)
    worker.state.startup_complete = False
    if admission is not None:
        worker.state.source_supervisor_admission = admission
    return worker


def admit(database, boundary, owner, platform):
    child = owner.start_worker()
    worker = worker_app(database, boundary.admissions[-1])
    platform.current = child
    try:
        registration = recovery.register_recovery_worker(worker)
    finally:
        platform.current = platform.owner
    return child, worker, registration


def test_trusted_supervisor_empty_owner_and_exclusive_registration(controlled):
    _, database, boundary, _ = controlled
    observed = recovery.inspect_recovery_maintenance(database, state(database))
    assert observed.supervisor_registered and observed.supervisor_workers == 0 and not observed.active
    with pytest.raises(ReasoningError):
        supervisor.DeploymentSupervisor(database, boundary)


def test_copied_coordinator_hash_does_not_confer_supervisor_ownership(controlled):
    _, database, boundary, owner = controlled
    with closing(sqlite3.connect(recovery._coordination_path(database))) as connection:
        copied_hash, = connection.execute('SELECT token_hash FROM source_recovery_supervisor').fetchone()
    impostor = object.__new__(supervisor.DeploymentSupervisor)
    impostor._database, impostor._boundary, impostor._identity = database, boundary, owner._identity
    impostor._owner_nonce = copied_hash
    with pytest.raises(ReasoningError):
        impostor.start_worker()
    assert not boundary.admissions


@pytest.mark.parametrize('fault', ['wrong_owner', 'wrong_database', 'incomplete', 'restart', 'unapproved', 'existing_workers'])
def test_untrusted_supervisor_registration_is_denied(authority, platform, fault):
    _, database = authority
    boundary = Boundary(database, platform.owner)

    async def run():
        if fault == 'wrong_owner':
            boundary.view = replace(boundary.view, owner=supervisor.ProcessIdentity(platform.owner.pid, 1))
        elif fault == 'wrong_database':
            boundary.view = replace(boundary.view, database=database.with_name('other.db'))
        elif fault == 'incomplete':
            boundary.view = replace(boundary.view, ownership_complete=False)
        elif fault == 'restart':
            boundary.view = replace(boundary.view, autonomous_restart_disabled=False)
        elif fault == 'unapproved':
            platform.authorized = False
        else:
            recovery.register_recovery_worker(worker_app(database))
        with pytest.raises(maintenance.MaintenanceError if fault == 'unapproved' else ReasoningError):
            supervisor.DeploymentSupervisor(database, boundary)
        assert not recovery.inspect_recovery_maintenance(database, state(database)).supervisor_registered

    run_offline(run())


@pytest.mark.parametrize('fault', ['missing_ticket', 'forged_ticket', 'wrong_database', 'pid_reuse', 'wrong_pid', 'dead_owner', 'replay'])
def test_worker_admission_requires_bound_live_owner_exact_birth_and_one_time_ticket(controlled, platform, fault):
    _, database, boundary, owner = controlled

    async def run():
        child = owner.start_worker()
        ticket = boundary.admissions[-1]
        if fault == 'missing_ticket':
            ticket = None
        elif fault == 'forged_ticket':
            ticket = replace(ticket, nonce='0' * 32)
        elif fault == 'wrong_database':
            ticket = replace(ticket, database=database.with_name('other.db'))
        platform.current = child
        if fault == 'pid_reuse':
            platform.current = replace(child, created=child.created + 1)
        elif fault == 'wrong_pid':
            platform.current = replace(child, pid=child.pid + 1)
        elif fault == 'dead_owner':
            platform.live = False
        elif fault == 'replay':
            recovery.register_recovery_worker(worker_app(database, ticket))
        with pytest.raises(ReasoningError):
            recovery.register_recovery_worker(worker_app(database, ticket))
        observed = recovery.inspect_recovery_maintenance(database, state(database))
        assert observed.registered_workers == int(fault == 'replay')
        assert observed.supervisor_workers == 1

    run_offline(run())


def test_supervised_maintenance_refuses_restart_drains_and_releases_explicitly(controlled, platform):
    app, database, boundary, owner = controlled

    async def run():
        child, worker, registration = admit(database, boundary, owner, platform)

        def acknowledge(process):
            assert process == child
            platform.current = process
            try:
                recovery.acknowledge_recovery_worker_shutdown(worker, registration)
            finally:
                platform.current = platform.owner

        boundary.on_shutdown = acknowledge
        pending = prepare(app, database)
        with pytest.raises(ReasoningError):
            owner.start_worker()
        with pytest.raises(ReasoningError):
            recovery.register_recovery_worker(worker_app(database, boundary.admissions[-1]))
        with pytest.raises(ReasoningError):
            recovery.set_recovery_maintenance(database, pending.generation, active=False)
        with pytest.raises(ReasoningError):
            owner.release_maintenance(pending.generation)
        owner.drain(pending.generation)
        assert boundary.signals == [child] and len(boundary.admissions) == 1
        with pytest.raises(ReasoningError):
            owner.start_worker()
        drained = recovery.inspect_recovery_maintenance(database, pending)
        assert drained.active and drained.awaiting_shutdown == 0 and drained.supervisor_workers == 1
        owner.release_maintenance(pending.generation)
        current = state(database)
        assert current.pending and current.generation != pending.generation
        observed = recovery.inspect_recovery_maintenance(database, current)
        assert not observed.supervisor_registered and not observed.active and observed.registered_workers == 0
        with pytest.raises(ReasoningError):
            owner.start_worker()

    run_offline(run())


def test_fastapi_shutdown_ack_is_tied_to_supervised_process_and_preserves_gates(create_app, platform):
    original = create_app()
    database = original.state.settings.tasks_db_path
    boundary = Boundary(database, platform.owner)

    async def run():
        async with original.router.lifespan_context(original):
            create_sessions(original)
        owner = supervisor.DeploymentSupervisor(database, boundary)
        identity = owner.start_worker()
        worker = create_app()
        routes = tuple(worker.routes)
        worker.state.source_supervisor_admission = boundary.admissions[-1]
        platform.current = identity
        async with worker.router.lifespan_context(worker):
            assert worker.state.source_recovery_registration.pid == identity.pid
            pending = prepare(worker, database)
            assert recovery.inspect_recovery_maintenance(database, pending).awaiting_shutdown == 1
            capabilities = capability_report(
                environment='production', ready=True, planning=True, persistence=True, probe_allowed=False,
                configured_models=dict.fromkeys(('default_chat', 'vision', 'image_chat')), observation=None,
            )
            assert capabilities['document_intelligence']['direct_chat'] == 'blocked'
            assert capabilities['document_intelligence']['artifact_publication'] == 'blocked'
            assert capabilities['capabilities']['document_rag']['state'] == 'blocked'
            assert not capabilities['deployment_verified'] and tuple(worker.routes) == routes
        boundary.exit(identity)
        platform.current = platform.owner
        owner.drain(pending.generation)
        assert not boundary.signals
        assert recovery.inspect_recovery_maintenance(database, pending).awaiting_shutdown == 0

    run_offline(run())


@pytest.mark.parametrize('fault', ['child_crash', 'descendant', 'stale_generation', 'restart_policy', 'supervisor_crash', 'failed_drain'])
def test_uncertain_supervised_drain_leaves_barrier_and_acknowledgment_unresolved(controlled, platform, fault):
    app, database, boundary, owner = controlled

    async def run():
        child, _, _ = admit(database, boundary, owner, platform)
        pending = prepare(app, database)
        generation = pending.generation
        if fault == 'child_crash':
            boundary.exit(child)
        elif fault == 'descendant':
            boundary.view = replace(boundary.view, workers=(*boundary.view.workers,
                supervisor.OwnedWorker(supervisor.ProcessIdentity(999, 1), True)))
        elif fault == 'stale_generation':
            generation = '0' * 32
        elif fault == 'restart_policy':
            boundary.view = replace(boundary.view, autonomous_restart_disabled=False)
        elif fault == 'supervisor_crash':
            platform.current = replace(platform.owner, created=platform.owner.created + 1)
        else:
            def fail(_):
                raise TimeoutError('child did not drain')
            boundary.on_shutdown = fail
        with pytest.raises(ReasoningError):
            owner.drain(generation)
        with pytest.raises(ReasoningError):
            owner.release_maintenance(generation)
        with pytest.raises(ReasoningError):
            recovery.set_recovery_maintenance(database, pending.generation, active=False)
        observed = recovery.inspect_recovery_maintenance(database, pending)
        assert observed.active and observed.awaiting_shutdown == 1
        assert boundary.signals == ([child] if fault == 'failed_drain' else [])

    run_offline(run())


def test_prepare_between_launch_reservation_and_creation_blocks_creation(controlled, monkeypatch):
    app, database, boundary, owner = controlled
    write = supervisor._write
    writes = 0

    @contextmanager
    def interleave(path):
        nonlocal writes
        with write(path) as connection:
            yield connection
        writes += 1
        if writes == 1:
            prepare(app, database)

    monkeypatch.setattr(supervisor, '_write', interleave)
    with pytest.raises(ReasoningError):
        owner.start_worker()
    assert not boundary.admissions
    pending = state(database)
    assert recovery.inspect_recovery_maintenance(database, pending).supervisor_workers == 1
    with pytest.raises(ReasoningError):
        owner.release_maintenance(pending.generation)


def test_interrupted_birth_handoff_stays_unresolved_without_signaling_unknown_handle(controlled, monkeypatch):
    app, database, boundary, owner = controlled
    launch = boundary.launch

    def lost_handoff(admission, *, timeout_seconds):
        launch(admission, timeout_seconds=timeout_seconds)
        raise OSError('lost process handle handoff')

    monkeypatch.setattr(boundary, 'launch', lost_handoff)
    with pytest.raises(ReasoningError):
        owner.start_worker()
    pending = prepare(app, database)
    with pytest.raises(ReasoningError):
        owner.drain(pending.generation)
    observed = recovery.inspect_recovery_maintenance(database, pending)
    assert observed.active and observed.supervisor_workers == 1 and not boundary.signals


def test_concurrent_prepares_select_only_one_supervised_window(controlled):
    app, database, _, _ = controlled
    recovery.quarantine_source_authority(app)
    pending = state(database)
    ready = Barrier(2)

    def apply():
        ready.wait(timeout=5)
        try:
            recovery.set_recovery_maintenance(database, pending.generation, active=True)
            return 'prepared'
        except ReasoningError:
            return 'blocked'

    with ThreadPoolExecutor(max_workers=2) as workers:
        calls = [workers.submit(apply) for _ in range(2)]
        assert sorted(call.result(timeout=5) for call in calls) == ['blocked', 'prepared']
    assert recovery.inspect_recovery_maintenance(database, pending).active


def test_supervisor_preflight_does_not_certify_unmanaged_sqlite_clients(controlled, tmp_path):
    app, database, _, owner = controlled
    backup = tmp_path / 'backup'
    maintenance._backup(database, backup)
    proof = tmp_path / 'checkpoint.txt'
    proof.write_text(recovery.capture_source_checkpoint(app), encoding='ascii')
    pending = prepare(app, database)
    owner.drain(pending.generation)
    before = database.read_bytes(), recovery._coordination_path(database).read_bytes()
    with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)) as unmanaged:
        unmanaged.execute('BEGIN')
        unmanaged.execute('SELECT COUNT(*) FROM sqlite_schema').fetchone()
        result = maintenance._preflight(database, backup / maintenance._BACKUP, proof, pending.generation)
    assert result.status == 'BLOCKED'
    assert 'SUPERVISOR_ADMISSION_LOCK_RECORDED' in result.checks
    assert {'SUPERVISOR_RESTART_UNVERIFIED', 'SQLITE_CLIENT_INVENTORY_UNVERIFIED'} <= set(result.blockers)
    assert before == (database.read_bytes(), recovery._coordination_path(database).read_bytes())
    assert b'private synthetic evidence' not in (backup / maintenance._BACKUP).read_bytes()
    assert not document_content_allowed()


def test_missing_coordinator_cannot_be_created_by_supervisor(controlled):
    _, database, boundary, owner = controlled
    coordinator = recovery._coordination_path(database)
    coordinator.unlink()
    with pytest.raises(ReasoningError):
        owner.start_worker()
    assert not coordinator.exists() and not boundary.admissions


def test_handle_match_rejects_reused_pid_and_exited_owner(monkeypatch):
    api = WindowsKeyStoreNative()
    closed = []
    monkeypatch.setattr(api, '_process_creation_time', lambda handle: 9000)
    monkeypatch.setattr(api, 'close', closed.append)
    operations = {
        'OpenProcess': lambda access, inherit, pid: 31,
        'WaitForSingleObject': lambda handle, delay: 0x102,
    }
    monkeypatch.setattr(api, '_function', lambda dll, name, *args: operations[name])
    assert api.live_process_matches(45, 9000)
    assert not api.live_process_matches(45, 9001)
    operations['WaitForSingleObject'] = lambda handle, delay: 0
    assert not api.live_process_matches(45, 9000)
    assert closed == [31, 31, 31]
