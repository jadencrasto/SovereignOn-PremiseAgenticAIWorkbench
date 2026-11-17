"""Exercise the production Win32 adapters with synthetic kernel and audit data."""
from collections import deque
import ctypes
from dataclasses import replace
from pathlib import Path
import struct
import sys

import pytest

from backend.runtime import windows_job_driver
from test_r825_containment_handoff import Kernel
from tools.process_test_supervisor import native_bootstrap as bootstrap
from tools.process_test_supervisor import windows_pipe as pipe_module
from tools.process_test_supervisor.handoff import (
    Binding, HandoffError, ObserverHandoff, PeerIdentity, PeerPrincipal, decode, encode,
)
from tools.process_test_supervisor.windows_parentage import ParentageEvidence, Record, WindowsProcessAudit, audit_record
from tools.process_test_supervisor.windows_pipe import NativePipe, WindowsPipeAPI, pipe_descriptor


PRINCIPAL = PeerPrincipal('S-1-5-21-1-2-3-1001', 'S-1-5-5-1-2', 1)
NAME = '\\\\.\\pipe\\SovereignAI-R55-' + 'e' * 64
OPERATION = '9e89c286-0bd0-4e5b-8c65-7a35ae75be5e'
PARENT, CHILD, CONTROLLER = (10, 100), (11, 200), (12, 50)
EVENT_HEAD = Record(123, 1, 'baseline', 99)
ROOT_EVENT = Record(124, 4688, 'audit', 102, CONTROLLER[0], PARENT[0])
CHILD_EVENT = Record(125, 4688, 'audit', 201, PARENT[0], CHILD[0])


class ByteKernel:
    def __init__(self):
        self.chunks = deque()
        self.log = []

    def server(self, name, sid):
        assert (name, sid) == (NAME, PRINCIPAL.logon_sid)
        return 60

    def io(self, handle, operation, value, deadline, cancelled):
        assert handle == 60 and deadline == 5
        assert not cancelled()
        self.log.append(operation)
        if operation == 'read':
            data = self.chunks.popleft()
            assert len(data) <= value
            return data
        return len(value) if operation == 'write' else 0

    def close(self, handle):
        self.log.append(('close', handle))


def test_single_connection_has_bounded_exact_frames_and_close():
    api = ByteKernel()
    pipe = NativePipe.listen(api, NAME, PRINCIPAL.logon_sid, clock=lambda: 0)
    assert not pipe.connected()
    pipe.accept(5)
    assert pipe.connected()
    with pytest.raises(HandoffError, match='single-use'):
        pipe.accept(5)
    frame = encode({'test': 1})
    api.chunks.extend([frame[:2], frame[2:4], frame[4:8], frame[8:]])
    assert decode(pipe.receive(5)) == {'test': 1}
    pipe.send(frame, 5)
    pipe.close()
    pipe.close()
    assert not pipe.connected() and api.log.count(('close', 60)) == 1


@pytest.mark.parametrize('raw', [b'', struct.pack('<I', 0), struct.pack('<I', 4097)])
def test_disconnect_or_unbounded_frame_closes_channel(raw):
    api = ByteKernel()
    pipe = NativePipe.listen(api, NAME, PRINCIPAL.logon_sid, clock=lambda: 0)
    pipe.accept(5)
    api.chunks.append(raw)
    with pytest.raises(HandoffError):
        pipe.receive(5)
    assert not pipe.connected()
    pipe.close()


def test_cancel_and_dead_peer_do_not_enter_kernel_io_again():
    api = ByteKernel()
    pipe = NativePipe.listen(api, NAME, PRINCIPAL.logon_sid, clock=lambda: 0)
    pipe.pin_liveness(lambda: False)
    with pytest.raises(HandoffError, match='cancelled'):
        pipe.accept(5)
    assert not api.log
    pipe.cancel()
    pipe.close()
    assert api.log == [('close', 60)]


@pytest.mark.parametrize('name,sid', [
    ('\\\\remote\\pipe\\SovereignAI-R55-' + 'e' * 64, PRINCIPAL.logon_sid),
    (NAME, 'S-1-1-0'), (NAME, 'S-1-5-7'), (NAME, 'S-1-5-5-1-2)(A;;GA;;;WD'),
    (NAME, 'S-1-5-5-4294967296-2'),
])
def test_reject_remote_pipe_or_broad_sddl_authority(name, sid):
    with pytest.raises(HandoffError):
        NativePipe.listen(ByteKernel(), name, sid)


class WinSymbols:
    def __getattr__(self, name):
        def unexpected(*args):
            raise AssertionError('Unexpected native call: ' + name)
        setattr(self, name, unexpected)
        return unexpected


@pytest.fixture
def symbols(monkeypatch):
    kernel, advapi = WinSymbols(), WinSymbols()
    last_error = [0]
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **kwargs: {'kernel32.dll': kernel, 'advapi32.dll': advapi}[name])
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: last_error[0])
    return kernel, advapi, last_error


def test_native_creation_uses_protected_dacl_one_instance_and_narrow_client_rights(symbols, monkeypatch):
    kernel, advapi, _ = symbols
    api = WindowsPipeAPI(None)

    def convert(descriptor, revision, target, _):
        assert descriptor == 'D:P(A;;GA;;;SY)(A;;0x12019f;;;S-1-5-5-1-2)'
        assert descriptor == pipe_descriptor(PRINCIPAL.logon_sid) and revision == 1
        target._obj.value = 71
        return True

    def server(name, access, mode, count, output, read, delay, attrs):
        assert (name, access, mode, count, output, read, delay) == (NAME, 0x40080003, 8, 1, 8192, 8192, 0)
        assert attrs._obj.descriptor == 71 and not attrs._obj.inherit
        return 60

    def client(name, access, share, attrs, disposition, flags, template):
        assert (name, access, share, attrs, disposition, flags, template) == (
            NAME, 0x100083, 0, None, 3, 0x40110000, None,
        )
        return 61

    monkeypatch.setattr(advapi, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', convert)
    monkeypatch.setattr(kernel, 'CreateNamedPipeW', server)
    monkeypatch.setattr(kernel, 'CreateFileW', client)
    monkeypatch.setattr(kernel, 'LocalFree', lambda value: None if value.value == 71 else 99)
    assert api.server(NAME, PRINCIPAL.logon_sid) == 60
    assert api.client(NAME) == 61


@pytest.mark.parametrize('drains', [True, False])
def test_native_overlapped_timeout_cancels_and_retains_only_undrained_storage(symbols, monkeypatch, drains):
    kernel, _, last_error = symbols
    now = [0.0]
    log = []
    api = WindowsPipeAPI(None, clock=lambda: now[0])

    def connect(handle, state):
        assert (handle, state._obj.event) == (60, 62)
        last_error[0] = 997
        return False

    def result(handle, state, count, wait, alertable):
        log.append(wait)
        if wait == 100:
            last_error[0] = 995 if drains else 258
        else:
            now[0] += wait / 1000
            last_error[0] = 258
        return False

    monkeypatch.setattr(kernel, 'CreateEventW', lambda *args: 62)
    monkeypatch.setattr(kernel, 'ConnectNamedPipe', connect)
    monkeypatch.setattr(kernel, 'GetOverlappedResultEx', result)
    monkeypatch.setattr(kernel, 'CancelIoEx', lambda *args: log.append('cancel'))
    monkeypatch.setattr(kernel, 'CloseHandle', lambda handle: log.append(('close', handle)) or True)
    monkeypatch.setattr(pipe_module, '_undrained', [])
    with pytest.raises(HandoffError, match='expired'):
        api.io(60, 'connect', None, 0.05, lambda: False)
    assert log[:4] == [25, 25, 'cancel', 100]
    if drains:
        assert log[4:] == [('close', 62)]
    else:
        assert len(pipe_module._undrained) == 1
        with pytest.raises(HandoffError, match='unverified'):
            api.close(60)
        assert not log[4:]


def test_native_reverts_impersonation_before_parsing_token(symbols, monkeypatch):
    kernel, advapi, _ = symbols
    api = WindowsPipeAPI(None)
    log = []

    def open_thread(thread, rights, self_access, token):
        assert (thread, rights, self_access) == (99, 8, True)
        token._obj.value = 55
        return True

    monkeypatch.setattr(kernel, 'GetCurrentThread', lambda: 99)
    monkeypatch.setattr(advapi, 'ImpersonateNamedPipeClient', lambda handle: log.append(('impersonate', handle)) or True)
    monkeypatch.setattr(advapi, 'OpenThreadToken', open_thread)
    monkeypatch.setattr(advapi, 'RevertToSelf', lambda: log.append('revert') or True)
    monkeypatch.setattr(api, '_principal', lambda token: log.append(('principal', token)) or PRINCIPAL)
    monkeypatch.setattr(api, 'close', lambda handle: log.append(('close', handle)))
    transport = NativePipe(api, 60, server=False)
    assert api.pipe_client_principal(transport) == PRINCIPAL
    assert log == [('impersonate', 60), 'revert', ('principal', 55), ('close', 55)]


@pytest.mark.parametrize('fault', [None, 'out-of-buffer', 'wrong-logon-count', 'wrong-group-type'])
def test_native_checks_sid_pointer_bounds_and_actual_token_fields(symbols, monkeypatch, fault):
    _, advapi, _ = symbols
    api = WindowsPipeAPI(None)

    def query(token, kind, output, size, used):
        assert token == 55
        buffer = output._obj
        if kind == 12:
            buffer.value = PRINCIPAL.session
            used._obj.value = ctypes.sizeof(buffer)
            return True
        assert kind in (1, 28)
        name = PRINCIPAL.user_sid if kind == 1 else PRINCIPAL.logon_sid
        revision, authority, *parts = map(int, name.split('-')[1:])
        raw = bytes([revision, len(parts)]) + authority.to_bytes(6, 'big')
        raw += b''.join(part.to_bytes(4, 'little') for part in parts)
        if kind == 1:
            header = pipe_module._SidAndAttributes.from_buffer(buffer)
            start = ctypes.sizeof(header)
            pointer = header
        else:
            header = pipe_module._LogonGroups.from_buffer(buffer)
            header.count = 2 if fault == 'wrong-logon-count' else 1
            header.group.attributes = 0 if fault == 'wrong-group-type' else 0xc0000000
            start = ctypes.sizeof(header)
            pointer = header.group
        assert start + len(raw) <= size
        address = ctypes.addressof(buffer) + start
        ctypes.memmove(address, raw, len(raw))
        pointer.sid = address + size if fault == 'out-of-buffer' else address
        used._obj.value = start + len(raw)
        return True

    monkeypatch.setattr(advapi, 'GetTokenInformation', query)
    if fault is None:
        assert api._principal(55) == PRINCIPAL
    else:
        with pytest.raises((HandoffError, OSError)):
            api._principal(55)


def test_process_audit_extracts_os_creator_and_ignores_command_line():
    xml = '''<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
      <System><Provider Name="Microsoft-Windows-Security-Auditing"/><EventID>4688</EventID>
      <TimeCreated SystemTime="1601-01-01T00:00:00.0000201Z"/>
      <EventRecordID>125</EventRecordID><Channel>Security</Channel></System>
      <EventData><Data Name="ProcessId">0xa</Data><Data Name="NewProcessId">0xb</Data>
      <Data Name="CommandLine">unrelated</Data></EventData></Event>'''
    assert audit_record(xml) == replace(CHILD_EVENT, provider='Microsoft-Windows-Security-Auditing')
    with pytest.raises(HandoffError):
        audit_record(xml.replace('Security</Channel>', 'Application</Channel>'))
    with pytest.raises(HandoffError):
        audit_record(xml.replace('Name="CommandLine"', 'Name="ProcessId"'))


class Audit:
    def __init__(self):
        self.records = [EVENT_HEAD, ROOT_EVENT, CHILD_EVENT]

    def latest(self, deadline):
        return EVENT_HEAD

    def since(self, number, deadline):
        assert number == EVENT_HEAD.number
        return tuple(self.records)


class Processes:
    def __init__(self):
        self.pids = {10, 11}
        self.counts = (2, 2)
        self.identities = {50: PARENT, 51: CHILD}
        self.member = True
        self.alive = True

    def job_process_ids(self, job):
        assert job == 99
        return self.pids

    def job_counts(self, job):
        assert job == 99
        return self.counts

    def process_identity(self, handle):
        return self.identities[handle]

    def running(self, handle):
        return self.alive

    def in_job(self, handle, job):
        return self.member


def evidence(audit, kernel):
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    return ParentageEvidence(audit, kernel, CONTROLLER, 0.1, clock=lambda: clock[0], sleep=sleep)


def test_parentage_and_final_audit_stay_bound_to_retained_job_generations():
    audit, api = Audit(), Processes()
    evidence_source = evidence(audit, api)
    assert evidence_source.verify(99, 50, 51, PARENT, CHILD, 0.1)
    evidence_source.confirm(99, 50, 51, PARENT, CHILD, 0.1)
    assert evidence_source.finish(0.1)
    audit.records.append(Record(126, 4688, 'audit', 203, CHILD[0], 13))
    assert not evidence_source.finish(0.1)
    with pytest.raises(HandoffError, match='changed'):
        evidence_source.confirm(99, 50, 51, PARENT, CHILD, 0.1)


@pytest.mark.parametrize('fault', [None, 'gap', 'disruption', 'overflow', 'anchor-replaced'])
def test_busy_audit_window_preserves_capacity_and_continuity(fault):
    audit, kernel = Audit(), Processes()
    count = 1025 if fault == 'overflow' else 1024
    busy = [Record(EVENT_HEAD.number + i, 4688, 'audit', 101, 77, 2000 + i)
            for i in range(1, count)]
    busy[699] = replace(ROOT_EVENT, number=busy[699].number)
    busy[1022] = replace(CHILD_EVENT, number=busy[1022].number)
    if fault == 'gap':
        del busy[31]
    elif fault == 'disruption':
        busy[31] = replace(busy[31], code=1102, creator=None, child=None)
    anchor = replace(EVENT_HEAD, created=97) if fault == 'anchor-replaced' else EVENT_HEAD
    audit.records = [anchor, *busy]
    source = evidence(audit, kernel)
    if fault is None:
        assert source.verify(99, 50, 51, PARENT, CHILD, 0.1)
        assert source.finish(0.1)
    else:
        category = 'record budget exceeded' if fault == 'overflow' else 'continuity lost'
        with pytest.raises(HandoffError, match=category):
            source.verify(99, 50, 51, PARENT, CHILD, 0.1)


class EventPages:
    def __init__(self, count, error):
        self.available = deque(range(100, 100 + count))
        self.error = error
        self.closed = []
        self.calls = []

    def EvtQuery(self, session, path, xpath, flags):
        assert (session, path, xpath, flags) == (None, 'Security', '*[System[EventRecordID >= 123]]', 0x101)
        return 50

    def EvtNext(self, query, size, items, wait, flags, returned):
        assert query == 50 and 1 <= size <= 32 and 0 < wait <= 25 and flags == 0
        self.calls.append(size)
        count = min(size, len(self.available))
        for index in range(count):
            items[index] = self.available.popleft()
        returned._obj.value = count
        return bool(count)

    def EvtClose(self, handle):
        self.closed.append(handle)
        return True


def page_reader(monkeypatch, count, *, error=259, clock=lambda: 0):
    events = EventPages(count, error)
    reader = WindowsProcessAudit.__new__(WindowsProcessAudit)
    reader._clock = clock
    reader._evt = events
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: events.error)
    monkeypatch.setattr(reader, '_render', lambda handle: replace(EVENT_HEAD, number=handle + 23))
    return reader, events


@pytest.mark.parametrize('count,expected', [(31, 31), (1024, 1024), (1060, 1025)])
def test_native_audit_batch_stops_at_eof_or_overflow_sentinel(monkeypatch, count, expected):
    reader, events = page_reader(monkeypatch, count)
    rows = reader.since(123, 1)
    assert [row.number for row in rows] == list(range(123, 123 + expected))
    assert len(events.calls) == (expected // 32 + 1 if expected >= 1024 else 2)
    assert sorted(events.closed) == [50, *range(100, 100 + expected)]


@pytest.mark.parametrize('fault', ['deadline', 'render', 'partial'])
def test_audit_page_releases_every_handle_when_processing_fails(monkeypatch, fault):
    now = [0]
    reader, events = page_reader(monkeypatch, 65, clock=lambda: now[0])
    if fault == 'partial':
        read = events.EvtNext

        def partial(query, size, items, wait, flags, returned):
            result = read(query, size, items, wait, flags, returned)
            returned._obj.value -= 1
            return result

        monkeypatch.setattr(events, 'EvtNext', partial)
    else:
        def render(handle):
            if fault == 'render':
                raise ValueError('malformed event')
            now[0] = 1
            return EVENT_HEAD

        monkeypatch.setattr(reader, '_render', render)
    with pytest.raises(ValueError if fault == 'render' else HandoffError):
        reader.since(123, 1)
    assert len(events.calls) == 1
    assert sorted(events.closed) == [50, *range(100, 132)]


@pytest.mark.parametrize('error', [1460, 15011])
def test_audit_query_errors_are_not_clean_end_of_log(monkeypatch, error):
    reader, events = page_reader(monkeypatch, 0, error=error)
    with pytest.raises(OSError):
        reader.since(123, 1)
    assert events.closed == [50]


@pytest.mark.parametrize('defect', [
    'wrong-parent', 'wrong-controller', 'wrong-job', 'pid-reuse', 'missing', 'gap',
    'reset', 'early-audit', 'already-exited', 'truncated', 'extra-live', 'extra-exited', 'dead-child',
])
def test_parentage_rejects_mismatches_incompleteness_and_races(defect):
    audit, kernel = Audit(), Processes()
    match defect:
        case 'wrong-parent':
            audit.records[2] = replace(CHILD_EVENT, creator=13)
        case 'wrong-controller':
            audit.records[1] = replace(ROOT_EVENT, creator=13)
        case 'wrong-job':
            kernel.member = False
        case 'pid-reuse':
            kernel.identities[51] = (CHILD[0], 202)
        case 'missing':
            audit.records.pop()
        case 'gap':
            audit.records[2] = replace(CHILD_EVENT, number=126)
        case 'reset':
            audit.records[2] = replace(CHILD_EVENT, code=1102)
        case 'early-audit':
            audit.records[2] = replace(CHILD_EVENT, created=199)
        case 'already-exited':
            kernel.counts = (2, 1)
        case 'truncated':
            audit.records[0] = replace(EVENT_HEAD, number=122)
        case 'extra-live':
            kernel.pids.add(13)
        case 'extra-exited':
            kernel.counts = (3, 2)
        case 'dead-child':
            kernel.alive = False
    with pytest.raises(HandoffError):
        evidence(audit, kernel).verify(99, 50, 51, PARENT, CHILD, 0.05)


def test_native_worker_profile_is_bounded_and_worker_cli_cannot_start_controller(monkeypatch):
    bind = Binding.fresh(OPERATION, 'descendant', 'a' * 64)
    peer = PeerIdentity(CONTROLLER, PRINCIPAL, str(Path('python.exe').resolve()))
    monkeypatch.setattr(bootstrap.time, 'monotonic', lambda: 10)
    capsule = bootstrap._profile(NAME, bind, peer, 15)
    assert bootstrap._read_profile(capsule) == (NAME, bind, peer, 15)
    for deadline in (10, 16):
        with pytest.raises(HandoffError, match='deadline'):
            bootstrap._read_profile(bootstrap._profile(NAME, bind, peer, deadline))
    assert bootstrap._main(['--run-reviewed-fixture', 'normal']) == 2
    assert bootstrap._main(['--observer', '0', '0']) == 2
    assert not bootstrap.verified_fixture_handoff(None, object(), ['python.exe'])


@pytest.mark.parametrize('scenario,fault', [
    ('normal', None), ('descendant', None), ('descendant', 'wrong-client'), ('descendant', 'audit-gap'),
    ('reject_containment', None),
])
def test_reviewed_composition_routes_native_contracts_to_existing_driver(monkeypatch, scenario, fault):
    snapshots = []

    class Guest(Kernel):
        def __init__(self):
            super().__init__()
            snapshots.append(self)
            self.ids.update({10: PARENT, 11: CHILD, 19: CONTROLLER, 110: PARENT, 111: CHILD})
            self.alive = {13, 19}  # Independently held control and controller.
            self.jobs = iter((21, 20))
            self.seen = set()

        def create_kill_on_close_job(self):
            return next(self.jobs)

        def inheritable_controller_query(self):
            return 69

        def create_suspended_observer(self, job, command, handles):
            assert job == 21 and command[-3:] == ['--observer', '69', '70'] and handles == (69, 70)
            self.alive.add(12)
            return 12, 32

        def create_suspended_in_job(self, job, command):
            if scenario == 'descendant':
                name, binding, _, _ = bootstrap._read_profile(bytes.fromhex(command[-1]), observer=False)
                assert binding is None and name == self.root_name and command[-2] == '--cooperative'
            self.seen.add(10)
            return super().create_suspended_in_job(job, command)

        def in_job(self, handle, job):
            return handle == 12 if job == 21 else super().in_job(handle, job)

        def job_counts(self, job):
            return (1, int(12 in self.alive)) if job == 21 else (len(self.seen), self.active_processes(job))

        def job_process_ids(self, job):
            return self.alive & self.members

    class PipeAPI:
        def __init__(self, native):
            self.process = native
            self.pipes, self.readers, self.responses = {}, {}, {}
            self.running = native.running
            self.process_identity = native.process_identity

        def current_process(self):
            return 19

        def process_principal(self, handle):
            return PRINCIPAL

        def process_image(self, handle):
            return str(Path(sys.executable).resolve())

        def bootstrap_reader(self, profile):
            name, binding, _, deadline = bootstrap._read_profile(profile)
            self.process.binding = binding
            handle = self.pipes[name]
            audit = (bootstrap.ParentageEvidence(Audit(), self.process, CONTROLLER, deadline)
                     if scenario == 'descendant' else None)
            self.readers[handle] = ObserverHandoff(binding, self.process, exact_job=True, creation_audit=audit,
                                                    resume_deadline=deadline)._handle
            return 70

        def server(self, name, logon_sid):
            assert logon_sid == PRINCIPAL.logon_sid
            self.pipes[name] = 60 + len(self.pipes)
            if scenario == 'descendant' and len(self.pipes) == 1:
                self.process.root_name = name
            return self.pipes[name]

        def pipe_peer_pid(self, transport, remote):
            if transport.handle in self.readers:
                return 13 if fault == 'wrong-client' else 12
            return 10

        def pipe_client_principal(self, transport):
            return PRINCIPAL

        def close(self, handle):
            self.process.close(handle)

        def io(self, handle, operation, value, deadline, cancelled):
            assert not cancelled()
            if operation == 'connect':
                return 0
            if operation == 'read':
                result = self.responses[handle][:value]
                self.responses[handle] = self.responses[handle][value:]
                return result
            request = decode(value)
            if handle in self.readers:
                response = self.readers[handle](request, deadline=deadline)
            elif request['type'] == 'ROOT_HELLO':
                self.process.binding.fields(request, 'ROOT_HELLO', 0, ())
                response = request
            elif request['type'] == 'REQUEST_DESCENDANT':
                self.process.alive.add(11)
                self.process.seen.add(11)
                response = self.process.binding.message(
                    'DESCENDANT_OFFER', 1, parent=list(PARENT), child=list(CHILD),
                    process_slot=41, thread_slot=42, thread_id=51,
                )
            else:
                response = self.process.binding.message('THREAD_RELEASED', 2, child=list(CHILD))
            self.process.log.append(response['type'])
            self.responses[handle] = encode(response)
            return len(value)

    class AuditSource(Audit):
        def __init__(self):
            super().__init__()
            if fault == 'audit-gap':
                self.records[2] = replace(CHILD_EVENT, number=126)

    monkeypatch.setattr(windows_job_driver, 'WindowsNativeAPI', Guest)
    monkeypatch.setattr(bootstrap, 'WindowsNativeAPI', Guest)
    monkeypatch.setattr(bootstrap, 'WindowsPipeAPI', PipeAPI)
    monkeypatch.setattr(bootstrap, 'WindowsProcessAudit', AuditSource)
    monkeypatch.setattr(bootstrap.FixtureManifest, 'verify', lambda self: 'a' * 64)
    outcome = bootstrap.run_reviewed_fixture(bootstrap.FixtureManifest(PRINCIPAL, {}), scenario)
    api, = snapshots
    assert {13, 19} == api.alive
    if fault == 'wrong-client':
        assert 'root-suspended' not in api.log and outcome.cleanup == 'uncertain'
    elif fault == 'audit-gap':
        assert ('resume', 31) not in api.log and outcome.cleanup == 'uncertain'
    elif scenario == 'reject_containment':
        assert ('resume', 30) not in api.log and outcome.operation == 'failed' and outcome.cleanup == 'observed'
    else:
        assert api.log.index('ROOT_OBSERVED') < api.log.index(('resume', 30))
        if scenario == 'descendant':
            assert api.log.index('ROOT_HELLO') < api.log.index('DESCENDANT_OFFER')
            assert api.log.index('THREAD_RELEASED') < api.log.index(('resume', 31))
        assert outcome.operation == 'root_exited' and outcome.cleanup == 'observed'
    assert {21, 32, 69, 70} <= set(api.closed)
