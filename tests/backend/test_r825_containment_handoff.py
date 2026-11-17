"""Offline peers, byte frames and kernel objects. No IPC or Windows calls."""
import struct
from threading import Event, Thread

import pytest

from backend.runtime.windows_job_driver import WindowsJobDriver, WindowsNativeAPI
from test_r824_process_containment_offline import win32  # noqa: F401
from tools.process_test_supervisor.handoff import (
    AuthenticatedChannel, Binding, ControllerHandoff, CooperativeDescendant, HandoffError,
    ObserverHandoff, PeerIdentity, PeerPrincipal, decode, encode,
)
from tools.process_test_supervisor.outer_controller import OuterController


OP = '9e89c286-0bd0-4e5b-8c65-7a35ae75be5e'
PRINCIPAL = PeerPrincipal('S-1-5-21-1-2-3-1001', 'S-1-5-5-1-2', 1)
OTHER = PeerPrincipal('S-1-5-21-1-2-3-1002', 'S-1-5-5-1-2', 1)
ROOT, CHILD, OBSERVER = (10, 100), (11, 101), (12, 102)


class Kernel:
    def __init__(self):
        self.log = []
        self.ids = {10: ROOT, 11: CHILD, 12: OBSERVER, 110: ROOT, 111: CHILD, 13: (13, 103)}
        self.alive = {10, 12, 13}
        self.members = {10, 11}
        self.peer_pid = 12
        self.peer_token = PRINCIPAL
        self.closed = []
        self.now = 0

    def clock(self):
        return self.now

    def create_kill_on_close_job(self):
        return 20

    def create_suspended_in_job(self, job, command):
        assert job == 20
        self.alive.add(10)
        self.log.append('root-suspended')
        return 10, 30

    def process_identity(self, handle):
        return self.ids[handle]

    def process_image(self, handle):
        return 'fixture.exe'

    def process_principal(self, handle):
        return PRINCIPAL

    def pipe_peer_pid(self, transport, remote):
        return transport.pid if transport.pid is not None else self.peer_pid

    def pipe_client_principal(self, transport):
        return self.peer_token

    def in_job(self, handle, job):
        return job in (20, 120) and self.ids[handle][0] in self.members

    def running(self, handle):
        return self.ids[handle][0] in self.alive

    def duplicate_observation_job(self, job, observer):
        assert (job, observer) == (20, 12)
        self.log.append('job-query-duplicate')
        return 120

    def duplicate_observation_process(self, handle, observer):
        assert observer == 12
        self.log.append(('process-query-duplicate', handle))
        return handle + 100

    def duplicate_descendant(self, root, process_slot, thread_slot):
        assert (root, process_slot, thread_slot) == (10, 41, 42)
        return 11, 31

    def thread_identity(self, thread):
        assert thread == 31
        return 11, 51

    def resume(self, thread):
        self.log.append(('resume', thread))

    def terminate_job(self, job):
        assert job == 20
        self.log.append('terminate')
        self.alive.difference_update(self.members)

    def close(self, handle):
        self.closed.append(handle)
        self.log.append(('close', handle))

    def active_processes(self, job):
        return len(self.alive & self.members)

    def wait_process(self, handle, timeout_ms):
        self.alive.remove(handle)
        return True


class Wire:
    def __init__(self, respond, pid=None):
        self.respond = respond
        self.pid = pid
        self.open = True

    def connected(self):
        return self.open

    def send(self, frame, deadline):
        self.request = frame

    def receive(self, deadline):
        return self.respond(self.request)


def bind(scenario='normal', op=OP):
    return Binding(op, scenario, 'a' * 64, 'b' * 64, 'c' * 64)


def peer(wire, api, identity, handle):
    return AuthenticatedChannel(wire, api, handle, PeerIdentity(identity, PRINCIPAL, 'fixture.exe'),
                                remote='client', clock=api.clock)


def supervisor(api):
    commands = {service: [r'C:\synthetic\fixture.exe'] for service in ('backend', 'frontend')}
    return WindowsJobDriver(commands, api, clock=api.clock)


def setup(scenario='normal', *, api=None, rule=None, parentage=None, release=True, op=OP, deadline=5):
    api = api or Kernel()
    binding = bind(scenario, op)
    observer = ObserverHandoff(binding, api)

    def receive(frame):
        request = decode(frame)
        response = observer._handle(request)
        api.log.append(response['type'])
        return encode(rule(response) if rule and response['type'] != 'HELLO' else response)

    def root(frame):
        request = decode(frame)
        if request['type'] == 'REQUEST_DESCENDANT':
            api.alive.add(11)
            return encode(binding.message('DESCENDANT_OFFER', 1, parent=list(ROOT), child=list(CHILD),
                                          process_slot=41, thread_slot=42, thread_id=51))
        assert request['type'] == 'TRANSFER_ACCEPTED'
        if not release:
            raise TimeoutError('synthetic')
        api.log.append('raw-child-thread-released')
        return encode(binding.message('THREAD_RELEASED', 2, child=list(CHILD)))

    handoff = ControllerHandoff(
        binding, peer(Wire(receive), api, OBSERVER, 12), api, deadline,
        root_channel=lambda handle, identity: peer(Wire(root, 10), api, identity, handle),
        parentage=parentage if parentage is not None else lambda parent, child, end: (parent, child) == (ROOT, CHILD),
        clock=api.clock,
    )
    return api, supervisor(api), handoff


@pytest.mark.parametrize('scenario', ['normal', 'descendant'])
def test_controller_requires_authenticated_evidence_and_independent_cleanup(scenario):
    api = Kernel()

    def factory(op, scenario, deadline):
        return setup(scenario, api=api, op=op, deadline=deadline)[2]

    outcome = OuterController(supervisor(api), clock=api.clock, scenario=scenario, handoff_factory=factory).run(5)
    assert (outcome.operation, outcome.cleanup) == ('root_exited', 'observed')
    assert api.log.index('HELLO') < api.log.index('job-query-duplicate')
    assert api.log.index('ROOT_OBSERVED') < api.log.index(('resume', 30))
    if scenario == 'descendant':
        assert api.log.index('DESCENDANT_OBSERVED') < api.log.index('raw-child-thread-released')
        assert api.log.index('raw-child-thread-released') < api.log.index(('resume', 31))
        assert 111 in api.closed
    assert {12, 13} == api.alive
    assert api.log.count('terminate') == 1
    assert {20, 120, 10, 110} <= set(api.closed)


@pytest.mark.parametrize('fault', ['pid', 'generation', 'token', 'image', 'missing', 'timeout', 'forgery',
                                    'stale-nonce', 'wrong-job', 'wrong-child', 'extra-field', 'duplicate-seq'])
def test_root_rejects_invalid_acknowledgment_before_resume(fault):
    api = Kernel()

    def rule(response):
        match fault:
            case 'timeout':
                api.now = 5
            case 'forgery':
                response['member'] = 1
            case 'stale-nonce':
                response['nonce'] = 'f' * 64
            case 'wrong-job':
                response['job_token'] = 'f' * 64
            case 'wrong-child':
                response['process'] = [ROOT[0], ROOT[1] + 1]
            case 'extra-field':
                response['authenticated'] = True
            case 'duplicate-seq':
                response['seq'] = 0
        return response

    if fault == 'pid':
        api.peer_pid = 13
    elif fault == 'generation':
        api.ids[12] = (12, 99)
    elif fault == 'token':
        api.peer_token = OTHER
    elif fault == 'image':
        api.process_image = lambda handle: 'other.exe'
    elif fault == 'missing':
        api.alive.remove(12)
    api, driver, handoff = setup(api=api, rule=rule)
    job = driver.open_containment()
    with pytest.raises(HandoffError):
        driver.start_contained(job, 'backend', handoff=handoff)
    assert ('resume', 30) not in api.log
    if fault in ('pid', 'generation', 'token', 'image', 'missing'):
        assert 'job-query-duplicate' not in api.log
    assert driver.stop_containment(job)
    assert 13 in api.alive


def test_permit_is_one_shot_and_root_cannot_be_replayed():
    api, _, handoff = setup()
    permit, identity = handoff.prepare(20, 10)
    handoff.consume(permit, 20, identity)
    with pytest.raises(HandoffError):
        handoff.consume(permit, 20, identity)
    with pytest.raises(HandoffError):
        handoff.prepare(20, 10)


@pytest.mark.parametrize('fault', ['parentage', 'release', 'membership', 'thread', 'recycled-child', 'early-exit'])
def test_descendant_fault_only_resumes_root_and_preserves_unrelated_process(fault):
    api, driver, handoff = setup('descendant', release=fault != 'release',
                                parentage=lambda *args: fault != 'parentage')
    job = driver.open_containment()
    root = driver.start_contained(job, 'backend', handoff=handoff)
    if fault == 'membership':
        api.members.remove(11)
    elif fault == 'thread':
        api.thread_identity = lambda handle: (13, 51)
    elif fault == 'recycled-child':
        api.ids[11] = (11, 201)
    elif fault == 'early-exit':
        running = api.running
        api.running = lambda handle: False if handle == 11 else running(handle)
    with pytest.raises((HandoffError, RuntimeError, TimeoutError)):
        driver.adopt_descendant(job, root, handoff)
    assert ('resume', 31) not in api.log
    assert driver.stop_containment(job)
    assert 13 in api.alive


def test_nested_and_repeated_registration_are_refused():
    api, driver, handoff = setup('descendant')
    job = driver.open_containment()
    root = driver.start_contained(job, 'backend', handoff=handoff)
    child = driver.adopt_descendant(job, root, handoff)
    for parent in (root, child):
        with pytest.raises(RuntimeError, match='No eligible cooperative root'):
            driver.adopt_descendant(job, parent, handoff)
    with pytest.raises(RuntimeError):
        driver.start_contained(job, 'frontend')
    assert api.log.count(('resume', 31)) == 1
    assert driver.stop_containment(job)


@pytest.mark.parametrize('point', ['root-ack', 'descendant-creation', 'resume-in-flight'])
def test_concurrent_abort_orders_resume_and_pins_handles(point):
    api = Kernel()
    waiting, continued, cleanup_committed = Event(), Event(), Event()
    errors = []

    def block():
        waiting.set()
        assert continued.wait(1)

    def rule(response):
        if point == 'root-ack':
            block()
        return response

    api, driver, handoff = setup('descendant', api=api, rule=rule)
    job = driver.open_containment()
    if point == 'resume-in-flight':
        original_resume = api.resume
        api.resume = lambda thread: (original_resume(thread), block())[0]
    elif point == 'descendant-creation':
        original_duplicate = api.duplicate_descendant
        api.duplicate_descendant = lambda *args: (block(), original_duplicate(*args))[1]

    root = driver.start_contained(job, 'backend', handoff=handoff) if point == 'descendant-creation' else None

    def start():
        try:
            if root is None:
                driver.start_contained(job, 'backend', handoff=handoff)
            else:
                driver.adopt_descendant(job, root, handoff)
        except Exception as error:
            errors.append(error)

    wait_for = driver._handoff_complete.wait_for

    def wait_for_handoff(predicate, timeout):
        assert 20 not in api.closed
        assert 'terminate' not in api.log
        cleanup_committed.set()
        return wait_for(predicate, timeout)

    driver._handoff_complete.wait_for = wait_for_handoff
    worker = Thread(target=start)
    results = []
    cleanup = Thread(target=lambda: results.append(driver.stop_containment(job)))
    worker.start()
    try:
        assert waiting.wait(1)
        cleanup.start()
        assert cleanup_committed.wait(1)
    finally:
        continued.set()
        worker.join(1)
        if cleanup.ident is not None:
            cleanup.join(1)
    assert results == [True] and not cleanup.is_alive()
    assert not worker.is_alive() and errors
    assert api.log.count(('resume', 30)) == (0 if point == 'root-ack' else 1)
    assert ('resume', 31) not in api.log
    assert 13 in api.alive


def test_handoff_stall_preserves_handles_until_external_recovery():
    api, driver, _ = setup()
    job = driver.open_containment()
    job.handoff_active = True
    seen = []

    def blocked(predicate, timeout):
        seen.append(timeout)
        return False

    driver._handoff_complete.wait_for = blocked
    assert not driver.stop_containment(job)
    assert seen == [2]
    assert not job.closed and not api.closed and 'terminate' not in api.log
    with pytest.raises(RuntimeError, match='Unknown containment'):
        driver.stop_containment(job)


def test_partial_startup_cleans_job_but_requires_independent_descendant_exit():
    api = Kernel()

    def factory(op, scenario, deadline):
        return setup(scenario, api=api, release=False, op=op, deadline=deadline)[2]

    outcome = OuterController(supervisor(api), clock=api.clock, scenario='descendant', handoff_factory=factory).run(5)
    assert (outcome.operation, outcome.cleanup) == ('failed', 'uncertain')
    assert ('resume', 31) not in api.log
    assert api.alive == {12, 13}


def test_observer_checks_membership_independently_of_controller():
    api, driver, handoff = setup()
    member = api.in_job
    api.in_job = lambda process, job: job != 120 and member(process, job)
    job = driver.open_containment()
    with pytest.raises(HandoffError, match='Independent observation failed'):
        driver.start_contained(job, 'backend', handoff=handoff)
    assert ('resume', 30) not in api.log
    assert driver.stop_containment(job)


def test_authenticated_peer_is_rechecked_after_a_valid_frame():
    api = Kernel()

    def disconnect(response):
        api.peer_pid = 13
        return response

    api, driver, handoff = setup(api=api, rule=disconnect)
    job = driver.open_containment()
    with pytest.raises(HandoffError, match='peer identity mismatch'):
        driver.start_contained(job, 'backend', handoff=handoff)
    assert ('resume', 30) not in api.log
    assert driver.stop_containment(job)


@pytest.mark.parametrize('connected', [True, False])
def test_observer_authenticates_controller_before_consuming_handle_slots(connected):
    api = Kernel()
    binding = bind()
    observer = ObserverHandoff(binding, api)
    controller = api.ids[13]
    request = binding.message('HANDLES', 2, process=list(ROOT), parent=None, process_slot=110, job_slot=120)
    frames = iter([binding.message('HELLO', 1), request])
    wire = Wire(lambda _: encode(next(frames)), controller[0])
    wire.request = None
    wire.open = connected
    channel = AuthenticatedChannel(wire, api, 13, PeerIdentity(controller, PRINCIPAL, 'fixture.exe'),
                                   remote='server', clock=api.clock)
    if connected:
        assert observer.serve_once(channel, 5)
        assert decode(wire.request) == binding.message('HELLO', 1)
        assert observer.serve_once(channel, 5)
        assert decode(wire.request) == binding.message('ROOT_OBSERVED', 2, process=list(ROOT), parent=None, member=True)
        observer.close()
        assert api.closed == [110, 120]
    else:
        with pytest.raises(HandoffError, match='channel unavailable'):
            observer.serve_once(channel, 5)
        assert api.closed == []


def test_native_bridge_requests_only_explicit_rights_and_closes_partial_child_duplicate(win32, monkeypatch):
    kernel, _ = win32
    api = WindowsNativeAPI()
    calls = []
    failed = False
    closed = []

    def duplicate(source, slot, target, value, access, inherit, options):
        calls.append((source, slot, target, access))
        assert inherit is False and options == 0
        if failed and slot == 42:
            return False
        value._obj.value = slot + 100
        return True

    monkeypatch.setattr(kernel, 'GetCurrentProcess', lambda: 999)
    monkeypatch.setattr(kernel, 'DuplicateHandle', duplicate)
    monkeypatch.setattr(kernel, 'CloseHandle', lambda handle: closed.append(handle) or True)
    assert api.duplicate_observation_job(20, 12) == 120
    assert api.duplicate_observation_process(10, 12) == 110
    assert api.duplicate_descendant(10, 41, 42) == (141, 142)
    assert calls == [(999, 20, 12, 0x4), (999, 10, 12, 0x101000),
                     (10, 41, 999, 0x101000), (10, 42, 999, 0x802)]
    failed = True
    with pytest.raises(OSError):
        api.duplicate_descendant(10, 41, 42)
    assert closed == [141]


def test_fixture_closes_original_thread_before_release_ack():
    binding = bind('descendant')
    transcript = []

    class FixtureNative:
        def process_identity(self, handle):
            assert handle == 41
            return CHILD

        def thread_identity(self, handle):
            assert handle == 42
            return 11, 51

        def close(self, handle):
            transcript.append(handle)

    class Channel:
        requests = iter([binding.message('REQUEST_DESCENDANT', 1, parent=list(ROOT)),
                         binding.message('TRANSFER_ACCEPTED', 2, child=list(CHILD))])

        def receive(self, deadline):
            return next(self.requests)

        def send(self, message, deadline):
            transcript.append(message['type'])

    fixture = CooperativeDescendant(binding, ROOT, FixtureNative(), lambda: (41, 42))
    fixture.run(Channel(), 5)
    assert transcript == ['DESCENDANT_OFFER', 42, 'THREAD_RELEASED', 41]
    with pytest.raises(HandoffError):
        fixture.run(Channel(), 5)


@pytest.mark.parametrize('frame', [b'', struct.pack('<I', 2) + b'{}x', struct.pack('<I', 1) + b'\xff',
                                   struct.pack('<I', 13) + b'{"x":1,"x":2}', b' ' * 4101])
def test_invalid_frame_is_rejected(frame):
    with pytest.raises(HandoffError):
        decode(frame)
