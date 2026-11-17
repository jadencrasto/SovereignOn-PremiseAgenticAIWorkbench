"""Exercise the candidate driver with fake handles and Win32 calls only."""
import ctypes
from dataclasses import replace
from pathlib import Path

import pytest

from backend.runtime.windows_job_driver import OwnedJob, WindowsJobDriver, WindowsNativeAPI
from test_i52_windows_job_mock import Native, driver


def test_disabled_launcher_is_ascii_for_windows_powershell_51():
    source = Path(__file__).resolve().parents[2] / 'scripts' / 'dev.ps1'
    assert source.read_bytes().isascii()


class AtomicNative(Native):
    def create_suspended_in_job(self, job, command):
        self.log.append('atomic_create')
        process, thread = object(), object()
        self.status[process] = True
        self.assigned.add(process)
        self.active += 1
        return process, thread


def test_atomic_create_has_no_sequential_fallback():
    api = AtomicNative()
    d = driver(api)
    job = d.open_containment()
    root = d.start_contained(job, 'backend')
    assert api.log[:4] == ['job', 'atomic_create', 'in_job', 'resume']
    assert root.resumed and d.stop_containment(job)
    assert 'assign' not in api.log and 'suspended' not in api.log


@pytest.mark.parametrize('failure', ['unsupported', 'creation-failed', 'membership-unconfirmed'])
def test_atomic_create_failure_never_falls_back_or_resumes(failure, monkeypatch):
    api = AtomicNative()
    d = driver(api)
    job = d.open_containment()
    if failure == 'membership-unconfirmed':
        monkeypatch.setattr(api, 'in_job', lambda *args: False)
        error = RuntimeError
    else:
        error = NotImplementedError if failure == 'unsupported' else OSError

        def fail(*args):
            raise error('synthetic')

        monkeypatch.setattr(api, 'create_suspended_in_job', fail)
    with pytest.raises(error):
        d.start_contained(job, 'backend')
    assert d.stop_containment(job)
    assert not any(name in api.log for name in ('assign', 'suspended', 'resume'))
    assert not any(api.status.values())


def test_cleanup_wins_during_resume_authorization():
    api = AtomicNative()
    d = driver(api)
    job = d.open_containment()

    def authorize():
        root, = job.roots
        assert not d.is_running(root) and not root.resumed
        assert d.stop_containment(job)
        return True

    with pytest.raises(RuntimeError, match='Unknown containment'):
        d.start_contained(job, 'backend', before_resume=authorize)
    assert 'resume' not in api.log
    assert api.log.count('terminate_job') == 1


@pytest.mark.parametrize('change', ['root-exits', 'membership-lost', 'nonboolean-evidence'])
def test_final_resume_rechecks_live_membership_and_decision(change):
    api = AtomicNative()
    d = driver(api)
    job = d.open_containment()

    def authorize():
        root, = job.roots
        if change == 'root-exits':
            api.status[root.handle] = False
        elif change == 'membership-lost':
            api.in_job = lambda *args: False
        else:
            return 1
        return True

    with pytest.raises(RuntimeError):
        d.start_contained(job, 'backend', before_resume=authorize)
    assert 'resume' not in api.log
    assert d.stop_containment(job)


def test_stale_or_forged_handles_are_not_used():
    api = AtomicNative()
    d = driver(api)
    job = d.open_containment()
    root = d.start_contained(job, 'backend')
    calls = api.log[:]
    assert not d.is_running(replace(root))
    with pytest.raises(RuntimeError):
        d.stop_containment(OwnedJob(job.handle))
    with pytest.raises(RuntimeError):
        d.wait_root(job, replace(root), 0)
    assert api.log == calls
    assert d.stop_containment(job)
    assert not d.is_running(root)


@pytest.mark.parametrize('exit_at, observed', [(0.15, True), (3.0, False)])
def test_asynchronous_termination_observation_is_bounded(exit_at, observed):
    api = AtomicNative()
    now = 0.0
    delays = []

    def sleep(delay):
        nonlocal now
        delays.append(delay)
        now += delay

    d = WindowsJobDriver(
        {'backend': [r'C:\synthetic\backend.exe'], 'frontend': [r'C:\synthetic\frontend.exe']},
        api, clock=lambda: now, sleep=sleep,
    )
    job = d.open_containment()
    root = d.start_contained(job, 'backend')
    unrelated = object()
    api.status[unrelated] = True

    def terminate(_):
        api.log.append('terminate_job')
        api.status[root.handle] = False

    api.terminate_job = terminate
    api.active_processes = lambda _: 1 if now < exit_at else 0
    assert d.stop_containment(job) is observed
    assert job.closed is observed
    assert now <= 2 and delays and max(delays) <= 0.05
    assert api.status[unrelated] is True
    assert api.log.count('terminate_job') == 1


def test_incomplete_observer_blocks_native_driver_before_kernel_job_creation(monkeypatch):
    # Use the actual API type and a synthetic constructor so no DLL is loaded.
    monkeypatch.setattr(WindowsNativeAPI, '__init__', lambda self: None)
    api = WindowsNativeAPI()
    monkeypatch.setattr(api, 'create_kill_on_close_job', lambda: pytest.fail('native job created'))
    with pytest.raises(RuntimeError, match='Independent process observer handoff unavailable'):
        driver(api).open_containment()


@pytest.fixture
def win32(monkeypatch):
    calls = []

    def initialize(buffer, count, flags, size):
        assert (count, flags) == (1, 0)
        calls.append('size' if buffer is None else 'initialize')
        size._obj.value = 64
        return buffer is not None

    def update(buffer, flags, attr, jobs, length, previous, returned):
        assert (flags, attr, previous, returned) == (0, 0x2000D, None, None)
        assert length == ctypes.sizeof(ctypes.c_void_p)
        assert ctypes.cast(jobs, ctypes.POINTER(ctypes.c_void_p)).contents.value == 40
        calls.append('update')
        return True

    def create(exe, cmd, process_attrs, thread_attrs, inherit, flags, env, cwd, info, process):
        calls.append('create')
        assert not inherit and flags == 0x08080004
        assert info._obj.cb > ctypes.sizeof(info._obj)
        process._obj.hProcess = 41
        process._obj.hThread = 42
        return True

    class Kernel:
        InitializeProcThreadAttributeList = staticmethod(initialize)
        UpdateProcThreadAttribute = staticmethod(update)
        CreateProcessW = staticmethod(create)
        DeleteProcThreadAttributeList = staticmethod(lambda _: calls.append('delete'))

        def __getattr__(self, name):
            def unexpected(*args):
                pytest.fail('Unexpected Win32 operation')
            setattr(self, name, unexpected)
            return unexpected

    kernel = Kernel()
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 122, raising=False)
    return kernel, calls


def test_win32_creation_attribute_keeps_job_bound_before_execution(win32):
    _, calls = win32
    api = WindowsNativeAPI()
    assert api.create_suspended_in_job(40, [r'C:\synthetic\backend.exe']) == (41, 42)
    assert calls == ['size', 'initialize', 'update', 'create', 'delete']


def test_unsupported_job_attribute_releases_list_without_child_creation(win32, monkeypatch):
    kernel, calls = win32
    api = WindowsNativeAPI()
    monkeypatch.setattr(kernel, 'UpdateProcThreadAttribute', lambda *args: False)
    with pytest.raises(OSError):
        api.create_suspended_in_job(40, [r'C:\synthetic\backend.exe'])
    assert calls == ['size', 'initialize', 'delete']


@pytest.mark.parametrize('flags', [None, 0, 0x2000, 0x2800, 0x3000])
def test_job_limit_requires_independent_readback_without_breakaway(win32, monkeypatch, flags):
    kernel, calls = win32
    api = WindowsNativeAPI()

    def readback(job, info_class, info, length, returned):
        assert job == 40 and info_class == 9
        assert info._obj.BasicLimitInformation.LimitFlags == 0
        if flags is not None:
            info._obj.BasicLimitInformation.LimitFlags = flags
        return True

    monkeypatch.setattr(kernel, 'CreateJobObjectW', lambda *args: 40)
    monkeypatch.setattr(kernel, 'SetInformationJobObject', lambda *args: True)
    monkeypatch.setattr(kernel, 'QueryInformationJobObject', readback)
    monkeypatch.setattr(kernel, 'CloseHandle', lambda handle: calls.append(handle))
    if flags == 0x2000:
        assert api.create_kill_on_close_job() == 40 and calls == []
    else:
        with pytest.raises(RuntimeError, match='Job limits not verified'):
            api.create_kill_on_close_job()
        assert calls == [40]
