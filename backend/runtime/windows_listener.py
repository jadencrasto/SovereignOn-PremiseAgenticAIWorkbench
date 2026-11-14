"""Experimental loopback listener correlation. Native behavior is unverified."""
from dataclasses import dataclass
from ipaddress import ip_address
import struct


@dataclass(frozen=True)
class Listener:
    address: str
    port: int
    pid: int


def parse_tcp_owner_table(data: bytes, family: int):
    """Parse bounded Windows MIB_TCP[6]TABLE_OWNER_PID bytes; network-order port."""
    if family not in (4, 6) or len(data) < 4:
        raise ValueError('Invalid table')
    count = struct.unpack_from('<I', data)[0]
    size = 24 if family == 4 else 56
    if count > 100_000 or len(data) != 4 + count * size:
        raise ValueError('Invalid table size')
    records=[]
    for index in range(count):
        row = memoryview(data)[4 + index * size:4 + (index + 1) * size]
        if family == 4:
            state = struct.unpack_from('<I', row, 0)[0]
            address = str(ip_address(bytes(row[4:8])))
            port = int.from_bytes(row[8:10], 'big')
            pid = struct.unpack_from('<I', row, 20)[0]
        else:
            address = str(ip_address(bytes(row[:16])))
            scope = struct.unpack_from('<I', row, 16)[0]
            port = int.from_bytes(row[20:22], 'big')
            state = struct.unpack_from('<I', row, 48)[0]
            pid = struct.unpack_from('<I', row, 52)[0]
            if scope:
                # Scoped endpoints require a separately verified interface policy.
                raise ValueError('Scoped endpoint unsupported')
        if state == 2:
            records.append(Listener(address, port, pid))
    return tuple(records)


class ListenerResolver:
    """Only exact loopback root ownership is eligible; descendants remain unknown."""
    def __init__(self, api):
        self.api=api

    def resolve(self, job, root, address, port):
        if address not in ('127.0.0.1', '::1') or not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            return 'unknown'
        held = None
        try:
            def snapshot():
                rows = parse_tcp_owner_table(self.api.tcp_table(4), 4) + parse_tcp_owner_table(self.api.tcp_table(6), 6)
                # Wildcard/dual-stack semantics cannot be inferred safely from this table.
                if any(row.port == port and row.address in ('0.0.0.0', '::') for row in rows):
                    return None
                return tuple(row for row in rows if row.port == port and row.address == address)
            first=snapshot()
            if first is None: return 'unknown'
            if not first: return 'absent'
            if len(first) != 1 or first[0].pid <= 0: return 'unknown'
            held=self.api.open_process(first[0].pid)
            if not held: return 'unknown'
            root_id = self.api.process_identity(root)
            observed_id = self.api.process_identity(held)
            if root_id != observed_id or observed_id[0] != first[0].pid:
                return 'other'
            if not self.api.running(root) or not self.api.running(held): return 'unknown'
            if not self.api.in_job(held, job): return 'other'
            second=snapshot()
            if second != first: return 'unknown'
            if self.api.process_identity(held) != observed_id or self.api.process_identity(root) != root_id:
                return 'unknown'
            if not self.api.running(root) or not self.api.running(held) or not self.api.in_job(held, job): return 'unknown'
            return 'owned'
        except Exception:
            return 'unknown'
        finally:
            if held is not None:
                try: self.api.close(held)
                except Exception: return 'unknown'


class WindowsListenerAPI:
    """GetExtendedTcpTable and stable handle observations; no process termination."""
    def __init__(self, job_native):
        import os
        if os.name != 'nt': raise RuntimeError('Windows only')
        import ctypes as C
        from ctypes import wintypes as W
        self.C, self.W, self.native=C,W,job_native
        self.ip=C.WinDLL('iphlpapi', use_last_error=True)
        self.k=C.WinDLL('kernel32', use_last_error=True)
        self.ip.GetExtendedTcpTable.argtypes=[W.LPVOID, C.POINTER(W.DWORD), W.BOOL, W.ULONG, W.ULONG, W.ULONG]
        self.ip.GetExtendedTcpTable.restype=W.DWORD
        self.k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]; self.k.OpenProcess.restype=W.HANDLE
        self.k.GetProcessId.argtypes=[W.HANDLE]; self.k.GetProcessId.restype=W.DWORD
        self.k.GetProcessTimes.argtypes=[W.HANDLE,C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME)]
        self.k.GetProcessTimes.restype=W.BOOL

    def tcp_table(self, family):
        af = {4:2, 6:23}[family]
        size=self.W.DWORD(0)
        result=self.ip.GetExtendedTcpTable(None, self.C.byref(size), False, af, 3, 0)
        if result != 122 or size.value < 4 or size.value > 4 * 1024 * 1024:
            raise OSError('TCP table size unavailable')
        for _ in range(3):
            buf=self.C.create_string_buffer(size.value)
            result=self.ip.GetExtendedTcpTable(buf, self.C.byref(size), False, af, 3, 0)
            if result == 0:
                return bytes(buf.raw[:size.value])
            if result != 122 or size.value > 4 * 1024 * 1024: break
        raise OSError('TCP table unavailable')

    def open_process(self, pid):
        handle=self.k.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle: raise OSError('Process identity unavailable')
        return handle

    def process_identity(self, handle):
        pid=self.k.GetProcessId(handle)
        created, exited, kernel, user=(self.W.FILETIME() for _ in range(4))
        if not pid or not self.k.GetProcessTimes(handle, self.C.byref(created), self.C.byref(exited), self.C.byref(kernel), self.C.byref(user)):
            raise OSError('Process identity unavailable')
        return pid, (created.dwHighDateTime << 32) | created.dwLowDateTime

    def running(self, handle): return self.native.running(handle)
    def in_job(self, handle, job): return self.native.in_job(handle, job)
    def close(self, handle): self.native.close(handle)
