"""One ephemeral pipe and child open; no broker, payload, network, or storage."""
import json
import ctypes
from ctypes import wintypes
import subprocess
import sys
import uuid

import pywintypes
import win32api
import win32con
import win32file
import win32pipe
import win32security


def token_snapshot():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        def get(name):
            try:
                return win32security.GetTokenInformation(token, getattr(win32security, name))
            except Exception:
                return None
        user = win32security.ConvertSidToStringSid(get('TokenUser')[0])
        integrity = get('TokenIntegrityLevel')
        if isinstance(integrity, tuple):
            integrity = win32security.ConvertSidToStringSid(integrity[0]).split('-')[-1]
        groups = get('TokenGroups') or []
        group_sids = {win32security.ConvertSidToStringSid(sid): flags for sid, flags in groups}
        info = wintypes.DWORD()
        size = wintypes.DWORD()
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        query = advapi.GetTokenInformation
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD)]
        query.restype = wintypes.BOOL
        appcontainer = int(info.value) if query(int(token), 29, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(size)) else None
        return {'user': user, 'session': get('TokenSessionId'), 'elevation_type': get('TokenElevationType'),
                'elevation': get('TokenElevation'), 'integrity_rid': integrity,
                'restricted': bool(win32security.IsTokenRestricted(token)),
                'appcontainer': appcontainer,
                'admin_group_present': 'S-1-5-32-544' in group_sids,
                'admin_group_flags': group_sids.get('S-1-5-32-544'),
                'users_group_present': 'S-1-5-32-545' in group_sids,
                'authenticated_users_present': 'S-1-5-11' in group_sids}
    finally:
        token.Close()


def main():
    if len(sys.argv) == 2 and sys.argv[1] == 'token-only':
        result = token_snapshot()
        result.pop('user', None)
        print(json.dumps(result))
        return
    if len(sys.argv) == 3 and sys.argv[1] == 'child':
        result = {'token': token_snapshot()}
        try:
            handle = win32file.CreateFile(sys.argv[2], win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                          0, None, win32con.OPEN_EXISTING, win32con.FILE_FLAG_OVERLAPPED, None)
        except pywintypes.error as exc:
            result['open_error'] = exc.winerror
        else:
            result['open_error'] = 0
            handle.Close()
        print(json.dumps(result))
        return
    parent = token_snapshot()
    user_sid = win32security.ConvertStringSidToSid(parent['user'])
    acl = win32security.ACL()
    granted = win32con.GENERIC_READ | win32con.GENERIC_WRITE
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, granted, user_sid)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(1, acl, 0)
    attrs = pywintypes.SECURITY_ATTRIBUTES()
    attrs.SECURITY_DESCRIPTOR = descriptor
    attrs.bInheritHandle = False
    name = r'\\.\pipe\SovereignAI-Diagnostic-' + uuid.uuid4().hex
    handle = win32pipe.CreateNamedPipe(name, win32pipe.PIPE_ACCESS_DUPLEX | win32con.FILE_FLAG_OVERLAPPED | 0x80000,
                                       win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | 8,
                                       1, 2048, 1025, 1500, attrs)
    try:
        sd = win32security.GetSecurityInfo(handle, win32security.SE_KERNEL_OBJECT, win32security.DACL_SECURITY_INFORMATION)
        dacl = sd.GetSecurityDescriptorDacl()
        ace = dacl.GetAce(0)
        try:
            label_sd = win32security.GetSecurityInfo(handle, win32security.SE_KERNEL_OBJECT, 0x10)
            label_acl = label_sd.GetSecurityDescriptorSacl()
            label = [win32security.ConvertSidToStringSid(label_acl.GetAce(i)[2])
                     for i in range(label_acl.GetAceCount())] if label_acl else []
        except pywintypes.error as exc:
            label = {'error': exc.winerror}
        child = subprocess.run([sys.executable, '-I', __file__, 'child', name], capture_output=True,
                               text=True, timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if child.returncode:
            print(json.dumps({'child_exit': child.returncode, 'dacl_ace_count': dacl.GetAceCount()}))
            return
        child_result = json.loads(child.stdout)
        child_token = child_result.pop('token')
        print(json.dumps({'server': {k:v for k,v in parent.items() if k != 'user'},
                          'client': {k:v for k,v in child_token.items() if k != 'user'},
                          'same_user_sid': parent['user'] == child_token['user'],
                          'same_session': parent['session'] == child_token['session'],
                          'dacl_ace_count': dacl.GetAceCount(),
                          'ace_type': ace[0][0], 'ace_mask_hex': hex(ace[1]),
                          'ace_matches_user_sid': win32security.ConvertSidToStringSid(ace[2]) == parent['user'],
                          'pipe_mandatory_labels': label,
                          'requested_generic_mask_hex': hex(granted & 0xffffffff),
                          'client_open_error': child_result['open_error']}))
    finally:
        handle.Close()


if __name__ == '__main__':
    main()
