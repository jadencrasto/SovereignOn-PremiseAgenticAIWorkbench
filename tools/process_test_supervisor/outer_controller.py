"""Experimental handle-owning fixture controller; no launcher or CLI.

Run only inside a separately approved and independently bounded environment.
The caller must itself be an external process, outside the workload job. This
module does not create or verify that outer environment.
"""
from dataclasses import dataclass
from math import ceil, isfinite
from pathlib import Path
import sys
import time
import uuid

from backend.runtime.windows_job_driver import WindowsJobDriver
from tools.process_test_supervisor.handoff import ControllerHandoff


@dataclass(frozen=True)
class Outcome:
    operation_id: str
    operation: str
    cleanup: str


class _DeadlineExceeded(Exception):
    pass


SCENARIOS = frozenset({'normal', 'stall', 'descendant', 'early_exit', 'reject_containment'})


def fixture_driver(scenario='descendant', operation_id=None):
    """Pin the workload to the existing non-networked test fixture."""
    if scenario not in SCENARIOS:
        raise ValueError('Unsupported fixture scenario')
    fixture = Path(__file__).resolve().parents[2] / 'tests' / 'fixtures' / 'i52_processes' / 'non_network_workload.py'
    operation_id = operation_id or str(uuid.uuid4())
    if str(uuid.UUID(operation_id)) != operation_id:
        raise ValueError('Invalid operation identity')
    command = [str(Path(sys.executable).resolve()), '-I', '-B', str(fixture),
               '--scenario', scenario, '--operation-id', operation_id]
    return WindowsJobDriver({'backend': command, 'frontend': command})


class OuterController:
    """Single-use owner of a job; synchronous calls can themselves stall.

    This is not evidence of a separate process, independent watchdog or OS
    isolation. A separate outer boundary must bound the controller itself.
    """
    def __init__(self, driver=None, *, clock=time.monotonic, scenario='descendant', handoff_factory=None):
        if scenario not in SCENARIOS:
            raise ValueError('Unsupported fixture scenario')
        self.operation_id = str(uuid.uuid4())
        self._driver = driver if driver is not None else fixture_driver(scenario, self.operation_id)
        self._scenario = scenario
        self._clock = clock
        self._handoff_factory = handoff_factory
        self._used = False

    def run(self, timeout_seconds):
        if self._used:
            raise RuntimeError('Controller is single-use')
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or
                not isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30):
            raise ValueError('Invalid deadline')
        self._used = True
        deadline = self._clock() + timeout_seconds
        job = None
        handoff = None
        operation = 'failed'
        cleanup = 'uncertain'
        try:
            if self._handoff_factory is not None:
                handoff = self._handoff_factory(self.operation_id, self._scenario, deadline)
                if (type(handoff) is not ControllerHandoff or handoff.binding.op != self.operation_id
                        or handoff.binding.scenario != self._scenario or handoff.deadline != deadline):
                    raise RuntimeError('Authenticated fixture handoff unavailable')
                handoff.check_peer()
            job = (self._driver.open_containment() if handoff is None
                   else self._driver.open_containment(handoff=handoff))
            if job is None:
                raise RuntimeError('Containment unavailable')
            if self._clock() >= deadline:
                operation = 'deadline_exceeded'
            else:
                def check_deadline():
                    if self._clock() >= deadline:
                        raise _DeadlineExceeded()
                    if self._scenario == 'reject_containment':
                        return False
                    return True
                if handoff is None:
                    root = self._driver.start_contained(job, 'backend', before_resume=check_deadline)
                else:
                    root = self._driver.start_contained(job, 'backend', handoff=handoff)
                    if self._scenario == 'descendant':
                        self._driver.adopt_descendant(job, root, handoff)
                remaining = deadline - self._clock()
                if remaining <= 0:
                    operation = 'deadline_exceeded'
                else:
                    signalled = self._driver.wait_root(job, root, min(30000, ceil(remaining * 1000)))
                    if signalled is True:
                        operation = 'root_exited'
                    elif signalled is False and self._clock() >= deadline:
                        operation = 'deadline_exceeded'
                    else:
                        operation = 'wait_unverified'
        except _DeadlineExceeded:
            operation = 'deadline_exceeded'
        except Exception:
            operation = 'failed'
        finally:
            if job is not None:
                try:
                    if self._driver.stop_containment(job) is True:
                        if handoff is None or handoff.observe_cleanup() is True:
                            cleanup = 'observed'
                except Exception:
                    cleanup = 'uncertain'
            if type(handoff) is ControllerHandoff:
                handoff.abort()
        return Outcome(self.operation_id, operation, cleanup)
