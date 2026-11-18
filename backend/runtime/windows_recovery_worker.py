"""Private Windows job launch and inherited admission for supervised workers."""

from backend.runtime.windows_job_driver import WindowsNativeAPI


_DETACHED_SUSPENDED_EXTENDED_STARTUP = 0x00000008 | 0x00000004 | 0x00080000


class WindowsRecoveryWorkerAPI(WindowsNativeAPI):
    """Private, explicitly inherited read endpoint and wait-only stop event."""

    def __init__(self):
        super().__init__()
        k, w, c = self.k, self.W, self.C
        k.CreatePipe.argtypes = [c.POINTER(w.HANDLE), c.POINTER(w.HANDLE), w.LPVOID, w.DWORD]
        k.CreatePipe.restype = w.BOOL
        k.PeekNamedPipe.argtypes = [w.HANDLE, w.LPVOID, w.DWORD, w.LPVOID, c.POINTER(w.DWORD), w.LPVOID]
        k.PeekNamedPipe.restype = w.BOOL
        for operation in (k.ReadFile, k.WriteFile):
            operation.argtypes = [w.HANDLE, w.LPVOID, w.DWORD, c.POINTER(w.DWORD), w.LPVOID]
            operation.restype = w.BOOL
        k.CreateEventW.argtypes = [w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR]
        k.CreateEventW.restype = w.HANDLE
        k.SetEvent.argtypes = [w.HANDLE]
        k.SetEvent.restype = w.BOOL

    def pipe(self):
        c, k, w = self.C, self.k, self.W
        read, write = w.HANDLE(), w.HANDLE()
        self._require(k.CreatePipe(c.byref(read), c.byref(write), None, 4096))
        return read.value, write.value

    def inherit_reader(self, reader):
        current = self.k.GetCurrentProcess()
        inherited = self.W.HANDLE()
        self._require(self.k.DuplicateHandle(current, reader, current, self.C.byref(inherited), 0, True, 2))
        return inherited.value

    def stop_event(self):
        handle = self.k.CreateEventW(None, True, False, None)
        self._require(handle)
        return handle

    def inherit_stop_wait(self, stop):
        current = self.k.GetCurrentProcess()
        return self._duplicate(current, stop, current, 0x00100000, inherit=True)  # SYNCHRONIZE

    def create_worker(self, job, command, reader, stop_wait):
        # A hidden console can still create a conhost.exe in the worker job.
        return self._create_with_attributes(job, command, (reader, stop_wait),
                                            creation_flags=_DETACHED_SUSPENDED_EXTENDED_STARTUP)

    def duplicate_worker_token(self, token):
        current = self.k.GetCurrentProcess()
        return self._duplicate(current, token, current, 0x000B)  # ASSIGN_PRIMARY | DUPLICATE | QUERY

    def create_worker_as(self, job, command, reader, stop_wait, token):
        advapi = self.C.WinDLL('advapi32.dll', use_last_error=True, winmode=0x800)
        create = advapi.CreateProcessAsUserW
        create.argtypes = [self.W.HANDLE, *self.k.CreateProcessW.argtypes]
        create.restype = self.W.BOOL

        def create_with_token(*args):
            return create(token, *args)

        return self._create_with_attributes(job, command, (reader, stop_wait), create_process=create_with_token,
                                            creation_flags=_DETACHED_SUSPENDED_EXTENDED_STARTUP)

    def write_ticket(self, handle, data):
        if len(data) != 32:
            raise RuntimeError('Invalid worker admission')
        count = self.W.DWORD()
        self._require(self.k.WriteFile(handle, self.C.create_string_buffer(data), len(data), self.C.byref(count), None))
        if count.value != len(data):
            raise RuntimeError('Incomplete worker admission')

    def read_ticket(self, handle):
        c, k, w = self.C, self.k, self.W
        ready, count, data = w.DWORD(), w.DWORD(), c.create_string_buffer(32)
        # The writer is closed before resume. Peek prevents blocking on short or foreign inputs.
        if not k.PeekNamedPipe(handle, None, 0, None, c.byref(ready), None) or ready.value != 32:
            raise RuntimeError('Invalid worker admission channel')
        self._require(k.ReadFile(handle, data, 32, c.byref(count), None))
        if (count.value != 32 or k.PeekNamedPipe(handle, None, 0, None, c.byref(ready), None)
                or c.get_last_error() != 109):  # ERROR_BROKEN_PIPE after the only writer closed
            raise RuntimeError('Unclosed or invalid worker admission channel')
        return data.raw

    def signal_stop(self, handle):
        self._require(self.k.SetEvent(handle))
