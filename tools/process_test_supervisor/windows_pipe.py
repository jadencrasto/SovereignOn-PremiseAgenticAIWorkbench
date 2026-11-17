"""One-shot, local Win32 transport for the R5.5 fixture."""
import ctypes as C
from ctypes import wintypes as W
from math import ceil, isfinite
import os
import re
import struct
from threading import Event, Lock
import time

from tools.process_test_supervisor.handoff import HandoffError, PeerPrincipal, _MAX_FRAME
from backend.security.provenance_windows_native import binary_sid


_INVALID = C.c_void_p(-1).value
_PIPE_NAME = re.compile(r'\\\\\.\\pipe\\SovereignAI-R55-[0-9a-f]{64}')
_LOGON_SID = re.compile(r'S-1-5-5-(?:0|[1-9][0-9]{0,9})-(?:0|[1-9][0-9]{0,9})')
# Individual rights omit FILE_CREATE_PIPE_INSTANCE, which shares the append bit.
_CLIENT_ACCESS = 0x00100083
# Server creation requests generic read/write under the dedicated logon.
# First-instance plus one instance prevents other opens as a second server.
_LOGON_ACCESS = 0x0012019f
_OVERLAPPED = 0x40000000
_PENDING = 997
_WAIT_TIMEOUT = 258
_WAIT_SLICE_MS = 25
_DRAIN_MS = 100
_UNDRAINED_ERRORS = {996, _PENDING, _WAIT_TIMEOUT}
# Keep the kernel's buffer and OVERLAPPED addresses alive until guest process exit.
_undrained = []


class _Security(C.Structure):
    _fields_ = [('length', W.DWORD), ('descriptor', W.LPVOID), ('inherit', W.BOOL)]


class _Overlapped(C.Structure):
    _fields_ = [('internal', C.c_size_t), ('internal_high', C.c_size_t),
                ('offset', W.DWORD), ('offset_high', W.DWORD), ('event', W.HANDLE)]


class _SidAndAttributes(C.Structure):
    _fields_ = [('sid', W.LPVOID), ('attributes', W.DWORD)]


class _LogonGroups(C.Structure):
    _fields_ = [('count', W.DWORD), ('group', _SidAndAttributes)]


def pipe_descriptor(logon_sid):
    if (type(logon_sid) is not str or not _LOGON_SID.fullmatch(logon_sid)
            or any(int(part) > 0xffffffff for part in logon_sid.split('-')[-2:])):
        raise HandoffError('Expected a dedicated logon SID')
    return f'D:P(A;;GA;;;SY)(A;;0x{_LOGON_ACCESS:x};;;{logon_sid})'


def _bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


def _check(result, category):
    if not result:
        raise OSError(C.get_last_error(), category)


class WindowsPipeAPI:
    def __init__(self, process, *, clock=time.monotonic):
        if os.name != 'nt':
            raise HandoffError('Windows named pipes unavailable')
        self._clock = clock
        self.process = process
        self._k = k = C.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
        self._a = a = C.WinDLL('advapi32.dll', use_last_error=True, winmode=0x800)
        dword, ptr, handle, boolean = W.DWORD, W.LPVOID, W.HANDLE, W.BOOL
        out_dword = C.POINTER(dword)
        ov = C.POINTER(_Overlapped)
        _bind(a, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', boolean,
              W.LPCWSTR, dword, C.POINTER(ptr), out_dword)
        _bind(k, 'LocalFree', ptr, ptr)
        _bind(k, 'CreateNamedPipeW', handle, W.LPCWSTR, dword, dword, dword, dword, dword, dword,
              C.POINTER(_Security))
        _bind(k, 'CreateFileW', handle, W.LPCWSTR, dword, dword, ptr, dword, dword, handle)
        _bind(k, 'ConnectNamedPipe', boolean, handle, ov)
        _bind(k, 'CreateEventW', handle, ptr, boolean, boolean, W.LPCWSTR)
        _bind(k, 'CreatePipe', boolean, C.POINTER(handle), C.POINTER(handle), ptr, dword)
        _bind(k, 'SetHandleInformation', boolean, handle, dword, dword)
        _bind(k, 'PeekNamedPipe', boolean, handle, ptr, dword, out_dword, out_dword, out_dword)
        for name in ('ReadFile', 'WriteFile'):
            _bind(k, name, boolean, handle, ptr, dword, out_dword, ov)
        _bind(k, 'GetOverlappedResultEx', boolean, handle, ov, out_dword, dword, boolean)
        _bind(k, 'CancelIoEx', boolean, handle, ov)
        _bind(k, 'CloseHandle', boolean, handle)
        for name in ('GetNamedPipeClientProcessId', 'GetNamedPipeServerProcessId'):
            _bind(k, name, boolean, handle, out_dword)
        _bind(k, 'QueryFullProcessImageNameW', boolean, handle, dword, W.LPWSTR, out_dword)
        _bind(k, 'GetCurrentThread', handle)
        _bind(k, 'GetCurrentProcess', handle)
        _bind(k, 'OpenProcess', handle, dword, boolean, dword)
        _bind(a, 'OpenProcessToken', boolean, handle, dword, C.POINTER(handle))
        _bind(a, 'OpenThreadToken', boolean, handle, dword, boolean, C.POINTER(handle))
        _bind(a, 'GetTokenInformation', boolean, handle, dword, ptr, dword, out_dword)
        _bind(a, 'ImpersonateNamedPipeClient', boolean, handle)
        _bind(a, 'RevertToSelf', boolean)

    def close(self, handle):
        if not any(item[0] == handle for item in _undrained):
            _check(self._k.CloseHandle(handle), 'Pipe handle close failed')
        else:
            raise HandoffError('Pipe cancellation completion unverified')

    def server(self, name, logon_sid):
        descriptor = W.LPVOID()
        _check(self._a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            pipe_descriptor(logon_sid), 1, C.byref(descriptor), None), 'Pipe DACL conversion failed')
        handle = None
        try:
            attrs = _Security(C.sizeof(_Security), descriptor, False)
            # Byte mode, local only, duplex, one instance and no reopen.
            handle = self._k.CreateNamedPipeW(name, _OVERLAPPED | 0x80000 | 3, 8, 1,
                                              8192, 8192, 0, C.byref(attrs))
            _check(handle and handle != _INVALID, 'Local pipe creation failed')
            return handle
        finally:
            if self._k.LocalFree(descriptor):
                if handle and handle != _INVALID:
                    self.close(handle)
                raise HandoffError('Pipe descriptor release failed')

    def client(self, name):
        # Explicit SecurityIdentification and static tracking. No remote path.
        handle = self._k.CreateFileW(name, _CLIENT_ACCESS, 0, None, 3,
                                     _OVERLAPPED | 0x100000 | 0x10000, None)
        _check(handle and handle != _INVALID, 'Local pipe open failed')
        return handle

    def bootstrap_reader(self, packet):
        if type(packet) is not bytes or not 4 < len(packet) <= _MAX_FRAME + 4:
            raise HandoffError('Invalid observer bootstrap frame')
        reader, writer = W.HANDLE(), W.HANDLE()
        _check(self._k.CreatePipe(C.byref(reader), C.byref(writer), None, 8192),
               'Observer bootstrap pipe creation failed')
        try:
            try:
                count = W.DWORD()
                _check(self._k.WriteFile(writer, packet, len(packet), C.byref(count), None)
                       and count.value == len(packet), 'Observer bootstrap write failed')
            finally:
                self.close(writer.value)
            _check(self._k.SetHandleInformation(reader, 1, 1), 'Observer bootstrap inheritance failed')
            return reader.value
        except BaseException:
            self.close(reader.value)
            raise

    def read_bootstrap(self, handle):
        size, count = W.DWORD(), W.DWORD()
        _check(self._k.PeekNamedPipe(handle, None, 0, None, C.byref(size), None)
               and 4 < size.value <= _MAX_FRAME + 4, 'Observer bootstrap frame unavailable')
        data = C.create_string_buffer(size.value)
        _check(self._k.ReadFile(handle, data, size, C.byref(count), None) and count.value == size.value,
               'Observer bootstrap read failed')
        return data.raw

    def io(self, handle, operation, value, deadline, cancelled):
        if cancelled() or self._clock() >= deadline:
            raise HandoffError('Pipe operation cancelled or expired')
        event = self._k.CreateEventW(None, True, False, None)
        _check(event, 'Pipe event creation failed')
        state = _Overlapped(event=event)
        count = W.DWORD()
        buffer = None
        pending = True
        try:
            if operation == 'connect':
                result = self._k.ConnectNamedPipe(handle, C.byref(state))
            else:
                if operation == 'read':
                    buffer = C.create_string_buffer(value)
                    call = self._k.ReadFile
                else:
                    buffer = C.create_string_buffer(value, len(value))
                    call = self._k.WriteFile
                result = call(handle, buffer, len(buffer), None, C.byref(state))
            error = 0 if result else C.get_last_error()
            if not result and error == _PENDING:
                while True:
                    remaining = deadline - self._clock()
                    if cancelled() or remaining <= 0:
                        raise HandoffError('Pipe operation cancelled or expired')
                    ms = min(_WAIT_SLICE_MS, ceil(remaining * 1000))
                    if self._k.GetOverlappedResultEx(handle, C.byref(state), C.byref(count), ms, False):
                        pending = False
                        break
                    error = C.get_last_error()
                    if error in _UNDRAINED_ERRORS:
                        continue
                    pending = False
                    raise OSError(error, 'Pipe operation failed')
            else:
                pending = False
                if not result and not (operation == 'connect' and error == 535):
                    raise OSError(error, 'Pipe operation failed')
                if result and operation != 'connect':
                    _check(self._k.GetOverlappedResultEx(handle, C.byref(state), C.byref(count), 0, False),
                           'Completed pipe byte count unavailable')
            if cancelled() or self._clock() >= deadline:
                raise HandoffError('Pipe operation cancelled or expired')
            if operation == 'read':
                return buffer.raw[:count.value]
            return count.value
        finally:
            if pending:
                try:
                    self._k.CancelIoEx(handle, C.byref(state))
                    drained = self._k.GetOverlappedResultEx(handle, C.byref(state), C.byref(count), _DRAIN_MS, False)
                    pending = not drained and C.get_last_error() in _UNDRAINED_ERRORS
                finally:
                    if pending:
                        _undrained.append((handle, event, state, buffer))
            if not pending:
                self.close(event)

    def pipe_peer_pid(self, transport, remote):
        pid = W.DWORD()
        query = self._k.GetNamedPipeClientProcessId if remote == 'client' else self._k.GetNamedPipeServerProcessId
        _check(query(transport.handle, C.byref(pid)) and pid.value, 'Pipe peer PID unavailable')
        return pid.value

    def current_process(self):
        return self._k.GetCurrentProcess()

    def running(self, handle):
        return self.process.running(handle)

    def process_identity(self, handle):
        return self.process.process_identity(handle)

    def open_peer(self, pid):
        handle = self._k.OpenProcess(0x101000, False, pid)
        _check(handle and handle != _INVALID, 'Peer handle unavailable')
        return handle

    def process_image(self, handle):
        image = C.create_unicode_buffer(32768)
        size = W.DWORD(len(image))
        _check(self._k.QueryFullProcessImageNameW(handle, 0, image, C.byref(size))
               and 0 < size.value < len(image), 'Peer image unavailable')
        return image[:size.value]

    def _token_info(self, token, kind, buffer):
        used = W.DWORD()
        _check(self._a.GetTokenInformation(token, kind, C.byref(buffer), C.sizeof(buffer), C.byref(used))
               and 0 < used.value <= C.sizeof(buffer), 'Peer token evidence unavailable')
        return used.value

    @staticmethod
    def _sid(buffer, used, offset, minimum):
        base = C.addressof(buffer)
        pointer = W.LPVOID.from_buffer(buffer, offset).value
        if not pointer or not base + minimum <= pointer <= base + used - 8:
            raise HandoffError('Peer token SID pointer invalid')
        length = 8 + 4 * C.string_at(pointer, 8)[1]
        if length > min(68, base + used - pointer):
            raise HandoffError('Peer token SID length invalid')
        return binary_sid(C.string_at(pointer, length))

    def _principal(self, token):
        user, logon = C.create_string_buffer(128), C.create_string_buffer(128)
        session = W.DWORD()
        user_bytes = self._token_info(token, 1, user)
        logon_bytes = self._token_info(token, 28, logon)
        session_bytes = self._token_info(token, 12, session)
        _check(user_bytes >= C.sizeof(_SidAndAttributes) and logon_bytes >= C.sizeof(_LogonGroups)
               and session_bytes == C.sizeof(session), 'Peer token layout invalid')
        groups = _LogonGroups.from_buffer(logon)
        _check(groups.count == 1 and groups.group.attributes & 0xc0000000 == 0xc0000000,
               'Peer logon SID unavailable')
        return PeerPrincipal(
            self._sid(user, user_bytes, 0, C.sizeof(_SidAndAttributes)),
            self._sid(logon, logon_bytes, _LogonGroups.group.offset, C.sizeof(_LogonGroups)),
            session.value,
        )

    def process_principal(self, process):
        token = W.HANDLE()
        _check(self._a.OpenProcessToken(process, 8, C.byref(token)) and token.value,
               'Peer process token unavailable')
        try:
            return self._principal(token.value)
        finally:
            self.close(token.value)

    def pipe_client_principal(self, transport):
        # Called by the single reader of this handle after reading its frame.
        _check(self._a.ImpersonateNamedPipeClient(transport.handle), 'Pipe token impersonation failed')
        token = W.HANDLE()
        try:
            _check(self._a.OpenThreadToken(self._k.GetCurrentThread(), 8, True, C.byref(token))
                   and token.value, 'Pipe client token unavailable')
        finally:
            if not self._a.RevertToSelf():
                # Returning under the client's identity would expose subsequent
                # privileged work. The independent guest watchdog owns recovery.
                os._exit(125)
        try:
            return self._principal(token.value)
        finally:
            self.close(token.value)


class NativePipe:
    """Exclusive byte channel. A shutdown requests cancellation before close."""
    def __init__(self, api, handle, *, server, clock=time.monotonic):
        self._api, self.handle = api, handle
        self._clock = clock
        self._cancel = Event()
        self._lock = Lock()
        self._attempted = not server
        self._connected = not server
        self._peer_alive = None

    @classmethod
    def listen(cls, api, name, logon_sid, *, clock=time.monotonic):
        if type(name) is not str or not _PIPE_NAME.fullmatch(name):
            raise HandoffError('Invalid local pipe name')
        pipe_descriptor(logon_sid)
        return cls(api, api.server(name, logon_sid), server=True, clock=clock)

    @classmethod
    def connect(cls, api, name, deadline, *, clock=time.monotonic):
        if (type(name) is not str or not _PIPE_NAME.fullmatch(name) or not isfinite(deadline)
                or clock() >= deadline):
            raise HandoffError('Invalid local pipe connect')
        # The single server instance is created before the peer starts.
        pipe = cls(api, api.client(name), server=False, clock=clock)
        if clock() >= deadline:
            pipe.close()
            raise HandoffError('Pipe connect expired')
        return pipe

    def pin_liveness(self, probe):
        self._peer_alive = probe

    def cancel(self):
        self._cancel.set()

    def _cancelled(self):
        return self._cancel.is_set() or (self._peer_alive is not None and self._peer_alive() is not True)

    def connected(self):
        return self._connected and not self._cancelled()

    def _io(self, operation, value, deadline):
        if not isfinite(deadline) or self._cancelled() or self._clock() >= deadline:
            raise HandoffError('Pipe operation cancelled or expired')
        return self._api.io(self.handle, operation, value, deadline, self._cancelled)

    def accept(self, deadline):
        with self._lock:
            if self._attempted:
                raise HandoffError('Pipe accept is single-use')
            self._attempted = True
            self._io('connect', None, deadline)
            self._connected = True

    def send(self, frame, deadline):
        if not self.connected() or type(frame) is not bytes or not 4 < len(frame) <= _MAX_FRAME + 4:
            raise HandoffError('Pipe frame send rejected')
        with self._lock:
            try:
                if self._io('write', frame, deadline) != len(frame):
                    raise HandoffError('Partial pipe write')
            except BaseException:
                self.cancel()
                raise

    def _read_exact(self, size, deadline):
        data = bytearray()
        while len(data) < size:
            part = self._io('read', size - len(data), deadline)
            if not part or len(part) > size - len(data):
                raise HandoffError('Pipe read incomplete')
            data.extend(part)
        return bytes(data)

    def receive(self, deadline):
        if not self.connected():
            raise HandoffError('Pipe receive unavailable')
        with self._lock:
            try:
                prefix = self._read_exact(4, deadline)
                size = struct.unpack('<I', prefix)[0]
                if not 0 < size <= _MAX_FRAME:
                    raise HandoffError('Pipe frame size invalid')
                return prefix + self._read_exact(size, deadline)
            except BaseException:
                self.cancel()
                raise

    def close(self):
        self.cancel()
        if not self._lock.acquire(timeout=0.5):
            raise HandoffError('Pipe close unverified')
        try:
            if self.handle is not None:
                handle, self.handle = self.handle, None
                self._api.close(handle)
        finally:
            self._lock.release()
