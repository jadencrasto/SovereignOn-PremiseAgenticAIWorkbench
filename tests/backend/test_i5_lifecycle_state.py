"""State-machine-only tests: no Windows API, sockets, or process creation."""
import pytest
from backend.runtime.lifecycle import RuntimeCoordinator, State

class FakeDriver:
    def __init__(self):
        self.job = object(); self.roots = {}; self.stops = []; self.listeners = {}; self.health = {}
        self.fail_start = None; self.fail_cleanup = False; self.living = set(); self.probes=[]
    def open_containment(self): return self.job
    def start_contained(self, job, role):
        assert job is self.job
        if role == self.fail_start: raise RuntimeError('synthetic start failure')
        root = object(); self.roots[role] = root; self.living.add(root)
        self.listeners[role] = 'absent'; self.health[role] = False
        return root
    def is_running(self, root): return root in self.living
    def listener_state(self, job, root, role):
        assert job is self.job and self.roots[role] is root
        return self.listeners[role]
    def health_ready(self, role): self.probes.append(role); return self.health[role]
    def stop_containment(self, job):
        assert job is self.job
        self.stops.append(job)
        if self.fail_cleanup: return False
        self.living.clear(); return True
    def make_ready(self):
        for role in self.roots: self.listeners[role]='owned'; self.health[role]=True


def test_default_and_preflight_refuse_launch():
    assert RuntimeCoordinator().start({'ready':True},0)==State.FAILED
    fake=FakeDriver(); c=RuntimeCoordinator(fake)
    assert c.start({'ready':False},0)==State.FAILED and fake.roots=={}


def test_ready_requires_owned_listeners_and_healthy_roots():
    fake=FakeDriver(); c=RuntimeCoordinator(fake)
    assert c.start({'ready':True},0)==State.STARTING
    assert c.poll(1)==State.STARTING and not fake.probes
    fake.make_ready(); assert c.poll(2)==State.READY
    fake.health['backend']=False
    assert c.poll(3)==State.FAILED and c.failure=='health_lost_or_unknown'
    assert len(fake.stops)==1


def test_unowned_listener_cannot_be_accepted_or_targeted():
    fake=FakeDriver(); c=RuntimeCoordinator(fake); c.start({'ready':True},0)
    fake.listeners['backend']='other'
    assert c.poll(1)==State.FAILED and c.failure=='listener_ownership_unverified'
    assert fake.probes==[] and fake.stops==[fake.job]


def test_listener_change_after_health_response_fails_closed():
    fake=FakeDriver(); c=RuntimeCoordinator(fake); c.start({'ready':True},0)
    fake.make_ready()
    def probe(role):
        fake.listeners[role] = 'other'
        return True
    fake.health_ready=probe
    assert c.poll(1)==State.FAILED and c.failure=='listener_ownership_changed'
    assert fake.stops==[fake.job]


def test_partial_start_failure_and_repeated_stop():
    fake=FakeDriver(); fake.fail_start='frontend'; c=RuntimeCoordinator(fake)
    assert c.start({'ready':True},0)==State.FAILED
    assert len(fake.roots)==1 and not fake.living and fake.stops==[fake.job]
    assert c.stop()==State.FAILED and len(fake.stops)==1


def test_interrupt_during_second_start_attempts_containment_cleanup():
    fake=FakeDriver(); original=fake.start_contained
    def interrupt(job,role):
        if role == 'frontend': raise KeyboardInterrupt()
        return original(job,role)
    fake.start_contained=interrupt; c=RuntimeCoordinator(fake)
    with pytest.raises(KeyboardInterrupt): c.start({'ready':True},0)
    assert c.failure=='startup_interrupted' and c.state==State.FAILED
    assert fake.stops==[fake.job]


def test_interrupt_during_cleanup_does_not_claim_success():
    fake=FakeDriver(); c=RuntimeCoordinator(fake); c.start({'ready':True},0)
    def interrupt(job): raise KeyboardInterrupt()
    fake.stop_containment=interrupt
    with pytest.raises(KeyboardInterrupt): c.stop()
    assert c.state==State.CLEANUP_UNVERIFIED


def test_timeout_crash_and_cleanup_unknown_fail_closed():
    fake=FakeDriver(); c=RuntimeCoordinator(fake,timeout_seconds=2); c.start({'ready':True},0)
    assert c.poll(2)==State.FAILED and c.failure=='startup_timeout'
    assert fake.stops==[fake.job]
    fake=FakeDriver(); c=RuntimeCoordinator(fake); c.start({'ready':True},0)
    fake.living.clear(); fake.fail_cleanup=True
    assert c.poll(1)==State.CLEANUP_UNVERIFIED and c.failure=='child_exited_or_unknown'
    assert c.stop()==State.CLEANUP_UNVERIFIED and len(fake.stops)==1


def test_cancel_and_clock_ambiguity():
    fake=FakeDriver(); c=RuntimeCoordinator(fake); c.start({'ready':True},5)
    assert c.stop()==State.STOPPED and c.stop()==State.STOPPED and len(fake.stops)==1
    fake=FakeDriver(); c=RuntimeCoordinator(fake); c.start({'ready':True},5)
    assert c.poll(4)==State.FAILED and c.failure=='clock_unverified'
    with pytest.raises(ValueError): RuntimeCoordinator(timeout_seconds=float('nan'))


def test_exited_second_root_is_detected_while_first_listener_is_absent():
    fake=FakeDriver(); c=RuntimeCoordinator(fake); c.start({'ready':True},0)
    fake.living.remove(fake.roots['frontend'])
    assert c.poll(1)==State.FAILED
    assert c.failure=='child_exited_or_unknown' and fake.stops==[fake.job]


def test_second_port_conflict_is_detected_while_first_listener_is_absent():
    fake = FakeDriver()
    coordinator = RuntimeCoordinator(fake)
    coordinator.start({'ready': True}, 0)
    fake.listeners['frontend'] = 'other'
    assert coordinator.poll(1) == State.FAILED
    assert coordinator.failure == 'listener_ownership_unverified'
    assert fake.probes == [] and fake.stops == [fake.job]


def test_second_health_probe_cannot_invalidate_first_listener_and_claim_ready():
    fake = FakeDriver()
    coordinator = RuntimeCoordinator(fake)
    coordinator.start({'ready': True}, 0)
    fake.make_ready()

    def probe(role):
        if role == 'frontend':
            fake.listeners['backend'] = 'other'
        return True

    fake.health_ready = probe
    assert coordinator.poll(1) == State.FAILED
    assert coordinator.failure == 'listener_ownership_changed'


def test_listener_is_rechecked_before_its_health_probe():
    fake = FakeDriver()
    coordinator = RuntimeCoordinator(fake)
    coordinator.start({'ready': True}, 0)
    fake.make_ready()

    def probe(role):
        fake.probes.append(role)
        fake.listeners['frontend'] = 'other'
        return True

    fake.health_ready = probe
    assert coordinator.poll(1) == State.FAILED
    assert coordinator.failure == 'listener_ownership_changed'
    assert fake.probes == ['backend']


@pytest.mark.parametrize('stage', ['startup', 'readiness'])
def test_primary_interruption_survives_cleanup_interruption(stage):
    class PrimaryInterruption(BaseException): pass
    class CleanupInterruption(BaseException): pass
    fake=FakeDriver(); c=RuntimeCoordinator(fake)
    def interrupted_cleanup(job): raise CleanupInterruption()
    fake.stop_containment=interrupted_cleanup
    if stage == 'startup':
        def interrupted_start(job, role): raise PrimaryInterruption()
        fake.start_contained=interrupted_start
        operation=lambda: c.start({'ready':True},0)
    else:
        c.start({'ready':True},0)
        def interrupted_check(root): raise PrimaryInterruption()
        fake.is_running=interrupted_check
        operation=lambda: c.poll(1)
    with pytest.raises(PrimaryInterruption): operation()
    assert c.state==State.CLEANUP_UNVERIFIED
    assert c.failure==f'{stage}_interrupted'
    assert c.stop()==State.CLEANUP_UNVERIFIED
