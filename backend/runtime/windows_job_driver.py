"""Experimental Windows job driver; not connected to application startup.

Native execution and containment have not been verified. Readiness deliberately
fails closed until a trusted listener-identity implementation is available.
"""
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from threading import Condition, Lock
import time


_CLEANUP_TIMEOUT = 2.0
_CLEANUP_POLL_INTERVAL = 0.05
_KILL_ON_JOB_CLOSE = 0x2000
_JOB_LIST_ATTRIBUTE = 0x0002000D
_HANDLE_LIST_ATTRIBUTE = 0x00020002
_SUSPENDED_EXTENDED_STARTUP = 0x00000004 | 0x00080000 | 0x08000000
_PROCESS_OBSERVE = 0x1000 | 0x100000
_THREAD_CONTROL = 0x0002 | 0x0800
_JOB_QUERY = 0x0004


@dataclass
class OwnedRoot:
    handle: object
    thread: object
    role: str
    assigned: bool = False
    resumed: bool = False


@dataclass
class OwnedJob:
    handle: object
    roots: list = field(default_factory=list)
    closed: bool = False
    cleanup_started: bool = False
    descendants: list = field(default_factory=list)
    handoff: object = None
    handoff_active: bool = False


class WindowsJobDriver:
    """Coordinator adapter. `native` can be replaced only for isolated unit tests.

    All termination goes through held process/job handles, never a PID. A job
    with uncertain cleanup remains open to avoid reporting false success.
    """
    def __init__(self, commands, native=None, *, endpoints=None, listener_resolver=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.native = native if native is not None else WindowsNativeAPI()
        self.commands = dict(commands)
        if set(self.commands) != {'backend', 'frontend'}:
            raise ValueError('Two explicit synthetic commands are required')
        for command in self.commands.values():
            if not command or not Path(command[0]).is_absolute() or any(not isinstance(x, str) or '\x00' in x for x in command):
                raise ValueError('Absolute executable and valid arguments required')
        self._job = None
        self._lock = Lock()
        self._handoff_complete = Condition(self._lock)
        self._clock = clock
        self._sleep = sleep
        self.endpoints = dict(endpoints or {})
        self.listener_resolver = listener_resolver
        if self.endpoints and set(self.endpoints) != {'backend', 'frontend'}:
            raise ValueError('Both listener endpoints must be specified')
        if self.endpoints and self.listener_resolver is None:
            from backend.runtime.windows_listener import ListenerResolver, WindowsListenerAPI
            self.listener_resolver = ListenerResolver(WindowsListenerAPI(self.native))

    def open_containment(self, *, handoff=None):
        with self._lock:
            if isinstance(self.native, WindowsNativeAPI):
                from tools.process_test_supervisor.native_bootstrap import verified_fixture_handoff
                if not verified_fixture_handoff(handoff, self.native, self.commands['backend']):
                    raise RuntimeError('Independent process observer handoff unavailable')
            if self._job is not None:
                raise RuntimeError('A containment is already open')
            handle = self.native.create_kill_on_close_job()
            if not handle:
                raise RuntimeError('Job creation failed')
            self._job = OwnedJob(handle)
            self._required_handoff = handoff
            return self._job

    def _check_job(self, job):
        if self._job is None or job is not self._job or job.closed or job.cleanup_started:
            raise RuntimeError('Unknown containment')

    def start_contained(self, job, role, *, before_resume=None, handoff=None):
        if handoff is not None:
            handoff.check_peer()
        with self._lock:
            self._check_job(job)
            if self._required_handoff is not None and handoff is not self._required_handoff:
                raise RuntimeError('Authenticated fixture handoff required')
            if isinstance(self.native, WindowsNativeAPI):
                from tools.process_test_supervisor.native_bootstrap import verified_fixture_handoff
                if (role != 'backend'
                        or not verified_fixture_handoff(handoff, self.native, self.commands['backend'])):
                    raise RuntimeError('Native fixture command changed')
            if job.handoff is not None or (
                    handoff is not None and (before_resume is not None or job.roots
                    or not callable(getattr(self.native, 'create_suspended_in_job', None)))):
                raise RuntimeError('Invalid authenticated fixture startup')
            if role not in self.commands or any(root.role == role for root in job.roots):
                raise RuntimeError('Unknown or duplicate process role')
            create_in_job = getattr(self.native, 'create_suspended_in_job', None)
            if create_in_job is not None:
                process, thread = create_in_job(job.handle, self.commands[role])
            else:
                # Compatibility for the synthetic sequential-assignment adapter.
                process, thread = self.native.create_suspended(self.commands[role])
            root = OwnedRoot(process, thread, role)
            job.roots.append(root)
            if create_in_job is None:
                self.native.assign(job.handle, root.handle)
            if self.native.in_job(root.handle, job.handle) is not True:
                raise RuntimeError('Job assignment not verified')
            root.assigned = True
            if self.native.running(root.handle) is not True:
                raise RuntimeError('Process exited during setup')
            if handoff is not None:
                job.handoff = handoff
                job.handoff_active = True
            elif before_resume is None:
                return self._resume(root)

        if handoff is not None:
            try:
                return self._resume_observed(job, root, handoff)
            except BaseException:
                handoff.abort()
                raise
            finally:
                self._finish_handoff(job)

        if before_resume() is not True:
            raise RuntimeError('Resume authorization unavailable')
        with self._lock:
            # Serialize the final validation and resume against the start of cleanup.
            self._check_job(job)
            if (self.native.running(root.handle) is not True
                    or self.native.in_job(root.handle, job.handle) is not True):
                raise RuntimeError('Process ownership changed during setup')
            return self._resume(root)

    def _finish_handoff(self, job):
        with self._handoff_complete:
            job.handoff_active = False
            self._handoff_complete.notify_all()

    def _resume_observed(self, job, process, handoff, *, parent=None, parent_handle=None):
        permit, identity = handoff.prepare(job.handle, process.handle, parent=parent)
        if (self.native.running(process.handle) is not True
                or self.native.in_job(process.handle, job.handle) is not True
                or self.native.process_identity(process.handle) != identity
                or (parent_handle is not None and (self.native.running(parent_handle) is not True
                    or self.native.process_identity(parent_handle) != parent
                    or self.native.in_job(parent_handle, job.handle) is not True))):
            raise RuntimeError('Observed process ownership changed')
        if parent_handle is not None:
            handoff.confirm_parentage(job.handle, parent_handle, process.handle, parent, identity)
        with self._lock:
            self._check_job(job)
            handoff.consume(permit, job.handle, identity, parent=parent)
        # The decision is committed. The outstanding reservation pins the
        # handles so cleanup cannot close one while a native call is in flight.
        self.native.resume(process.thread)
        with self._lock:
            self._check_job(job)
            handoff.check_deadline()
        if self.native.running(process.handle) is not True:
            raise RuntimeError('Observed process exited during resume')
        self.native.close(process.thread)
        with self._lock:
            process.thread = None
            self._check_job(job)
            handoff.check_deadline()
            process.resumed = True
        return process

    def adopt_descendant(self, job, root, handoff):
        with self._lock:
            self._check_job(job)
            if (job.handoff is not handoff or job.handoff_active or job.descendants
                    or len(job.roots) != 1 or job.roots[0] is not root or not root.resumed):
                raise RuntimeError('No eligible cooperative root')
            job.handoff_active = True
        process = thread = None
        owned = False
        try:
            parent = self.native.process_identity(root.handle)
            offer = handoff.claim_child(root.handle, parent)
            process, thread = self.native.duplicate_descendant(root.handle, offer.process_slot, offer.thread_slot)
            if (self.native.process_identity(process) != offer.child
                    or self.native.thread_identity(thread) != (offer.child[0], offer.thread_id)
                    or self.native.in_job(process, job.handle) is not True
                    or self.native.running(process) is not True):
                raise RuntimeError('Descendant identity or membership not verified')
            child = OwnedRoot(process, thread, 'fixture_child', assigned=True)
            with self._lock:
                # Retain independently verified handles for cleanup even if an
                # abort was registered during transfer.
                job.descendants.append(child)
                owned = True
                self._check_job(job)
            handoff.verify_parentage(job.handle, root.handle, child.handle, parent, offer.child)
            return self._resume_observed(job, child, handoff, parent=parent, parent_handle=root.handle)
        except BaseException:
            handoff.abort()
            raise
        finally:
            try:
                if not owned:
                    try:
                        if thread is not None:
                            self.native.close(thread)
                    finally:
                        if process is not None:
                            self.native.close(process)
            finally:
                self._finish_handoff(job)

    def _resume(self, root):
        self.native.resume(root.thread)
        root.resumed = True
        if self.native.running(root.handle) is not True:
            raise RuntimeError('Process exited during setup')
        self.native.close(root.thread)
        root.thread = None
        return root

    def is_running(self, root):
        with self._lock:
            return bool(self._job is not None and not self._job.closed and not self._job.cleanup_started and
                        any(item is root for item in self._job.roots) and root.assigned and root.resumed and
                        self.native.running(root.handle) is True and
                        self.native.in_job(root.handle, self._job.handle) is True)

    def wait_root(self, job, root, timeout_ms):
        """Wait on the retained process handle; never open or terminate by PID."""
        with self._lock:
            self._check_job(job)
            if (not any(item is root for item in job.roots) or not root.assigned or not root.resumed or
                    type(timeout_ms) is not int or not 0 <= timeout_ms <= 30000):
                raise RuntimeError('Unknown process or invalid wait')
            return self.native.wait_process(root.handle, timeout_ms)

    def listener_state(self, job, root, role):
        with self._lock:
            self._check_job(job)
            if (self.listener_resolver is None or role not in self.endpoints or
                    not any(item is root and item.role == role and item.resumed for item in job.roots)):
                return 'unknown'
            try:
                address, port = self.endpoints[role]
                return self.listener_resolver.resolve(job.handle, root.handle, address, port)
            except Exception:
                return 'unknown'

    def health_ready(self, role):
        return False

    def stop_containment(self, job):
        with self._handoff_complete:
            self._check_job(job)
            # Once cleanup starts, no thread can resume or borrow these handles.
            job.cleanup_started = True
        deadline = self._clock() + _CLEANUP_TIMEOUT
        with self._handoff_complete:
            if not self._handoff_complete.wait_for(lambda: not job.handoff_active,
                                                   timeout=max(0, deadline - self._clock())):
                return False
        clean = True
        try:
            self.native.terminate_job(job.handle)
        except Exception:
            clean = False
        for root in job.roots:
            if not root.assigned:
                try:
                    if self.native.running(root.handle) is not False:
                        self.native.terminate_process(root.handle)
                except Exception:
                    clean = False
        if not clean or not self._observe_exit(job, deadline):
            return False
        for root in [*job.roots, *job.descendants]:
            if root.thread is not None:
                self.native.close(root.thread)
                root.thread = None
            self.native.close(root.handle)
        self.native.close(job.handle)
        job.closed = True
        return True

    def _observe_exit(self, job, deadline):
        try:
            while self._clock() <= deadline:
                active = self.native.active_processes(job.handle)
                if type(active) is not int or active < 0:
                    return False
                if active == 0 and all(self.native.running(root.handle) is False
                                       for root in [*job.roots, *job.descendants]):
                    return self._clock() <= deadline
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return False
                self._sleep(min(_CLEANUP_POLL_INTERVAL, remaining))
        except Exception:
            return False
        return False


class WindowsNativeAPI:
    """Minimal ctypes bridge; initialization fails closed outside Windows."""
    def __init__(self):
        import os
        if os.name != 'nt':
            raise RuntimeError('Windows Job Objects are unavailable')
        import ctypes as C
        from ctypes import wintypes as W
        self.C, self.W = C, W
        class BasicLimit(C.Structure):
            _fields_ = [('PerProcessUserTimeLimit', C.c_longlong), ('PerJobUserTimeLimit', C.c_longlong),
                ('LimitFlags', W.DWORD), ('MinimumWorkingSetSize', C.c_size_t), ('MaximumWorkingSetSize', C.c_size_t),
                ('ActiveProcessLimit', W.DWORD), ('Affinity', C.c_size_t), ('PriorityClass', W.DWORD), ('SchedulingClass', W.DWORD)]
        class IoCounters(C.Structure):
            _fields_ = [(name, C.c_ulonglong) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class ExtendedLimit(C.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimit), ('IoInfo', IoCounters), ('ProcessMemoryLimit', C.c_size_t),
                        ('JobMemoryLimit', C.c_size_t), ('PeakProcessMemoryUsed', C.c_size_t), ('PeakJobMemoryUsed', C.c_size_t)]
        class StartupInfo(C.Structure):
            _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR), ('lpTitle', W.LPWSTR),
                ('dwX', W.DWORD), ('dwY', W.DWORD), ('dwXSize', W.DWORD), ('dwYSize', W.DWORD),
                ('dwXCountChars', W.DWORD), ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD),
                ('dwFlags', W.DWORD), ('wShowWindow', W.WORD), ('cbReserved2', W.WORD),
                ('lpReserved2', C.POINTER(C.c_ubyte)), ('hStdInput', W.HANDLE), ('hStdOutput', W.HANDLE), ('hStdError', W.HANDLE)]
        class StartupInfoEx(C.Structure):
            _fields_ = [('StartupInfo', StartupInfo), ('lpAttributeList', W.LPVOID)]
        class ProcessInfo(C.Structure):
            _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE), ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]
        class Accounting(C.Structure):
            _fields_ = [('TotalUserTime', C.c_longlong), ('TotalKernelTime', C.c_longlong), ('ThisPeriodTotalUserTime', C.c_longlong),
                ('ThisPeriodTotalKernelTime', C.c_longlong), ('TotalPageFaultCount', W.DWORD), ('TotalProcesses', W.DWORD),
                ('ActiveProcesses', W.DWORD), ('TotalTerminatedProcesses', W.DWORD)]
        class JobProcesses(C.Structure):
            _fields_ = [('assigned', W.DWORD), ('listed', W.DWORD), ('pids', C.c_size_t * 2)]
        self.ExtendedLimit, self.StartupInfo, self.StartupInfoEx = ExtendedLimit, StartupInfo, StartupInfoEx
        self.ProcessInfo, self.Accounting = ProcessInfo, Accounting
        self.JobProcesses = JobProcesses
        k = C.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800); self.k = k
        k.CreateJobObjectW.argtypes = [W.LPVOID, W.LPCWSTR]; k.CreateJobObjectW.restype = W.HANDLE
        k.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD]; k.SetInformationJobObject.restype = W.BOOL
        k.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, W.LPVOID, W.LPVOID, W.BOOL, W.DWORD, W.LPVOID, W.LPCWSTR, C.POINTER(StartupInfo), C.POINTER(ProcessInfo)]; k.CreateProcessW.restype = W.BOOL
        k.InitializeProcThreadAttributeList.argtypes = [W.LPVOID, W.DWORD, W.DWORD, C.POINTER(C.c_size_t)]
        k.InitializeProcThreadAttributeList.restype = W.BOOL
        k.UpdateProcThreadAttribute.argtypes = [W.LPVOID, W.DWORD, C.c_size_t, W.LPVOID, C.c_size_t,
                                             W.LPVOID, C.POINTER(C.c_size_t)]
        k.UpdateProcThreadAttribute.restype = W.BOOL
        k.DeleteProcThreadAttributeList.argtypes = [W.LPVOID]
        k.DeleteProcThreadAttributeList.restype = None
        k.IsProcessInJob.argtypes = [W.HANDLE, W.HANDLE, C.POINTER(W.BOOL)]; k.IsProcessInJob.restype = W.BOOL
        k.ResumeThread.argtypes = [W.HANDLE]; k.ResumeThread.restype = W.DWORD
        k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]; k.WaitForSingleObject.restype = W.DWORD
        k.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]; k.TerminateJobObject.restype = W.BOOL
        k.TerminateProcess.argtypes = [W.HANDLE, W.UINT]; k.TerminateProcess.restype = W.BOOL
        k.QueryInformationJobObject.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, W.LPVOID]; k.QueryInformationJobObject.restype = W.BOOL
        k.CloseHandle.argtypes = [W.HANDLE]; k.CloseHandle.restype = W.BOOL
        k.GetCurrentProcess.argtypes = []; k.GetCurrentProcess.restype = W.HANDLE
        k.DuplicateHandle.argtypes = [W.HANDLE, W.HANDLE, W.HANDLE, C.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD]
        k.DuplicateHandle.restype = W.BOOL
        k.GetProcessId.argtypes = [W.HANDLE]; k.GetProcessId.restype = W.DWORD
        k.GetProcessTimes.argtypes = [W.HANDLE, *([C.POINTER(W.FILETIME)] * 4)]
        k.GetProcessTimes.restype = W.BOOL
        k.GetProcessIdOfThread.argtypes = [W.HANDLE]; k.GetProcessIdOfThread.restype = W.DWORD
        k.GetThreadId.argtypes = [W.HANDLE]; k.GetThreadId.restype = W.DWORD

    def _require(self, value):
        if not value: raise OSError(self.C.get_last_error(), 'Windows process operation failed')

    def create_kill_on_close_job(self):
        job = self.k.CreateJobObjectW(None, None); self._require(job)
        info = self.ExtendedLimit(); info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        try:
            self._require(self.k.SetInformationJobObject(job, 9, self.C.byref(info), self.C.sizeof(info)))
            observed = self.ExtendedLimit()
            self._require(self.k.QueryInformationJobObject(job, 9, self.C.byref(observed),
                                                           self.C.sizeof(observed), None))
            if observed.BasicLimitInformation.LimitFlags != _KILL_ON_JOB_CLOSE:
                raise RuntimeError('Job limits not verified')
        except BaseException:
            self.k.CloseHandle(job)
            raise
        return job

    def create_suspended_in_job(self, job, command):
        """Assign via the Windows 10+ creation attribute, without a sequential fallback."""
        return self._create_with_attributes(job, command, ())

    def create_suspended_observer(self, job, command, bootstrap_handles):
        if len(bootstrap_handles) != 2 or len(set(bootstrap_handles)) != 2:
            raise RuntimeError('Invalid observer inheritance allowlist')
        return self._create_with_attributes(job, command, bootstrap_handles)

    def _create_with_attributes(self, job, command, bootstrap_handles):
        C, k = self.C, self.k
        size = C.c_size_t()
        attribute_count = 2 if bootstrap_handles else 1
        initialized = k.InitializeProcThreadAttributeList(None, attribute_count, 0, C.byref(size))
        if initialized or C.get_last_error() != 122 or not 0 < size.value <= 65536:
            raise RuntimeError('Process job attribute size unavailable')
        attribute_list = C.create_string_buffer(size.value)
        jobs = (self.W.HANDLE * 1)(job)
        info = self.StartupInfoEx()
        info.StartupInfo.cb = C.sizeof(info)
        info.lpAttributeList = C.addressof(attribute_list)
        process = self.ProcessInfo()
        cmd = C.create_unicode_buffer(subprocess.list2cmdline(command))
        self._require(k.InitializeProcThreadAttributeList(attribute_list, attribute_count, 0, C.byref(size)))
        try:
            self._require(k.UpdateProcThreadAttribute(attribute_list, 0, _JOB_LIST_ATTRIBUTE,
                                                     C.byref(jobs), C.sizeof(jobs), None, None))
            if bootstrap_handles:
                handles = (self.W.HANDLE * len(bootstrap_handles))(*bootstrap_handles)
                self._require(k.UpdateProcThreadAttribute(attribute_list, 0, _HANDLE_LIST_ATTRIBUTE,
                                                         C.byref(handles), C.sizeof(handles), None, None))
            self._require(k.CreateProcessW(command[0], cmd, None, None, bool(bootstrap_handles), _SUSPENDED_EXTENDED_STARTUP,
                                           None, None, C.byref(info.StartupInfo), C.byref(process)))
        finally:
            k.DeleteProcThreadAttributeList(attribute_list)
        return process.hProcess, process.hThread

    def create_suspended_descendant(self, command):
        """Cooperative fixture only. Normal child creation inherits job membership."""
        C, k = self.C, self.k
        info = self.StartupInfo()
        info.cb = C.sizeof(info)
        process = self.ProcessInfo()
        cmd = C.create_unicode_buffer(subprocess.list2cmdline(command))
        self._require(k.CreateProcessW(command[0], cmd, None, None, False, 0x08000004,
                                       None, None, C.byref(info), C.byref(process)))
        return process.hProcess, process.hThread

    def _duplicate(self, source, handle, target, access, *, inherit=False):
        duplicate = self.W.HANDLE()
        self._require(self.k.DuplicateHandle(source, handle, target, self.C.byref(duplicate),
                                             access, inherit, 0))
        self._require(duplicate.value)
        return duplicate.value

    def duplicate_observation_job(self, job, observer):
        return self._duplicate(self.k.GetCurrentProcess(), job, observer, _JOB_QUERY)

    def duplicate_observation_process(self, process, observer):
        return self._duplicate(self.k.GetCurrentProcess(), process, observer, _PROCESS_OBSERVE)

    def inheritable_controller_query(self):
        current = self.k.GetCurrentProcess()
        return self._duplicate(current, current, current, _PROCESS_OBSERVE, inherit=True)

    def duplicate_descendant(self, parent, process_slot, thread_slot):
        current = self.k.GetCurrentProcess()
        process = self._duplicate(parent, process_slot, current, _PROCESS_OBSERVE)
        try:
            thread = self._duplicate(parent, thread_slot, current, _THREAD_CONTROL)
        except BaseException:
            self.close(process)
            raise
        return process, thread

    def process_identity(self, handle):
        pid = self.k.GetProcessId(handle)
        self._require(pid)
        created, exited, kernel, user = (self.W.FILETIME() for _ in range(4))
        self._require(self.k.GetProcessTimes(handle, self.C.byref(created), self.C.byref(exited),
                                            self.C.byref(kernel), self.C.byref(user)))
        return pid, (created.dwHighDateTime << 32) | created.dwLowDateTime

    def thread_identity(self, handle):
        pid, tid = self.k.GetProcessIdOfThread(handle), self.k.GetThreadId(handle)
        self._require(pid and tid)
        return pid, tid

    def in_job(self, process, job):
        result = self.W.BOOL(); self._require(self.k.IsProcessInJob(process, job, self.C.byref(result))); return bool(result.value)
    def resume(self, thread):
        if self.k.ResumeThread(thread) != 1: raise RuntimeError('Thread resume was not confirmed')
    def running(self, process):
        status = self.k.WaitForSingleObject(process, 0)
        if status == 0: return False
        if status == 0x102: return True
        raise RuntimeError('Process status unavailable')
    def wait_process(self, process, timeout_ms):
        status = self.k.WaitForSingleObject(process, timeout_ms)
        if status == 0: return True
        if status == 0x102: return False
        raise RuntimeError('Process wait unavailable')
    def terminate_job(self, job): self._require(self.k.TerminateJobObject(job, 1))
    def terminate_process(self, process): self._require(self.k.TerminateProcess(process, 1))
    def active_processes(self, job):
        return self.job_counts(job)[1]

    def job_counts(self, job):
        info = self.Accounting()
        self._require(self.k.QueryInformationJobObject(job, 1, self.C.byref(info), self.C.sizeof(info), None))
        return info.TotalProcesses, info.ActiveProcesses

    def job_process_ids(self, job):
        info = self.JobProcesses()
        self._require(self.k.QueryInformationJobObject(job, 3, self.C.byref(info), self.C.sizeof(info), None))
        if not 0 < info.listed == info.assigned <= len(info.pids):
            raise RuntimeError('Incomplete fixture job process list')
        pids = set(info.pids[:info.listed])
        if len(pids) != info.listed or any(not 0 < pid <= 0xffffffff for pid in pids):
            raise RuntimeError('Invalid fixture job process list')
        return pids
    def close(self, handle): self._require(self.k.CloseHandle(handle))
