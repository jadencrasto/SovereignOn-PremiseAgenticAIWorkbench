"""Synthetic local-handle observations, never native process execution."""
from dataclasses import asdict
import pytest

from tools.process_test_supervisor.process_observer import observe_process

OP = '9e89c286-0bd0-4e5b-8c65-7a35ae75be5e'


class FakeAPI:
    def __init__(self): self.ids = [(42, 100), (42, 100)]; self.alive = True
    def process_identity(self, handle): return self.ids.pop(0)
    def running(self, handle): return self.alive


@pytest.mark.parametrize('alive,state', [(True, 'running'), (False, 'exited')])
def test_observation_binds_identity_without_granting_cleanup(alive, state):
    api = FakeAPI(); api.alive = alive
    item = observe_process(OP, 'normal', object(), (42, 100), api=api, clock=lambda: 1)
    assert (item.state, item.process_id, item.creation_time) == (state, 42, 100)
    assert item.observer == 'local_handle_query_unverified'
    assert 'cleanup_verified' not in asdict(item)


@pytest.mark.parametrize('ids,error', [([(42, 101)], 'identity_mismatch'),
    ([(42, 100), (42, 101)], 'identity_or_state_changed'),
    ([(42, 0)], 'identity_mismatch'), ([(42.0, 100)], 'identity_mismatch')])
def test_stale_or_invalid_identity_is_unknown(ids, error):
    api=FakeAPI(); api.ids=ids
    item=observe_process(OP, 'normal', object(), (42, 100), api=api, clock=lambda: 1)
    assert item.state == 'unknown' and item.error == error and item.process_id is None


def test_native_failure_and_missing_handle_are_unknown():
    api=FakeAPI(); api.ids=[]
    assert observe_process(OP, 'normal', object(), (42,100), api=api, clock=lambda:1).state == 'unknown'
    assert observe_process(OP, 'normal', None, (42,100), api=api, clock=lambda:1).state == 'unknown'


@pytest.mark.parametrize('op,scenario,identity', [('invalid','normal',(42,100)),
    (OP,'unknown',(42,100)), (OP,'normal',(42,0)), (OP,'normal',(True,100))])
def test_invalid_binding_rejected(op,scenario,identity):
    with pytest.raises(ValueError): observe_process(op,scenario,object(),identity,api=FakeAPI())
