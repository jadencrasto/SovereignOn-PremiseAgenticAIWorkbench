"""Mocked driver contract tests only: no Windows native calls or processes."""
import pytest
from backend.runtime.windows_job_driver import WindowsJobDriver
from backend.runtime.lifecycle import RuntimeCoordinator, State


class Native:
    def __init__(self): self.log=[]; self.status={}; self.assigned=set(); self.active=0; self.fail_assign=False; self.cleanup_failed=False
    def create_kill_on_close_job(self): self.log.append('job'); return object()
    def create_suspended(self, command):
        self.log.append('suspended'); process=object(); thread=object(); self.status[process]=True
        return process, thread
    def assign(self, job, process):
        self.log.append('assign')
        if self.fail_assign: raise OSError('mock')
        self.assigned.add(process); self.active+=1
    def in_job(self, process, job): self.log.append('in_job'); return process in self.assigned
    def resume(self, thread): self.log.append('resume')
    def close(self, handle): self.log.append('close')
    def running(self, process): return self.status.get(process,False)
    def terminate_job(self, job):
        self.log.append('terminate_job')
        if self.cleanup_failed: raise OSError('mock')
        for process in self.assigned: self.status[process]=False
        self.active=0
    def terminate_process(self, process): self.log.append('terminate_unassigned'); self.status[process]=False
    def active_processes(self, job): return self.active


def driver(fake):
    return WindowsJobDriver({'backend':[r'C:\synthetic\backend.exe'], 'frontend':[r'C:\synthetic\frontend.exe']}, fake)


def test_suspended_then_assigned_verified_then_resumed():
    api=Native(); d=driver(api); job=d.open_containment(); root=d.start_contained(job,'backend')
    assert api.log[:5]==['job','suspended','assign','in_job','resume']
    assert d.is_running(root) is True
    assert d.listener_state(job,root,'backend')=='unknown'
    assert d.stop_containment(job) is True and job.closed


def test_assignment_failure_cleans_unassigned_handle_with_coordinator():
    api=Native(); api.fail_assign=True; d=driver(api); c=RuntimeCoordinator(d)
    assert c.start({'ready':True},0)==State.FAILED
    assert 'resume' not in api.log and 'terminate_unassigned' in api.log
    assert all(not active for active in api.status.values())


def test_unknown_listener_never_calls_health_and_coordinator_cleans_owned_job():
    api=Native(); d=driver(api); c=RuntimeCoordinator(d); assert c.start({'ready':True},0)==State.STARTING
    assert c.poll(1)==State.FAILED and c.failure=='listener_ownership_unverified'
    assert api.log.count('terminate_job')==1


def test_cleanup_uncertainty_is_not_success_and_no_pid_is_used():
    api=Native(); d=driver(api); c=RuntimeCoordinator(d); c.start({'ready':True},0); api.cleanup_failed=True
    assert c.stop()==State.CLEANUP_UNVERIFIED
    assert c.stop()==State.CLEANUP_UNVERIFIED and api.log.count('terminate_job')==1
    assert not d._job.closed


def test_unknown_handle_and_relative_executable_are_rejected():
    api=Native(); d=driver(api); d.open_containment()
    with pytest.raises(RuntimeError): d.stop_containment(object())
    assert 'terminate_job' not in api.log
    with pytest.raises(ValueError): WindowsJobDriver({'backend':['relative.exe'],'frontend':[r'C:\synthetic\frontend.exe']},api)


def test_membership_failure_never_resumes_and_job_is_cleaned():
    api=Native(); api.in_job=lambda process,job: False
    d=driver(api); c=RuntimeCoordinator(d)
    assert c.start({'ready':True},0)==State.FAILED
    assert 'resume' not in api.log and api.log.count('terminate_job')==1


def test_root_exit_and_active_descendant_keep_cleanup_uncertain():
    api=Native(); d=driver(api); c=RuntimeCoordinator(d); c.start({'ready':True},0)
    api.status[d._job.roots[0].handle]=False
    def terminate_with_surviving_descendant(job): api.log.append('terminate_job'); api.active=1
    api.terminate_job=terminate_with_surviving_descendant
    assert c.poll(1)==State.CLEANUP_UNVERIFIED
    assert c.failure=='child_exited_or_unknown' and not d._job.closed


def test_injected_listener_resolver_receives_retained_handles():
    api=Native(); seen=[]
    class Resolver:
        def resolve(self, job, root, address, port):
            seen.append((job,root,address,port)); return 'owned'
    d=WindowsJobDriver({'backend':[r'C:\synthetic\backend.exe'],'frontend':[r'C:\synthetic\frontend.exe']}, api,
        endpoints={'backend':('127.0.0.1',8000),'frontend':('::1',5173)},listener_resolver=Resolver())
    job=d.open_containment(); root=d.start_contained(job,'backend')
    assert d.listener_state(job,root,'backend')=='owned'
    assert seen==[(job.handle,root.handle,'127.0.0.1',8000)]
    assert d.listener_state(job,root,'frontend')=='unknown'


@pytest.mark.parametrize('phase', ['before_resume', 'after_resume'])
def test_exited_root_during_setup_is_never_reported_started(phase):
    api = Native()
    if phase == 'before_resume':
        api.running = lambda handle: False
    else:
        original_resume = api.resume
        def exit_on_resume(thread):
            original_resume(thread)
            for handle in api.status: api.status[handle] = False
        api.resume = exit_on_resume
    d = driver(api); c = RuntimeCoordinator(d)
    assert c.start({'ready': True}, 0) == State.FAILED
    assert c.failure == 'containment_or_start_failed'
    assert api.log.count('terminate_job') == 1
    if phase == 'before_resume': assert 'resume' not in api.log


def test_partial_handle_close_blocks_retry_and_new_roots():
    api = Native(); d = driver(api); job = d.open_containment()
    root = d.start_contained(job, 'backend')
    original_close = api.close
    def failed_close(handle):
        if handle is job.handle: raise OSError('synthetic close failure')
        original_close(handle)
    api.close = failed_close
    c = RuntimeCoordinator(d); c._job = job; c.state = State.STARTING
    assert c.stop() == State.CLEANUP_UNVERIFIED
    calls = list(api.log)
    assert c.stop() == State.CLEANUP_UNVERIFIED
    with pytest.raises(RuntimeError): d.stop_containment(job)
    with pytest.raises(RuntimeError): d.start_contained(job, 'frontend')
    assert not d.is_running(root) and api.log == calls
