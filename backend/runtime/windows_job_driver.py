"""Experimental Windows job driver; not connected to application startup.

Native execution and containment have not been verified. Readiness deliberately
fails closed until a trusted listener-identity implementation is available.
"""
from dataclasses import dataclass, field
from pathlib import Path
import subprocess


@dataclass
class OwnedRoot:
    handle: object
    thread: object
    role: str
    assigned: bool = False


@dataclass
class OwnedJob:
    handle: object
    roots: list = field(default_factory=list)
    closed: bool = False
    cleanup_started: bool = False


class WindowsJobDriver:
    """Coordinator adapter. `native` can be replaced only for isolated unit tests.

    All termination goes through held process/job handles, never a PID. A job
    with uncertain cleanup remains open to avoid reporting false success.
    """
    def __init__(self, commands, native=None, *, endpoints=None, listener_resolver=None):
        self.native = native if native is not None else WindowsNativeAPI()
        self.commands = dict(commands)
        if set(self.commands) != {'backend', 'frontend'}:
            raise ValueError('Two explicit synthetic commands are required')
        for command in self.commands.values():
            if not command or not Path(command[0]).is_absolute() or any(not isinstance(x, str) or '\x00' in x for x in command):
                raise ValueError('Absolute executable and valid arguments required')
        self._job = None
        self.endpoints = dict(endpoints or {})
        self.listener_resolver = listener_resolver
        if self.endpoints and set(self.endpoints) != {'backend', 'frontend'}:
            raise ValueError('Both listener endpoints must be specified')
        if self.endpoints and self.listener_resolver is None:
            from backend.runtime.windows_listener import ListenerResolver, WindowsListenerAPI
            self.listener_resolver = ListenerResolver(WindowsListenerAPI(self.native))

    def open_containment(self):
        if self._job is not None:
            raise RuntimeError('A containment is already open')
        handle = self.native.create_kill_on_close_job()
        if not handle:
            raise RuntimeError('Job creation failed')
        self._job = OwnedJob(handle)
        return self._job

    def _check_job(self, job):
        if job is not self._job or job.closed or job.cleanup_started:
            raise RuntimeError('Unknown containment')

    def start_contained(self, job, role, *, before_resume=None):
        self._check_job(job)
        if role not in self.commands or any(root.role == role for root in job.roots):
            raise RuntimeError('Unknown or duplicate process role')
        process, thread = self.native.create_suspended(self.commands[role])
        root = OwnedRoot(process, thread, role)
        job.roots.append(root)
        # On any exception coordinator cleanup retains the process handle even
        # when assignment failed. User code has not run while still suspended.
        self.native.assign(job.handle, root.handle)
        root.assigned = True
        if not self.native.in_job(root.handle, job.handle):
            raise RuntimeError('Job assignment not verified')
        if not self.native.running(root.handle):
            raise RuntimeError('Process exited during setup')
        if before_resume is not None and before_resume() is not True:
            raise RuntimeError('Resume authorization unavailable')
        self.native.resume(root.thread)
        if not self.native.running(root.handle):
            raise RuntimeError('Process exited during setup')
        self.native.close(root.thread)
        root.thread = None
        return root

    def is_running(self, root):
        return bool(self._job is not None and not self._job.closed and not self._job.cleanup_started and
                    any(item is root for item in self._job.roots) and root.assigned and
                    self.native.running(root.handle) and self.native.in_job(root.handle, self._job.handle))

    def wait_root(self, job, root, timeout_ms):
        """Wait on the retained process handle; never open or terminate by PID."""
        self._check_job(job)
        if (not any(item is root for item in job.roots) or not root.assigned or
                type(timeout_ms) is not int or not 0 <= timeout_ms <= 30000):
            raise RuntimeError('Unknown process or invalid wait')
        return self.native.wait_process(root.handle, timeout_ms)

    def listener_state(self, job, root, role):
        self._check_job(job)
        if (self.listener_resolver is None or role not in self.endpoints or
                not any(item is root and item.role == role for item in job.roots)):
            return 'unknown'
        try:
            address, port = self.endpoints[role]
            return self.listener_resolver.resolve(job.handle, root.handle, address, port)
        except Exception:
            return 'unknown'

    def health_ready(self, role):
        return False

    def stop_containment(self, job):
        self._check_job(job)
        # Once cleanup starts, never reuse potentially closed handles or admit
        # more roots, including when a native close or termination fails.
        job.cleanup_started = True
        clean = True
        try:
            self.native.terminate_job(job.handle)
        except Exception:
            clean = False
        for root in job.roots:
            if not root.assigned:
                try:
                    self.native.terminate_process(root.handle)
                except Exception:
                    clean = False
        try:
            clean = (self.native.active_processes(job.handle) == 0 and clean)
            for root in job.roots:
                if self.native.running(root.handle): clean = False
        except Exception:
            clean = False
        if not clean:
            return False
        for root in job.roots:
            if root.thread is not None:
                self.native.close(root.thread)
                root.thread = None
            self.native.close(root.handle)
        self.native.close(job.handle)
        job.closed = True
        return True


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
        class ProcessInfo(C.Structure):
            _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE), ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]
        class Accounting(C.Structure):
            _fields_ = [('TotalUserTime', C.c_longlong), ('TotalKernelTime', C.c_longlong), ('ThisPeriodTotalUserTime', C.c_longlong),
                ('ThisPeriodTotalKernelTime', C.c_longlong), ('TotalPageFaultCount', W.DWORD), ('TotalProcesses', W.DWORD),
                ('ActiveProcesses', W.DWORD), ('TotalTerminatedProcesses', W.DWORD)]
        self.ExtendedLimit, self.StartupInfo, self.ProcessInfo, self.Accounting = ExtendedLimit, StartupInfo, ProcessInfo, Accounting
        k = C.WinDLL('kernel32', use_last_error=True); self.k = k
        k.CreateJobObjectW.argtypes = [W.LPVOID, W.LPCWSTR]; k.CreateJobObjectW.restype = W.HANDLE
        k.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD]; k.SetInformationJobObject.restype = W.BOOL
        k.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, W.LPVOID, W.LPVOID, W.BOOL, W.DWORD, W.LPVOID, W.LPCWSTR, C.POINTER(StartupInfo), C.POINTER(ProcessInfo)]; k.CreateProcessW.restype = W.BOOL
        k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]; k.AssignProcessToJobObject.restype = W.BOOL
        k.IsProcessInJob.argtypes = [W.HANDLE, W.HANDLE, C.POINTER(W.BOOL)]; k.IsProcessInJob.restype = W.BOOL
        k.ResumeThread.argtypes = [W.HANDLE]; k.ResumeThread.restype = W.DWORD
        k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]; k.WaitForSingleObject.restype = W.DWORD
        k.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]; k.TerminateJobObject.restype = W.BOOL
        k.TerminateProcess.argtypes = [W.HANDLE, W.UINT]; k.TerminateProcess.restype = W.BOOL
        k.QueryInformationJobObject.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, W.LPVOID]; k.QueryInformationJobObject.restype = W.BOOL
        k.CloseHandle.argtypes = [W.HANDLE]; k.CloseHandle.restype = W.BOOL

    def _require(self, value):
        if not value: raise OSError(self.C.get_last_error(), 'Windows process operation failed')

    def create_kill_on_close_job(self):
        job = self.k.CreateJobObjectW(None, None); self._require(job)
        info = self.ExtendedLimit(); info.BasicLimitInformation.LimitFlags = 0x2000
        try: self._require(self.k.SetInformationJobObject(job, 9, self.C.byref(info), self.C.sizeof(info)))
        except BaseException:
            self.k.CloseHandle(job)
            raise
        return job

    def create_suspended(self, command):
        info = self.StartupInfo(); info.cb = self.C.sizeof(info); process = self.ProcessInfo()
        cmd = self.C.create_unicode_buffer(subprocess.list2cmdline(command))
        self._require(self.k.CreateProcessW(command[0], cmd, None, None, False, 0x00000004 | 0x08000000,
                                           None, None, self.C.byref(info), self.C.byref(process)))
        return process.hProcess, process.hThread

    def assign(self, job, process): self._require(self.k.AssignProcessToJobObject(job, process))
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
        info = self.Accounting(); self._require(self.k.QueryInformationJobObject(job, 1, self.C.byref(info), self.C.sizeof(info), None)); return info.ActiveProcesses
    def close(self, handle): self._require(self.k.CloseHandle(handle))
