"""Actual outer controller and driver with fake native handles; no processes."""
import pytest

from test_i52_windows_job_mock import Native, driver
from tools.process_test_supervisor.outer_controller import OuterController


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now


def test_deadline_uses_retained_handle_and_cleans_owned_job():
    api = Native(); clock = Clock(); d = driver(api); seen = []
    def wait(handle, ms):
        seen.append((handle, ms)); clock.now += ms / 1000
        return False
    api.wait_process = wait
    c = OuterController(d, clock=clock)
    outcome = c.run(2)
    assert outcome.operation == 'deadline_exceeded' and outcome.cleanup == 'observed'
    assert seen == [(d._job.roots[0].handle, 2000)]
    assert api.log.index('assign') < api.log.index('resume') < api.log.index('terminate_job')
    with pytest.raises(RuntimeError): c.run(2)


def test_root_exit_does_not_imply_cleanup_and_unrelated_is_untouched():
    api = Native(); d = driver(api); unrelated = object(); api.status[unrelated] = True
    api.wait_process = lambda handle, ms: True
    result = OuterController(d, clock=Clock()).run(1)
    assert result.operation == 'root_exited' and result.cleanup == 'observed'
    assert api.status[unrelated] is True


@pytest.mark.parametrize('failure', ['create', 'assign', 'resume', 'wait', 'terminate', 'descendant'])
def test_failure_remains_explicit(failure):
    api = Native(); d = driver(api); clock = Clock()
    api.wait_process = lambda handle, ms: True
    def fail(*args): raise OSError('synthetic')
    if failure == 'create': api.create_kill_on_close_job = fail
    if failure == 'assign': api.fail_assign = True
    if failure == 'resume': api.resume = fail
    if failure == 'wait': api.wait_process = fail
    if failure == 'terminate': api.cleanup_failed = True
    if failure == 'descendant':
        def survive(job):
            for process in api.assigned: api.status[process] = False
            api.active = 1
        api.terminate_job = survive
    result = OuterController(d, clock=clock).run(1)
    if failure in ('create', 'assign', 'resume', 'wait'): assert result.operation == 'failed'
    if failure in ('create', 'terminate', 'descendant'): assert result.cleanup == 'uncertain'
    if failure == 'assign': assert 'resume' not in api.log and 'terminate_unassigned' in api.log


def test_invalid_wait_and_foreign_root_fail_closed():
    api = Native(); d = driver(api); job = d.open_containment(); root = d.start_contained(job, 'backend')
    with pytest.raises(RuntimeError): d.wait_root(job, object(), 1)
    with pytest.raises(RuntimeError): d.wait_root(job, root, -1)
    with pytest.raises(RuntimeError): d.wait_root(object(), root, 1)


def test_deadline_before_creation_prevents_resume():
    api = Native(); d = driver(api)
    times = iter([0, 2]); result = OuterController(d, clock=lambda: next(times)).run(1)
    assert result.operation == 'deadline_exceeded' and result.cleanup == 'observed'
    assert 'suspended' not in api.log and 'resume' not in api.log


def test_deadline_during_assignment_prevents_resume():
    api = Native(); d = driver(api); clock = Clock()
    original_assign = api.assign
    def delayed_assign(job, process):
        original_assign(job, process)
        clock.now = 2
    api.assign = delayed_assign
    result = OuterController(d, clock=clock).run(1)
    assert result.operation == 'deadline_exceeded' and result.cleanup == 'observed'
    assert 'assign' in api.log and 'resume' not in api.log
    assert api.log.count('terminate_job') == 1


def test_failed_resume_guard_still_cleans_containment():
    api = Native(); d = driver(api); job = d.open_containment()
    with pytest.raises(RuntimeError):
        d.start_contained(job, 'backend', before_resume=lambda: False)
    assert 'resume' not in api.log
    assert d.stop_containment(job) is True


def test_interruption_still_attempts_owned_cleanup():
    api = Native(); d = driver(api)
    def interrupted(handle, ms): raise KeyboardInterrupt()
    api.wait_process = interrupted
    with pytest.raises(KeyboardInterrupt): OuterController(d, clock=Clock()).run(1)
    assert api.log.count('terminate_job') == 1
