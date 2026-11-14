"""Manual development-only Windows pipe. Never a trusted production broker."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

if sys.platform != 'win32':
    raise SystemExit('Windows only')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pywintypes
import win32api
import win32con
import win32event
import win32file
import win32pipe
import win32security

from prototype.windows_broker_ipc.peer_identity import inspect_pipe_client
from prototype.windows_broker_ipc.protocol import MAX_REQUEST_BYTES, Command, ProtocolError, parse_request
from prototype.windows_broker_ipc.rights_design import CLIENT_CANDIDATE

PIPE = r'\\.\pipe\SovereignAI-Development-IPC-v1'
TIMEOUT_MS = 1500
MAX_RESPONSE = 2048
PENDING = 997
CONNECTED = 535
MORE_DATA = 234


def _account_sid():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
        return sid, win32security.ConvertSidToStringSid(sid)
    finally:
        token.Close()


def _security_attributes(sid):
    acl = win32security.ACL()
    # Candidate data/attribute/synchronization rights omit the bit shared by
    # FILE_APPEND_DATA and FILE_CREATE_PIPE_INSTANCE. Still requires OS review.
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, CLIENT_CANDIDATE, sid)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(1, acl, 0)
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    attributes.bInheritHandle = False
    return attributes


def _overlapped(handle, start):
    """Issue one overlapped operation, cancel on timeout on the same thread."""
    event = win32event.CreateEvent(None, True, False, None)
    ov = pywintypes.OVERLAPPED()
    ov.hEvent = event
    try:
        code, value = start(ov)
        if code == PENDING:
            if win32event.WaitForSingleObject(event, TIMEOUT_MS) != win32event.WAIT_OBJECT_0:
                try:
                    win32file.CancelIo(handle)
                except Exception:
                    # I/O may still reference the OVERLAPPED and its event.
                    os._exit(4)
                if win32event.WaitForSingleObject(event, TIMEOUT_MS) != win32event.WAIT_OBJECT_0:
                    # Do not free OVERLAPPED/buffer while kernel I/O is pending.
                    os._exit(4)
                try:
                    win32file.GetOverlappedResult(handle, ov, False)
                except pywintypes.error:
                    pass
                raise TimeoutError('operation timed out')
            size = win32file.GetOverlappedResult(handle, ov, False)
        elif code in (0, CONNECTED):
            size = None
        else:
            raise OSError('IPC operation failed')
        return value, size
    finally:
        event.Close()


def _connect(handle):
    def start(ov):
        try:
            return win32pipe.ConnectNamedPipe(handle, ov), None
        except pywintypes.error as exc:
            if exc.winerror == CONNECTED:
                return CONNECTED, None
            raise
    _overlapped(handle, start)


def _read(handle, limit):
    def start(ov):
        return win32file.ReadFile(handle, limit + 1, ov)
    data, size = _overlapped(handle, start)
    data = bytes(data if size is None else data[:size])
    if len(data) > limit:
        raise ValueError('message too large')
    return data


def _write(handle, payload):
    if len(payload) > MAX_RESPONSE:
        raise ValueError('response too large')
    _overlapped(handle, lambda ov: win32file.WriteFile(handle, payload, ov))


def _response(raw):
    try:
        command = parse_request(raw)
        data = {'version': 1, 'ok': True, 'development_only': True, 'trusted_broker': False,
                'command': command.value}
        if command == Command.CAPABILITIES:
            data['commands'] = ['ping', 'status', 'capabilities']
    except ProtocolError as exc:
        data = {'version': 1, 'ok': False, 'development_only': True, 'trusted_broker': False,
                'error': exc.code.value}
    return json.dumps(data, separators=(',', ':')).encode('utf-8')


def _serve_unverified_draft():
    sid, sid_text = _account_sid()
    handle = win32pipe.CreateNamedPipe(
        PIPE, win32pipe.PIPE_ACCESS_DUPLEX | win32con.FILE_FLAG_OVERLAPPED | 0x00080000,
        win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | 0x00000008,
        1, MAX_RESPONSE, MAX_REQUEST_BYTES + 1, TIMEOUT_MS, _security_attributes(sid))
    print('development broker ready', flush=True)
    try:
        while True:
            connected = False
            try:
                _connect(handle)
                connected = True
                try:
                    raw = _read(handle, MAX_REQUEST_BYTES)
                except ValueError:
                    _write(handle, _response(b'x' * (MAX_REQUEST_BYTES + 1)))
                    continue
                except pywintypes.error as exc:
                    if exc.winerror == MORE_DATA:
                        _write(handle, _response(b'x' * (MAX_REQUEST_BYTES + 1)))
                        continue
                    raise
                identity = inspect_pipe_client(handle, sid_text)
                if not identity.accepted:
                    if identity.code == 'CLEANUP_FAILED':
                        return 3
                    continue
                _write(handle, _response(raw))
            except (TimeoutError, pywintypes.error, OSError):
                pass
            finally:
                if connected:
                    try:
                        win32pipe.DisconnectNamedPipe(handle)
                    except pywintypes.error:
                        pass
    except KeyboardInterrupt:
        return 0
    finally:
        handle.Close()


def _request_unverified_draft(raw):
    # The pipe owner is not authenticated: never send secrets or trust results.
    handle = win32file.CreateFile(PIPE, CLIENT_CANDIDATE,
                                  0, None, win32con.OPEN_EXISTING, win32con.FILE_FLAG_OVERLAPPED, None)
    try:
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        _write(handle, raw)
        return _read(handle, MAX_RESPONSE)
    finally:
        handle.Close()


def serve():
    raise RuntimeError('Development IPC disabled: bounded I/O and pipe access remain unverified')


def request(raw):
    raise RuntimeError('Development IPC disabled: bounded I/O and pipe access remain unverified')


def main():
    parser = argparse.ArgumentParser(description='Untrusted development-only IPC')
    parser.add_argument('action', choices=('serve', 'ping', 'status', 'capabilities'))
    args = parser.parse_args()
    if args.action == 'serve':
        return serve()
    wire = json.dumps({'version': 1, 'command': args.action}).encode()
    print(request(wire).decode('utf-8'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
