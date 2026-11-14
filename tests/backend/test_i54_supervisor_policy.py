"""Policy only. No native API, process, pipe, filesystem or socket tests."""
from dataclasses import replace
import json
import pytest
from tools.process_test_supervisor.policy import Observation, classify, run_case, serialize, MAX_OUTPUT


def complete():
    return Observation(owned_boundary=True,externally_bounded=True,completed=True,exit_code=0,
        cleanup='verified',owned_survivors=0,unrelated_preserved=True,scenario_verified=True)


def test_no_native_boundary_or_fixture_never_launches():
    assert run_case('root',True)['status']=='BLOCKED'
    assert run_case('root',False)['reason']=='fixture_missing'
    assert run_case('unknown',True)['reason']=='unknown_case'
    assert run_case('root',True,timeout_seconds=31)['reason']=='invalid_timeout'


def test_complete_fake_evidence_can_only_describe_synthetic_result():
    result=classify('root',complete())
    assert result['status']=='SIMULATED_PASS' and json.loads(serialize(result))==result


@pytest.mark.parametrize('change,expected', [
    ({'owned_boundary':False},'ownership_unverified'),
    ({'externally_bounded':False},'ownership_unverified'),
    ({'nested_conflict':True},'nested_job_conflict'),
    ({'cleanup':'unknown'},'cleanup_unverified'),
    ({'owned_survivors':None},'cleanup_unverified'),
    ({'unrelated_preserved':False},'unrelated_preservation_unverified'),
    ({'unrelated_preserved':None},'unrelated_preservation_unverified'),
    ({'completed':False},'exit_unverified'),
    ({'scenario_verified':False},'scenario_unverified'),
    ({'timed_out':None},'timeout_status_unverified'),
])
def test_incomplete_evidence_is_blocked(change,expected):
    result=classify('root',replace(complete(),**change))
    assert result['status']=='BLOCKED' and result['reason']==expected


def test_timeouts_exit_codes_and_survivors_are_failures():
    for changed,reason in [({'timed_out':True},'external_timeout'),({'exit_code':5},'test_host_failed'),
                           ({'cleanup':'failed'},'cleanup_failed'),({'owned_survivors':1},'cleanup_failed')]:
        result=classify('root',replace(complete(),**changed));assert result['status']=='FAIL' and result['reason']==reason


def test_output_serialization_is_bounded_and_native_errors_categorical():
    result=classify('root',replace(complete(),stdout='x'*(MAX_OUTPUT+20),stderr='y'*(MAX_OUTPUT+20),native_error='secret path'))
    assert len(result['stdout'])==MAX_OUTPUT and len(result['stderr'])==MAX_OUTPUT and result['native_error'] is None
    assert result['status']=='FAIL' and result['reason']=='native_error'


def test_interrupt_never_claims_cleanup_succeeded():
    class Interrupted:
        def execute(self,case,timeout): raise KeyboardInterrupt()
    result=run_case('root',True,Interrupted())
    assert result=={'status':'BLOCKED','reason':'supervisor_interrupted_cleanup_unknown'}
