"""Actual FastAPI lifecycle and authorization stores with simulated process and bind."""

import asyncio
import ctypes
from types import SimpleNamespace

import pytest
from uvicorn import Server

from backend.agent.deployment_documents import inspect_document_worker
from backend.agent.sqlite_transaction import immediate_transaction, observe_sqlite_activity
from backend.auth.models import AuthStore
from backend.multimodal import document_recovery_supervisor as supervisor
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal import document_supervisor_worker as worker
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_windows_supervisor import _require_supervised_profile
from backend.security import provenance_windows_native as native_security
from test_a22_async_offline import run_offline
from test_r857_deployment_composition_offline import create_app, create_sessions  # noqa: F401
from test_r867_deployment_supervisor_offline import Boundary, platform, prepare, state  # noqa: F401


@pytest.fixture
def supervised(create_app, platform):
    initial = create_app()

    async def provision():
        async with initial.router.lifespan_context(initial):
            create_sessions(initial)

    run_offline(provision())
    app = create_app()
    database = app.state.settings.tasks_db_path
    process = Boundary(database, platform.owner)
    owner = supervisor.DeploymentSupervisor(database, process)
    identity = owner.start_worker()
    app.state.source_supervisor_admission = process.admissions[-1]
    return app, database, process, owner, identity


def test_ready_requires_actual_authenticated_lifespan_and_listener_start(supervised, platform, monkeypatch):
    app, database, process, owner, identity = supervised
    phases = []
    stop = SimpleNamespace(signalled=False)
    api = SimpleNamespace(wait_process=lambda *_: stop.signalled)

    def as_owner(action):
        platform.current = platform.owner
        try:
            return action()
        finally:
            platform.current = identity

    async def startup(server, sockets=None):
        await server.lifespan.startup()
        assert not server.lifespan.should_exit and app.state.startup_complete
        assert app.state.source_recovery_registration.pid == identity.pid
        assert not hasattr(app.state, 'source_supervisor_admission')
        assert type(app.state.auth_store) is AuthStore
        assert app.state.session_manager._store is app.state.auth_store
        assert app.state.auth_store.count_users() == 2
        with pytest.raises(ReasoningError):
            as_owner(lambda: owner.wait_for_worker_ready(identity, timeout_seconds=1))
        server.started = True  # Stand-in for the bind, with no socket opened.
        phases.append('bound')

    async def main_loop(server):
        as_owner(lambda: owner.wait_for_worker_ready(identity, timeout_seconds=1))
        activity = app.state.document_recovery_worker.sqlite
        with observe_sqlite_activity(activity), immediate_transaction(database, require_existing=True):
            observation = inspect_document_worker(app)
            assert (observation.phase, observation.observed_connections) == ('active', 1)
        assert inspect_document_worker(app).observed_connections == 0
        pending = prepare(app, database)
        with pytest.raises(ReasoningError):
            as_owner(lambda: owner.wait_for_worker_ready(identity, timeout_seconds=1))
        with pytest.raises(ReasoningError):
            as_owner(owner.start_worker)
        stop.signalled = True
        async with asyncio.timeout(2):
            while not server.should_exit:
                await asyncio.sleep(0.05)
        phases.append('ready')

    async def shutdown(server, sockets=None):
        await server.lifespan.shutdown()
        assert not server.lifespan.shutdown_failed
        phases.append('shutdown')

    monkeypatch.setattr(Server, 'startup', startup)
    monkeypatch.setattr(Server, 'main_loop', main_loop)
    monkeypatch.setattr(Server, 'shutdown', shutdown)
    platform.current = identity
    server = worker._server(app, app.state.settings, api, 17)
    assert run_offline(worker._serve(server, api, 17)) is True
    assert phases == ['bound', 'ready', 'shutdown'] and server.startup_observed
    assert app.state.source_recovery_registration is None
    process.exit(identity)
    platform.current = platform.owner
    pending = state(database)
    owner.drain(pending.generation)
    owner.release_maintenance(pending.generation)
    assert state(database).pending and state(database).generation != pending.generation


def test_barrier_after_lifespan_start_refuses_readiness_and_still_acknowledges_shutdown(supervised, platform, monkeypatch):
    app, database, _, _, identity = supervised
    api = SimpleNamespace(wait_process=lambda *_: False)

    async def startup(server, sockets=None):
        await server.lifespan.startup()
        assert app.state.startup_complete
        prepare(app, database)
        server.started = True

    async def shutdown(server, sockets=None):
        await server.lifespan.shutdown()
        assert not server.lifespan.shutdown_failed

    monkeypatch.setattr(Server, 'startup', startup)
    monkeypatch.setattr(Server, 'main_loop', lambda _: pytest.fail('Stale worker entered serving loop'))
    monkeypatch.setattr(Server, 'shutdown', shutdown)
    platform.current = identity
    server = worker._server(app, app.state.settings, api, 17)
    assert run_offline(worker._serve(server, api, 17)) is False
    assert app.state.source_recovery_registration is None
    pending = state(database)
    assert pending.pending and recovery.inspect_recovery_maintenance(database, pending).awaiting_shutdown == 0


@pytest.mark.parametrize('fault', ['wrong_process', 'maintenance'])
def test_failed_admission_never_opens_auth_store(supervised, platform, monkeypatch, fault):
    from backend import main

    app, database, _, _, identity = supervised
    if fault == 'maintenance':
        prepare(app, database)
        platform.current = identity
    monkeypatch.setattr(main, 'AuthStore', lambda **_: pytest.fail('AuthStore opened before admission'))

    async def run():
        with pytest.raises(ReasoningError):
            async with app.router.lifespan_context(app):
                pytest.fail('Lifespan admitted an unverified worker')

    run_offline(run())
    assert app.state.startup_complete is False
    assert recovery.inspect_recovery_maintenance(database, state(database)).registered_workers == 0


def test_maintenance_during_initialization_prevents_lifespan_ready(supervised, platform, monkeypatch):
    from backend import main

    app, database, process, owner, identity = supervised

    def auth_store(**kwargs):
        prepare(app, database)
        return AuthStore(**kwargs)

    monkeypatch.setattr(main, 'AuthStore', auth_store)
    platform.current = identity

    async def run():
        with pytest.raises(ReasoningError):
            async with app.router.lifespan_context(app):
                pytest.fail('Stale generation reached readiness')

    run_offline(run())
    pending = state(database)
    assert pending.pending and not app.state.startup_complete
    assert recovery.inspect_recovery_maintenance(database, pending).awaiting_shutdown == 1
    process.exit(identity)
    platform.current = platform.owner
    with pytest.raises(ReasoningError):
        owner.release_maintenance(pending.generation)


def test_failed_lifespan_refuses_detached_password_provisioning(supervised, platform, monkeypatch):
    from backend import main

    app, database, process, owner, identity = supervised
    with immediate_transaction(database, require_existing=True) as connection:
        connection.execute('DELETE FROM sessions')
        connection.execute('DELETE FROM users')
    monkeypatch.setattr(main, 'initialize_admin_user_if_empty', lambda _: pytest.fail('Generated detached credential'))
    platform.current = identity

    async def run():
        with pytest.raises(RuntimeError, match='initialized authentication catalog'):
            async with app.router.lifespan_context(app):
                pytest.fail('Unprovisioned application reached readiness')

    run_offline(run())
    assert not app.state.startup_complete
    process.exit(identity)
    platform.current = platform.owner
    with pytest.raises(ReasoningError):
        owner.wait_for_worker_ready(identity, timeout_seconds=1)
    assert AuthStore(db_path=database).count_users() == 0


def test_outstanding_tracked_connection_leaves_supervised_shutdown_unacknowledged(supervised, platform):
    app, database, process, owner, identity = supervised
    platform.current = identity

    async def run():
        with pytest.raises(ReasoningError):
            async with app.router.lifespan_context(app):
                prepare(app, database)
                app.state.document_recovery_worker.sqlite.open_connections = 1

    run_offline(run())
    pending = state(database)
    assert recovery.inspect_recovery_maintenance(database, pending).awaiting_shutdown == 1
    process.exit(identity)
    platform.current = platform.owner
    with pytest.raises(ReasoningError):
        owner.release_maintenance(pending.generation)


@pytest.mark.parametrize(('group_flags', 'privilege_index', 'privilege_flags', 'valid'), [
    (0x10, 1, 0x03, True),     # Deny-only admin, enabled change notification.
    (0x04, 1, 0x03, False),    # Enabled admin.
    (0x00, 1, 0x03, False),    # Disabled admin is not the required deny-only group.
    (0x10, 2, 0x02, False),    # Enabled shutdown.
    (0x10, 255, 0x00, False),  # Unapproved privilege, even if disabled.
])
def test_filtered_token_rights_require_deny_only_admin_and_bounded_privileges(
    monkeypatch, group_flags, privilege_index, privilege_flags, valid,
):
    api = native_security.WindowsKeyStoreNative()
    names = {}

    def lookup(_, name, out):
        out._obj.low = names.setdefault(name, len(names) + 1)
        return True

    def info(_, kind, buffer):
        ctypes.c_uint32.from_buffer(buffer).value = 1
        if kind == 2:
            offset = native_security._TokenGroups.first.offset
            entry = native_security._SidAndAttributes.from_buffer(buffer, offset)
            tail = offset + ctypes.sizeof(entry)
            entry.attributes = group_flags
            entry.sid = ctypes.addressof(buffer) + tail
            sid = bytes.fromhex('01020000000000052000000020020000')
            ctypes.memmove(entry.sid, sid, len(sid))
            return tail + len(sid)
        entry = native_security._LuidAndAttributes.from_buffer(buffer, 4)
        entry.luid.low = privilege_index
        entry.attributes = privilege_flags
        return 4 + ctypes.sizeof(entry)

    monkeypatch.setattr(api, '_token_info', info)
    monkeypatch.setattr(api, '_function', lambda *args: lookup)
    if valid:
        api._require_filtered_worker_rights(44)
    else:
        with pytest.raises(OSError):
            api._require_filtered_worker_rights(44)


def test_native_admission_profile_keeps_loopback_real_auth_and_chat_only():
    base = dict(backend_host='127.0.0.1', ollama_base_url='http://127.0.0.1:11434', app_env='development',
                auth_enabled=True, auth_dev_override=False, local_chat_only=True)
    _require_supervised_profile(SimpleNamespace(**base))
    for invalid in ({'auth_enabled': False}, {'auth_dev_override': True}, {'local_chat_only': False},
                    {'backend_host': '0.0.0.0'}, {'app_env': 'production'}):
        with pytest.raises(RuntimeError):
            _require_supervised_profile(SimpleNamespace(**(base | invalid)))
