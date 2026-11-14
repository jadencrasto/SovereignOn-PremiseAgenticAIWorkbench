"""Read-only process-token inspection; no named-pipe or host-policy writes."""
import ctypes
from ctypes import wintypes
import json
import subprocess
import sys

import win32api
import win32con
import win32security

adv = ctypes.WinDLL('advapi32', use_last_error=True)
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
adv.GetTokenInformation.restype = wintypes.BOOL
adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
adv.ConvertSidToStringSidW.restype = wintypes.BOOL
kernel.LocalFree.argtypes = [ctypes.c_void_p]
kernel.LocalFree.restype = ctypes.c_void_p


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Sid', ctypes.c_void_p), ('Attributes', wintypes.DWORD)]


class TOKEN_GROUPS_HEAD(ctypes.Structure):
    _fields_ = [('count', wintypes.DWORD), ('first', SID_AND_ATTRIBUTES)]


def query(token, kind):
    size = wintypes.DWORD()
    adv.GetTokenInformation(int(token), kind, None, 0, ctypes.byref(size))
    if not size.value or size.value > 1048576:
        raise OSError('token information unavailable')
    buf = ctypes.create_string_buffer(size.value)
    if not adv.GetTokenInformation(int(token), kind, buf, size, ctypes.byref(size)):
        raise OSError('token information query failed')
    return buf


def sid_string(pointer):
    if not pointer:
        return None
    value = ctypes.c_wchar_p()
    if not adv.ConvertSidToStringSidW(pointer, ctypes.byref(value)):
        raise OSError('SID conversion failed')
    try:
        return value.value
    finally:
        kernel.LocalFree(ctypes.cast(value, ctypes.c_void_p))


def snapshot():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        package = query(token, 31)
        package_sid = sid_string(ctypes.cast(package, ctypes.POINTER(ctypes.c_void_p))[0])
        capabilities = query(token, 30)
        head = ctypes.cast(capabilities, ctypes.POINTER(TOKEN_GROUPS_HEAD)).contents
        if head.count > 4096:
            raise ValueError('capability count invalid')
        offset = TOKEN_GROUPS_HEAD.first.offset
        needed = offset + head.count * ctypes.sizeof(SID_AND_ATTRIBUTES)
        if needed > ctypes.sizeof(capabilities):
            raise ValueError('capability buffer incomplete')
        entries = (SID_AND_ATTRIBUTES * head.count).from_buffer(capabilities, offset)
        cap_sids = sorted(sid_string(entry.Sid) for entry in entries)
        groups = win32security.GetTokenInformation(token, win32security.TokenGroups)
        logon = [win32security.ConvertSidToStringSid(sid) for sid, flags in groups
                 if flags & 0xc0000000 == 0xc0000000]
        user = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        return {'package_sid': package_sid, 'capability_sids': cap_sids, 'logon_sids': logon,
                'user_sid': user, 'session': win32security.GetTokenInformation(token, win32security.TokenSessionId),
                'integrity_sid': win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)[0])}
    finally:
        token.Close()


def main():
    if len(sys.argv) == 2 and sys.argv[1] == 'child':
        print(json.dumps(snapshot()))
        return
    parent = snapshot()
    child = subprocess.run([sys.executable, '-I', __file__, 'child'], capture_output=True, text=True,
                           timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    if child.returncode:
        print(json.dumps({'status': 'CHILD_FAILED', 'exit_code': child.returncode}))
        raise SystemExit(1)
    other = json.loads(child.stdout)
    # User/logon identifiers are compared, not emitted.
    print(json.dumps({'server_package_sid': parent['package_sid'], 'client_package_sid': other['package_sid'],
                      'server_capability_sids': parent['capability_sids'], 'client_capability_sids': other['capability_sids'],
                      'same_user': parent['user_sid'] == other['user_sid'],
                      'same_logon_sids': parent['logon_sids'] == other['logon_sids'],
                      'same_session': parent['session'] == other['session'],
                      'server_integrity': parent['integrity_sid'], 'client_integrity': other['integrity_sid']}))


if __name__ == '__main__':
    main()
