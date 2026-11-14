"""Synthetic TCP tables and fake handle APIs only; no native calls."""
import ipaddress
import struct
import pytest
from backend.runtime.windows_listener import Listener, ListenerResolver, parse_tcp_owner_table


def table4(entries=()):
    rows=[]
    for addr,port,pid in entries:
        rows.append(struct.pack('<I',2)+ipaddress.ip_address(addr).packed+port.to_bytes(2,'big')+b'\0\0'+b'\0'*8+struct.pack('<I',pid))
    return struct.pack('<I',len(rows))+b''.join(rows)


def table6(entries=()):
    rows=[]
    for addr,port,pid in entries:
        rows.append(ipaddress.ip_address(addr).packed+struct.pack('<I',0)+port.to_bytes(2,'big')+b'\0\0'+b'\0'*24+struct.pack('<II',2,pid))
    return struct.pack('<I',len(rows))+b''.join(rows)


class Fake:
    def __init__(self):
        self.v4=table4([('127.0.0.1',8000,42)]); self.v6=table6(); self.root=object(); self.opened=object()
        self.ids={self.root:(42,99), self.opened:(42,99)}; self.alive=True; self.member=True; self.closed=[]; self.reads=0
    def tcp_table(self, family): self.reads+=1; return self.v4 if family==4 else self.v6
    def open_process(self, pid): return self.opened
    def process_identity(self, handle): return self.ids[handle]
    def running(self, handle): return self.alive
    def in_job(self, handle, job): return self.member
    def close(self, handle): self.closed.append(handle)


def test_parses_ipv4_and_ipv6_owner_rows():
    assert parse_tcp_owner_table(table4([('127.0.0.1',8000,42)]),4)==(Listener('127.0.0.1',8000,42),)
    assert parse_tcp_owner_table(table6([('::1',5173,7)]),6)==(Listener('::1',5173,7),)
    with pytest.raises(ValueError): parse_tcp_owner_table(b'\x01\x00\x00\x00',4)


def test_owned_requires_stable_handle_identity_and_membership():
    f=Fake(); assert ListenerResolver(f).resolve(object(),f.root,'127.0.0.1',8000)=='owned'
    assert f.reads==4 and f.closed==[f.opened]
    f.ids[f.opened]=(42,100)
    assert ListenerResolver(f).resolve(object(),f.root,'127.0.0.1',8000)=='other'
    f.ids[f.opened]=(42,99); f.member=False
    assert ListenerResolver(f).resolve(object(),f.root,'127.0.0.1',8000)=='other'


def test_absence_wildcards_and_duplicates_fail_closed():
    f=Fake(); f.v4=table4(); assert ListenerResolver(f).resolve(object(),f.root,'127.0.0.1',8000)=='absent'
    f.v6=table6([('::',8000,3)]); assert ListenerResolver(f).resolve(object(),f.root,'127.0.0.1',8000)=='unknown'
    f.v6=table6(); f.v4=table4([('127.0.0.1',8000,42),('127.0.0.1',8000,42)])
    assert ListenerResolver(f).resolve(object(),f.root,'127.0.0.1',8000)=='unknown'


def test_exit_or_listener_replacement_is_unknown():
    f=Fake(); f.alive=False; assert ListenerResolver(f).resolve(object(),f.root,'127.0.0.1',8000)=='unknown'
    f=Fake(); original=f.tcp_table
    def changed(family):
        if f.reads>=2 and family==4: return table4([('127.0.0.1',8000,43)])
        return original(family)
    f.tcp_table=changed
    assert ListenerResolver(f).resolve(object(),f.root,'127.0.0.1',8000)=='unknown'


def test_ipv6_and_open_or_close_failure():
    f=Fake(); f.v4=table4(); f.v6=table6([('::1',5173,42)])
    assert ListenerResolver(f).resolve(object(),f.root,'::1',5173)=='owned'
    f.open_process=lambda pid: (_ for _ in ()).throw(OSError())
    assert ListenerResolver(f).resolve(object(),f.root,'::1',5173)=='unknown'
    f=Fake(); f.close=lambda handle: (_ for _ in ()).throw(OSError())
    assert ListenerResolver(f).resolve(object(),f.root,'127.0.0.1',8000)=='unknown'
