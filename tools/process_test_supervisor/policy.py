"""Fail-closed policy for future independent native process tests.

No native process runner is implemented here. The caller cannot select an
executable or command. A fake adapter may only be supplied by unit tests.
"""
from dataclasses import dataclass
import json
from typing import Protocol

MAX_OUTPUT = 4096
CASES = frozenset({'root', 'descendant', 'multiple_descendants', 'early_root_exit',
 'kill_on_close', 'explicit_cleanup', 'partial_start', 'child_crash', 'repeat_stop',
 'unrelated_preserved', 'startup_timeout', 'listener_ipv4', 'listener_ipv6',
 'wrong_listener', 'listener_change', 'stale_pid', 'nested_job', 'supervisor_interrupt',
 'cleanup_uncertain', 'no_survivors'})


@dataclass(frozen=True)
class Observation:
    owned_boundary: bool = False
    externally_bounded: bool = False
    completed: bool = False
    timed_out: bool = False
    exit_code: int | None = None
    cleanup: str = 'unknown'
    owned_survivors: int | None = None
    unrelated_preserved: bool | None = None
    scenario_verified: bool = False
    nested_conflict: bool = False
    native_error: str | None = None
    stdout: str = ''
    stderr: str = ''


class SupervisorBoundary(Protocol):
    def execute(self, case: str, timeout_seconds: int) -> Observation: ...


class UnavailableBoundary:
    def execute(self, case, timeout_seconds):
        raise RuntimeError('Independent process boundary is unavailable')


def bounded_text(value):
    if not isinstance(value, str): return ''
    return value[:MAX_OUTPUT]


def classify(case, observation):
    if case not in CASES or not isinstance(observation, Observation):
        return {'status':'BLOCKED','reason':'invalid_observation'}
    error_category = observation.native_error if observation.native_error in ('access_denied','assignment_failed','query_failed','timeout','other') else None
    result = {'status':'BLOCKED', 'reason':'ownership_unverified', 'timed_out':observation.timed_out,
              'exit_code':observation.exit_code, 'cleanup':observation.cleanup,
              'native_error':error_category,
              'stdout':bounded_text(observation.stdout), 'stderr':bounded_text(observation.stderr)}
    if observation.nested_conflict is True:
        result['reason']='nested_job_conflict'; return result
    if observation.owned_boundary is not True or observation.externally_bounded is not True:
        return result
    if observation.timed_out is not False and observation.timed_out is not True:
        result['reason']='timeout_status_unverified'; return result
    if observation.timed_out is True:
        result.update(status='FAIL', reason='external_timeout'); return result
    if observation.native_error is not None:
        result.update(status='FAIL', reason='native_error'); return result
    if observation.cleanup not in ('verified', 'failed', 'unknown'):
        result['reason']='invalid_cleanup_evidence'; return result
    if observation.cleanup == 'failed' or (isinstance(observation.owned_survivors, int) and observation.owned_survivors > 0):
        result.update(status='FAIL', reason='cleanup_failed'); return result
    if observation.cleanup != 'verified' or type(observation.owned_survivors) is not int or observation.owned_survivors != 0:
        result['reason']='cleanup_unverified'; return result
    if observation.unrelated_preserved is not True:
        result['reason']='unrelated_preservation_unverified'; return result
    if observation.completed is not True or observation.exit_code is None:
        result['reason']='exit_unverified'; return result
    if type(observation.exit_code) is not int or observation.exit_code != 0:
        result.update(status='FAIL', reason='test_host_failed'); return result
    if observation.scenario_verified is not True:
        result['reason']='scenario_unverified'; return result
    # There is no real boundary implementation or independent evidence verifier.
    result.update(status='SIMULATED_PASS', reason='synthetic_evidence_complete')
    return result


def run_case(case, fixture_exists, boundary=None, timeout_seconds=15):
    if case not in CASES: return {'status':'BLOCKED','reason':'unknown_case'}
    if fixture_exists is not True: return {'status':'BLOCKED','reason':'fixture_missing'}
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
        return {'status':'BLOCKED','reason':'invalid_timeout'}
    adapter = boundary if boundary is not None else UnavailableBoundary()
    try:
        return classify(case, adapter.execute(case, timeout_seconds))
    except KeyboardInterrupt:
        return {'status':'BLOCKED','reason':'supervisor_interrupted_cleanup_unknown'}
    except Exception:
        return {'status':'BLOCKED','reason':'independent_boundary_unavailable'}


def serialize(result):
    return json.dumps(result, sort_keys=True, separators=(',', ':'))
