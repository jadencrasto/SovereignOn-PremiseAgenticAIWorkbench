"""Deterministic Win32 and recovery coordinator checks. No native process creation."""

import ctypes
from types import SimpleNamespace

import pytest

from backend.multimodal import document_recovery_supervisor as supervisor
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal import document_supervisor_worker as worker
from backend.multimodal import document_windows_supervisor as windows
from backend.multimodal.document_reasoning import ReasoningError
from backend.runtime.windows_recovery_worker import WindowsRecoveryWorkerAPI
from backend.security.provenance_windows_native import TokenFacts, WindowsKeyStoreNative
from test_a22_async_offline import run_offline
from test_r824_process_containment_offline import win32  # noqa: F401
from test_r867_deployment_supervisor_offline import authority, platform, prepare, worker_app  # noqa: F401


class HandleFixture:
    def __init__(self):
        self.calls = []
        self.closed = []
        self.identity = (202, 404)
        self.admission = None
        self.live = True
        self.timeout = False
        self.total = 1
        self.member = True
        self.on_stop = lambda: None

    def stop_event(self):
        return 10

    def pipe(self):
        return 11, 12

    def inherit_reader(self, reader):
        assert reader == 11
        return 13

    def inherit_stop_wait(self, event):
        assert event == 10
        return 14

    def create_single_worker_job(self):
        return 15

    def create_worker(self, job, command, reader, stop_wait):
        assert (job, reader, stop_wait) == (15, 13, 14)
        assert command == ('python.exe', '-I', '-B', 'fixed-worker.py', '13', '14')
        self.calls.append('create suspended')
        return 16, 17

    def process_identity(self, process):
        assert process == 16 and process not in self.closed
        return self.identity

    def thread_identity(self, thread):
        assert thread == 17
        return self.identity[0], 206

    def running(self, process):
        assert process == 16 and process not in self.closed
        return self.live

    def in_job(self, process, job):
        assert (process, job) == (16, 15)
        return self.member

    def job_counts(self, job):
        assert job == 15 and job not in self.closed
        return self.total, int(self.live)

    def write_ticket(self, writer, secret):
        assert writer == 12
        self.calls.append('write private pipe')
        self.admission = secret.decode('ascii')

    def resume(self, thread):
        assert thread == 17 and set(self.closed) == {11, 12, 13, 14}
        self.calls.append('resume owned primary thread')

    def signal_stop(self, event):
        assert event == 10
        self.calls.append('signal owned stop event')
        self.on_stop()

    def wait_process(self, process, timeout):
        assert process == 16 and 0 <= timeout <= 15000
        if self.timeout:
            return False
        self.live = False
        return True

    def close(self, handle):
        assert handle not in self.closed
        self.closed.append(handle)


@pytest.fixture
def boundary(authority, platform, monkeypatch):
    app, database = authority
    native = HandleFixture()
    monkeypatch.setattr(windows, '_process_identity', lambda: platform.current)
    monkeypatch.setattr(windows, '_worker_command', lambda: ('python.exe', '-I', '-B', 'fixed-worker.py'))
    owner = windows._WindowsProcessBoundary(database, native=native)
    try:
        yield app, database, native, owner
    finally:
        owner.close()


def test_private_handles_connect_coordinator_admission_and_maintenance(boundary, platform):
    app, database, native, handles = boundary
    owner = supervisor.DeploymentSupervisor(database, handles)
    identity = owner.start_worker()
    assert identity == supervisor.ProcessIdentity(202, 404)
    assert native.calls == ['create suspended', 'write private pipe', 'resume owned primary thread']
    assert set(native.closed) == {11, 12, 13, 14, 17}
    ticket = supervisor.WorkerAdmission(database, native.admission)
    assert native.admission not in repr(ticket)
    assert handles.observe().workers == (supervisor.OwnedWorker(identity, True),)

    async def admit():
        platform.current = identity
        try:
            child = worker_app(database, ticket)
            registration = recovery.register_recovery_worker(child)
            assert not hasattr(child.state, 'source_supervisor_admission')
            with pytest.raises(ReasoningError):
                recovery.register_recovery_worker(worker_app(database, ticket))
            return child, registration
        finally:
            platform.current = platform.owner

    child, registration = run_offline(admit())
    pending = prepare(app, database)
    with pytest.raises(ReasoningError):
        owner.start_worker()

    def acknowledge():
        platform.current = identity
        try:
            recovery.acknowledge_recovery_worker_shutdown(child, registration)
        finally:
            platform.current = platform.owner

    # Graceful acknowledgment must occur on the same loop as worker registration.
    async def full_lifecycle():
        # The first phase above used another loop to assert replay. A stopped worker
        # cannot substitute that loop as shutdown evidence.
        with pytest.raises(ReasoningError):
            acknowledge()
        native.live = False
        assert handles.observe().workers == (supervisor.OwnedWorker(identity, False),)
        with pytest.raises(ReasoningError):
            owner.release_maintenance(pending.generation)

    run_offline(full_lifecycle())


def test_graceful_shutdown_uses_retained_event_and_records_verified_exit(boundary, platform):
    app, database, native, handles = boundary
    owner = supervisor.DeploymentSupervisor(database, handles)

    async def lifecycle():
        identity = owner.start_worker()
        child = worker_app(database, supervisor.WorkerAdmission(database, native.admission))
        platform.current = identity
        registration = recovery.register_recovery_worker(child)
        platform.current = platform.owner
        pending = prepare(app, database)

        def acknowledge():
            platform.current = identity
            try:
                recovery.acknowledge_recovery_worker_shutdown(child, registration)
            finally:
                platform.current = platform.owner

        native.on_stop = acknowledge
        owner.drain(pending.generation)
        assert handles.observe().workers == (supervisor.OwnedWorker(identity, False),)
        assert 16 not in native.closed
        owner.release_maintenance(pending.generation)
        observed = recovery._read_shared_state(recovery._coordination_path(database))
        assert observed.pending and observed.generation != pending.generation

    run_offline(lifecycle())
    assert native.calls[-1] == 'signal owned stop event'
    handles.close()
    assert set(native.closed) == set(range(10, 18))


@pytest.mark.parametrize('fault', ['pid_reuse', 'descendant', 'membership', 'owner_replacement'])
def test_inconsistent_owned_handles_fail_closed(boundary, platform, fault):
    _, database, native, handles = boundary
    owner = supervisor.DeploymentSupervisor(database, handles)
    identity = owner.start_worker()
    failures = {
        'pid_reuse': lambda: setattr(native, 'identity', (202, 405)),
        'descendant': lambda: setattr(native, 'total', 2),
        'membership': lambda: setattr(native, 'member', False),
        'owner_replacement': lambda: setattr(platform, 'current', supervisor.ProcessIdentity(100, 8001)),
    }
    failures[fault]()
    with pytest.raises(RuntimeError):
        handles.observe()
    with pytest.raises(RuntimeError):
        handles.shutdown(identity, timeout_seconds=1)
    assert 'signal owned stop event' not in native.calls


def test_ipc_failure_closes_all_handles_without_resuming(boundary, monkeypatch):
    _, database, native, handles = boundary
    owner = supervisor.DeploymentSupervisor(database, handles)

    def broken(*_):
        raise OSError('broken')

    monkeypatch.setattr(native, 'write_ticket', broken)
    with pytest.raises(ReasoningError):
        owner.start_worker()
    assert native.calls == ['create suspended']
    assert set(native.closed) == set(range(10, 18))
    with pytest.raises(RuntimeError):
        handles.observe()


def test_timeout_leaves_exit_unknown_and_does_not_force_terminate(boundary):
    _, database, native, handles = boundary
    child = supervisor.DeploymentSupervisor(database, handles).start_worker()
    native.timeout = True
    with pytest.raises(RuntimeError, match='timed out'):
        handles.shutdown(child, timeout_seconds=1)
    assert handles.observe().workers[0].running and 16 not in native.closed


def test_lockout_rejects_already_created_worker_before_admission(boundary, platform):
    app, database, native, handles = boundary
    owner = supervisor.DeploymentSupervisor(database, handles)
    identity = owner.start_worker()
    pending = prepare(app, database)

    async def admit():
        platform.current = identity
        try:
            child = worker_app(database, supervisor.WorkerAdmission(database, native.admission))
            with pytest.raises(ReasoningError):
                recovery.register_recovery_worker(child)
        finally:
            platform.current = platform.owner

    run_offline(admit())
    assert recovery.inspect_recovery_maintenance(database, pending).registered_workers == 0
    with pytest.raises(ReasoningError):
        owner.start_worker()
    with pytest.raises(ReasoningError):
        owner.drain(pending.generation)


@pytest.mark.parametrize('actual_account', ['operator', 'other'])
def test_deployment_child_token_is_checked_on_retained_process_before_resume(boundary, monkeypatch, actual_account):
    _, database, native, handles = boundary
    handles._worker_credentials = windows._WorkerCredentials(51, ('operator', 4))
    token_checks = []

    def create_as(job, command, reader, stop, token):
        assert token == 51
        return native.create_worker(job, command, reader, stop)

    def child_token(process):
        assert process == 16 and native.calls == ['create suspended']
        return 52

    monkeypatch.setattr(native, 'create_worker_as', create_as, raising=False)
    monkeypatch.setattr(windows, 'WindowsKeyStoreNative', lambda: SimpleNamespace(
        open_process_token_for=child_token, close=token_checks.append,
        supervised_worker_token_identity=lambda _: (actual_account, 4),
    ))
    owner = supervisor.DeploymentSupervisor(database, handles)
    if actual_account == 'operator':
        owner.start_worker()
        assert native.calls[-1] == 'resume owned primary thread'
    else:
        with pytest.raises(ReasoningError):
            owner.start_worker()
        assert native.calls == ['create suspended']
    assert token_checks == [52]


@pytest.mark.parametrize(('elevation', 'elevated', 'integrity', 'allowed'), [
    (3, 0, 'S-1-16-8192', True), (2, 1, 'S-1-16-12288', False),
    (3, 0, 'S-1-16-12288', False), (1, 0, 'S-1-16-8192', False),
])
def test_native_worker_qualification_requires_filtered_medium_primary_token(monkeypatch, elevation, elevated, integrity, allowed):
    api = WindowsKeyStoreNative()

    def token_info(token, kind, value):
        assert token == 52
        if kind == 25:
            ctypes.c_void_p.from_buffer(value).value = ctypes.addressof(value) + 2 * ctypes.sizeof(ctypes.c_void_p)
            return ctypes.sizeof(value)
        value.value = {18: elevation, 20: elevated, 12: 4}[kind]
        return ctypes.sizeof(value)

    monkeypatch.setattr(api, '_token_info', token_info)
    monkeypatch.setattr(api, '_copy_sid', lambda *_: integrity)
    monkeypatch.setattr(api, 'token_facts', lambda _: TokenFacts('operator', True, 'operator', 2))
    monkeypatch.setattr(api, '_require_filtered_worker_rights', lambda _: None)
    if allowed:
        assert api.supervised_worker_token_identity(52) == ('operator', 4)
    else:
        with pytest.raises((OSError, RuntimeError)):
            api.supervised_worker_token_identity(52)


def test_invalid_private_worker_cannot_provision_missing_database(tmp_path):
    database = tmp_path / 'absent.db'
    app = worker_app(database, supervisor.WorkerAdmission(database, 'a' * 32))

    async def attempt():
        with pytest.raises(ReasoningError):
            recovery.register_recovery_worker(app)

    run_offline(attempt())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('data', [b'a' * 32, b'a' * 31, b'X' * 32, b'a' * 33])
def test_worker_closes_inherited_reader_even_on_malformed_ticket(data):
    closed = []
    api = SimpleNamespace(read_ticket=lambda _: data, close=closed.append)
    if data == b'a' * 32:
        assert worker._read_nonce(api, 12) == 'a' * 32
    else:
        with pytest.raises(RuntimeError):
            worker._read_nonce(api, 12)
    assert closed == [12]


@pytest.mark.parametrize('args', [('1', '1'), ('-1', '2'), ('4294967295', '2'), ('1', '2', 'secret')])
def test_worker_arguments_accept_only_two_distinct_handle_slots(args):
    assert worker._bootstrap_handles(('13', '14')) == (13, 14)
    with pytest.raises(RuntimeError):
        worker._bootstrap_handles(args)


def test_worker_observes_stop_before_serving():
    server = SimpleNamespace(serve=lambda: pytest.fail('startup attempted'))
    native = SimpleNamespace(wait_process=lambda *_: True)
    assert run_offline(worker._serve(server, native, 10)) is False


@pytest.mark.parametrize('reported_limit', [1, 2])
def test_job_requires_kernel_readback_of_one_active_process_and_no_breakaway(win32, monkeypatch, reported_limit):
    kernel, calls = win32
    native = WindowsRecoveryWorkerAPI()

    def configure(job, kind, info, length):
        assert (job, kind) == (40, 9)
        assert info._obj.BasicLimitInformation.LimitFlags == 0x2008
        assert info._obj.BasicLimitInformation.ActiveProcessLimit == 1
        return True

    def readback(job, kind, info, length, _):
        info._obj.BasicLimitInformation.LimitFlags = 0x2008
        info._obj.BasicLimitInformation.ActiveProcessLimit = reported_limit
        return True

    monkeypatch.setattr(kernel, 'CreateJobObjectW', lambda first, name: 40 if first is name is None else 0)
    monkeypatch.setattr(kernel, 'SetInformationJobObject', configure)
    monkeypatch.setattr(kernel, 'QueryInformationJobObject', readback)
    monkeypatch.setattr(kernel, 'CloseHandle', lambda handle: calls.append(handle))
    if reported_limit == 1:
        assert native.create_single_worker_job() == 40 and not calls
    else:
        with pytest.raises(RuntimeError):
            native.create_single_worker_job()
        assert calls == [40]


def test_native_create_abi_keeps_worker_suspended_with_only_two_inherited_slots(win32, monkeypatch):
    kernel, _ = win32
    api = WindowsRecoveryWorkerAPI()
    attributes = {}

    def initialize(buffer, count, flags, size):
        assert (count, flags) == (2, 0)
        size._obj.value = 64
        return buffer is not None

    def update(buffer, flags, attr, value, length, previous, returned):
        handles = ctypes.cast(value, ctypes.POINTER(ctypes.c_size_t))
        attributes[attr] = tuple(handles[i] for i in range(length // ctypes.sizeof(ctypes.c_size_t)))
        return True

    def create(exe, cmd, process_attrs, thread_attrs, inherit, flags, env, cwd, info, process):
        assert inherit and flags == 0x0008000C
        assert attributes == {0x0002000D: (40,), 0x00020002: (13, 14)}
        assert 'private-secret' not in cmd.value
        process._obj.hProcess = 41
        process._obj.hThread = 42
        return True

    monkeypatch.setattr(kernel, 'InitializeProcThreadAttributeList', initialize)
    monkeypatch.setattr(kernel, 'UpdateProcThreadAttribute', update)
    monkeypatch.setattr(kernel, 'CreateProcessW', create)
    assert api.create_worker(40, [r'C:\trusted\python.exe', '-I', 'fixed-worker.py', '13', '14'], 13, 14) == (41, 42)
