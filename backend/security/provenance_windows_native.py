"""Lazy Windows bindings for protected provenance. No native calls on import."""
from __future__ import annotations

import ctypes as C
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PureWindowsPath

from backend.security.provenance_keystore import SERVICE_IDENTITY, valid_service_sid

SYSTEM_SID = "S-1-5-18"
FILE_ALL_ACCESS = 0x001F01FF
DIRECTORY = 0x10
UNSAFE_ATTRIBUTES = 0x00441440  # Recall-on-data/open, offline, reparse point and device.

_DWORD = C.c_uint32
_LONG = C.c_int32
_WORD = C.c_uint16
_HANDLE = C.c_void_p
_PTR = C.c_void_p
_WSTRING = C.c_wchar_p
_READ_ATTRIBUTES = 0x80
_READ_CONTROL = 0x20000
_SYNCHRONIZE = 0x100000
_INVALID_HANDLE = _HANDLE(-1).value
_FILE_SHARE_READ = 1
_ERROR_NO_TOKEN = 1008
_NOT_A_CLOUD_FILE = 0x80070178
_VOLUME_ROOT = re.compile(r"\\\\\?\\Volume\{[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}\\",
                          re.IGNORECASE).fullmatch


class _Luid(C.Structure):
    _fields_ = [("low", _DWORD), ("high", _LONG)]


class _TokenStatistics(C.Structure):
    _fields_ = [
        ("token_id", _Luid), ("authentication_id", _Luid), ("expires", C.c_int64),
        ("token_type", _DWORD), ("impersonation", _DWORD), ("charged", _DWORD),
        ("available", _DWORD), ("groups", _DWORD), ("privileges", _DWORD),
        ("modified", _Luid),
    ]


class _UnicodeString(C.Structure):
    _fields_ = [("length", _WORD), ("capacity", _WORD), ("buffer", _WSTRING)]


class _LogonSession(C.Structure):
    _fields_ = [
        ("size", _DWORD), ("logon_id", _Luid), ("user", _UnicodeString),
        ("domain", _UnicodeString), ("package", _UnicodeString), ("type", _DWORD),
        ("session", _DWORD), ("sid", _PTR),
    ]


class _ObjectAttributes(C.Structure):
    _fields_ = [
        ("size", _DWORD), ("root", _HANDLE), ("name", C.POINTER(_UnicodeString)),
        ("attributes", _DWORD), ("security_descriptor", _PTR), ("security_qos", _PTR),
    ]


class _IoStatus(C.Structure):
    _fields_ = [("status_or_pointer", _PTR), ("information", C.c_size_t)]


class _FileInformation(C.Structure):
    _fields_ = [
        ("attributes", _DWORD), ("times", _DWORD * 6), ("volume", _DWORD),
        ("size_high", _DWORD), ("size_low", _DWORD), ("links", _DWORD),
        ("index_high", _DWORD), ("index_low", _DWORD),
    ]


class _AclSize(C.Structure):
    _fields_ = [("ace_count", _DWORD), ("in_use", _DWORD), ("free", _DWORD)]


@dataclass(frozen=True)
class TokenFacts:
    sid: str
    primary: bool
    logon_sid: str
    logon_type: int


@dataclass(frozen=True)
class FileFacts:
    path: PureWindowsPath
    volume: int
    attributes: int
    links: int
    size: int


@dataclass(frozen=True, repr=False)
class FileSecurity:
    owner: str
    control: int
    dacl: bytes | None


def binary_sid(data: bytes) -> str:
    if len(data) < 8 or data[0] != 1 or not 0 < data[1] <= 15 or len(data) != 8 + 4 * data[1]:
        raise ValueError
    authority = int.from_bytes(data[2:8], "big")
    parts = [int.from_bytes(data[i:i + 4], "little") for i in range(8, len(data), 4)]
    return "S-" + "-".join(map(str, (data[0], authority, *parts)))


def _require(condition) -> None:
    if not condition:
        raise OSError("windows_keystore_operation_rejected")


class WindowsKeyStoreNative:
    """Win32 and ntdll operations, called only by explicit composition."""

    def __init__(self):
        self._libraries = {}

    def _function(self, library, name, result, *args):
        _require(sys.platform == "win32")
        if library not in self._libraries:
            self._libraries[library] = C.WinDLL(library + ".dll", winmode=0x800, use_last_error=True)
        function = getattr(self._libraries[library], name)
        function.restype = result
        function.argtypes = args
        return function

    def close(self, handle):
        _require(self._function("kernel32", "CloseHandle", _LONG, _HANDLE)(handle))

    def _local_free(self, pointer):
        _require(not self._function("kernel32", "LocalFree", _PTR, _PTR)(pointer))

    def open_thread_token(self):
        thread = self._function("kernel32", "GetCurrentThread", _HANDLE)()
        token = _HANDLE()
        open_token = self._function("advapi32", "OpenThreadToken", _LONG, _HANDLE, _DWORD, _LONG, _PTR)
        if open_token(thread, 8, True, C.byref(token)):  # TOKEN_QUERY, OpenAsSelf
            _require(token.value and token.value != _INVALID_HANDLE)
            return token.value
        _require(C.get_last_error() == _ERROR_NO_TOKEN)
        return None

    def open_process_token(self):
        process = self._function("kernel32", "GetCurrentProcess", _HANDLE)()
        token = _HANDLE()
        open_token = self._function("advapi32", "OpenProcessToken", _LONG, _HANDLE, _DWORD, _PTR)
        _require(open_token(process, 8, C.byref(token)) and token.value and token.value != _INVALID_HANDLE)
        return token.value

    def _copy_sid(self, pointer, limit=68):
        _require(pointer and limit >= 8)
        header = C.string_at(pointer, 8)
        length = 8 + 4 * header[1]
        _require(length <= min(68, limit))
        return binary_sid(C.string_at(pointer, length))

    def _token_info(self, handle, info_class, output):
        query = self._function("advapi32", "GetTokenInformation", _LONG, _HANDLE, _DWORD, _PTR, _DWORD, _PTR)
        used = _DWORD()
        _require(query(handle, info_class, C.byref(output), C.sizeof(output), C.byref(used)))
        _require(0 < used.value <= C.sizeof(output))
        return used.value

    def token_facts(self, handle):
        user = C.create_string_buffer(128)
        size = self._token_info(handle, 1, user)  # TokenUser (SID_AND_ATTRIBUTES)
        _require(size >= 2 * C.sizeof(_PTR))
        sid_pointer = _PTR.from_buffer(user).value
        base = C.addressof(user)
        _require(sid_pointer and base + 2 * C.sizeof(_PTR) <= sid_pointer < base + size)
        sid = self._copy_sid(sid_pointer, base + size - sid_pointer)

        stats = _TokenStatistics()
        _require(self._token_info(handle, 10, stats) == C.sizeof(stats))  # TokenStatistics
        data = C.POINTER(_LogonSession)()
        get_session = self._function("secur32", "LsaGetLogonSessionData", _LONG, _PTR, _PTR)
        free_session = self._function("secur32", "LsaFreeReturnBuffer", _LONG, _PTR)
        _require(get_session(C.byref(stats.authentication_id), C.byref(data)) == 0 and data)
        try:
            _require(data.contents.size >= C.sizeof(_LogonSession))
            return TokenFacts(sid, stats.token_type == 1, self._copy_sid(data.contents.sid), data.contents.type)
        finally:
            _require(free_session(data) == 0)

    def service_sid(self, virtual: bool):
        domain = "NT SERVICE"
        if not virtual:
            computer = C.create_unicode_buffer(16)  # MAX_COMPUTERNAME_LENGTH + 1
            length = _DWORD(len(computer))
            get_computer = self._function("kernel32", "GetComputerNameW", _LONG, _WSTRING, _PTR)
            _require(get_computer(computer, C.byref(length)) and computer.value)
            domain = computer.value
        account = domain + "\\" + SERVICE_IDENTITY
        sid = C.create_string_buffer(68)
        sid_size = _DWORD(len(sid))
        resolved_domain = C.create_unicode_buffer(256)
        domain_size = _DWORD(len(resolved_domain))
        sid_type = _DWORD()
        lookup = self._function("advapi32", "LookupAccountNameW", _LONG, _WSTRING, _WSTRING,
                                _PTR, _PTR, _WSTRING, _PTR, _PTR)
        _require(lookup(None, account, sid, C.byref(sid_size), resolved_domain, C.byref(domain_size),
                        C.byref(sid_type)))
        _require(resolved_domain.value.casefold() == domain.casefold() and 0 < sid_size.value <= len(sid))
        _require(virtual or sid_type.value == 1)  # A dedicated local account must be SidTypeUser.
        return binary_sid(sid.raw[:sid_size.value])

    def profile_flags(self):
        flags = _DWORD()
        _require(self._function("userenv", "GetProfileType", _LONG, _PTR)(C.byref(flags)))
        return flags.value

    def open_drive(self, path: PureWindowsPath):
        drive_type = self._function("kernel32", "GetDriveTypeW", _DWORD, _WSTRING)
        _require(drive_type(str(path)) == 3)  # DRIVE_FIXED
        create = self._function("kernel32", "CreateFileW", _HANDLE, _WSTRING, _DWORD, _DWORD, _PTR,
                                _DWORD, _DWORD, _HANDLE)
        handle = create("\\\\?\\" + str(path), _READ_ATTRIBUTES | 0x20 | _SYNCHRONIZE, _FILE_SHARE_READ,
                        None, 3, 0x02200000, None)  # Existing, BACKUP_SEMANTICS | OPEN_REPARSE_POINT.
        _require(handle and handle != _INVALID_HANDLE)
        return handle

    def _final_name(self, handle, flags):
        output = C.create_unicode_buffer(32768)
        query = self._function("kernel32", "GetFinalPathNameByHandleW", _DWORD, _HANDLE, _WSTRING, _DWORD, _DWORD)
        length = query(handle, output, len(output), flags)
        _require(0 < length < len(output))
        return output.value

    def volume_root(self, drive):
        filesystem = C.create_unicode_buffer(32)
        query = self._function("kernel32", "GetVolumeInformationByHandleW", _LONG, _HANDLE, _WSTRING, _DWORD,
                               _PTR, _PTR, _PTR, _WSTRING, _DWORD)
        _require(query(drive, None, 0, None, None, None, filesystem, len(filesystem)))
        # A DOS alias into a subdirectory cannot resolve to the actual root in GUID form.
        root = self._final_name(drive, 1)
        _require(filesystem.value == "NTFS" and _VOLUME_ROOT(root) is not None)
        return root.casefold()

    def open_child(self, parent, name: str, directory: bool, *, inspect_acl: bool):
        access = _READ_ATTRIBUTES | _SYNCHRONIZE | (0x20 if directory else 1)
        if inspect_acl:
            access |= _READ_CONTROL
        return self._open_relative(parent, name, access, directory=directory)

    def _open_relative(self, parent, name, access, *, directory=False, descriptor=None):
        # One component per call. FILE_OPEN_REPARSE_POINT applies to that component.
        _require(isinstance(name, str) and name not in ("", ".", "..")
                 and not any(char in name for char in ("\\", "/", ":", "\0")))
        size = len(name.encode("utf-16-le"))
        _require(size <= 510)
        unicode_name = _UnicodeString(size, size + 2, name)
        attributes = _ObjectAttributes(C.sizeof(_ObjectAttributes), parent, C.pointer(unicode_name),
                                       0x40, descriptor, None)  # OBJ_CASE_INSENSITIVE
        status = _IoStatus()
        handle = _HANDLE()
        # The documented FILE_DIRECTORY_FILE options exclude FILE_OPEN_REPARSE_POINT.
        # Inspect FILE_ATTRIBUTE_DIRECTORY on the returned handle before traversing it.
        options = 0x00200000 | 0x20 | (0 if directory else 0x40)
        disposition = 1  # FILE_OPEN
        if descriptor is not None:
            options |= 0x2  # FILE_WRITE_THROUGH
            disposition = 2  # FILE_CREATE (exclusive, no replacement)
        create = self._function("ntdll", "NtCreateFile", _LONG, _PTR, _DWORD, _PTR, _PTR,
                                _PTR, _DWORD, _DWORD, _DWORD, _DWORD, _PTR, _DWORD)
        result = create(C.byref(handle), access, C.byref(attributes), C.byref(status), None, 0,
                        _FILE_SHARE_READ, disposition, options, None, 0)
        _require(handle.value and handle.value != _INVALID_HANDLE)
        if result != 0:
            if result > 0:  # An unexpected successful status may still own a handle.
                self.close(handle.value)
            raise OSError("windows_keystore_operation_rejected")
        return handle.value

    @contextmanager
    def create_record(self, parent, name, service_sid):
        _require(valid_service_sid(service_sid))
        descriptor = _PTR()
        convert = self._function("advapi32", "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                                 _LONG, _WSTRING, _DWORD, _PTR, _PTR)
        acl = f"D:P(A;;FA;;;SY)(A;;FA;;;{service_sid})"
        access = 0x40000000 | _READ_ATTRIBUTES | _READ_CONTROL | _SYNCHRONIZE
        try:
            _require(convert(acl, 1, C.byref(descriptor), None) and descriptor.value)
            handle = self._open_relative(parent, name, access, descriptor=descriptor.value)
            try:
                yield handle
            finally:
                self.close(handle)
        finally:
            if descriptor.value:
                self._local_free(descriptor)

    def file_facts(self, handle):
        info = _FileInformation()
        query = self._function("kernel32", "GetFileInformationByHandle", _LONG, _HANDLE, _PTR)
        _require(query(handle, C.byref(info)))
        path = self._final_name(handle, 0)
        _require(path.startswith("\\\\?\\") and re.match(r"[A-Za-z]:\\", path[4:]))
        return FileFacts(PureWindowsPath(path[4:]), info.volume, info.attributes, info.links,
                         (info.size_high << 32) | info.size_low)

    def outside_cloud_root(self, handle):
        probe = self._function("cldapi", "CfGetSyncRootInfoByHandle", _LONG, _HANDLE, _DWORD,
                               _PTR, _DWORD, _PTR)
        result = probe(handle, 0, C.create_string_buffer(512), 512, None)
        # Unknown failures are unverifiable, including implementation-specific no-root errors.
        return result & 0xFFFFFFFF == _NOT_A_CLOUD_FILE

    def security(self, handle):
        owner, dacl, descriptor = _PTR(), _PTR(), _PTR()
        get_security = self._function("advapi32", "GetSecurityInfo", _DWORD, _HANDLE, _DWORD, _DWORD,
                                      _PTR, _PTR, _PTR, _PTR, _PTR)
        result = get_security(handle, 1, 0x5, C.byref(owner), None, C.byref(dacl), None,
                              C.byref(descriptor))  # SE_FILE_OBJECT, OWNER | DACL
        try:
            _require(result == 0 and descriptor.value)
            control, revision = _WORD(), _DWORD()
            get_control = self._function("advapi32", "GetSecurityDescriptorControl", _LONG, _PTR, _PTR, _PTR)
            _require(get_control(descriptor, C.byref(control), C.byref(revision)) and revision.value == 1)
            raw_acl = None
            if dacl.value:
                size = _AclSize()
                valid = self._function("advapi32", "IsValidAcl", _LONG, _PTR)
                query = self._function("advapi32", "GetAclInformation", _LONG, _PTR, _PTR, _DWORD, _DWORD)
                _require(valid(dacl) and query(dacl, C.byref(size), C.sizeof(size), 2))
                _require(8 <= size.in_use <= 65535)
                raw_acl = C.string_at(dacl, size.in_use)
            return FileSecurity(self._copy_sid(owner.value), control.value, raw_acl)
        finally:
            if descriptor.value:
                self._local_free(descriptor)

    def read(self, handle, count):
        output = C.create_string_buffer(count)
        read = self._function("kernel32", "ReadFile", _LONG, _HANDLE, _PTR, _DWORD, _PTR, _PTR)
        size = _DWORD()
        _require(read(handle, output, count, C.byref(size), None) and size.value <= count)
        return output.raw[:size.value]

    def write(self, handle, raw):
        write = self._function("kernel32", "WriteFile", _LONG, _HANDLE, _PTR, _DWORD, _PTR, _PTR)
        size = _DWORD()
        output = C.create_string_buffer(raw)
        _require(write(handle, output, len(raw), C.byref(size), None) and size.value == len(raw))

    def flush(self, handle):
        _require(self._function("kernel32", "FlushFileBuffers", _LONG, _HANDLE)(handle))
