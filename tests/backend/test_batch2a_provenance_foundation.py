import pytest
from backend.auth.request_context import effective_owner
from backend.security.provenance_foundation import QuarantinedSources, SecurityPolicy


def test_all_unverified_resources_are_denied():
    policy = SecurityPolicy()
    for operation in ('conversation', 'document', 'tool_result', 'task_result', 'artifact'):
        assert not policy.may_disclose(operation, {'owner': 'a', 'verified': True})
    assert not policy.may_disclose('unknown', {})


def test_quarantine_requires_server_context_and_survives_restart(tmp_path):
    path = tmp_path / 'synthetic.db'
    registry = QuarantinedSources(path)
    with pytest.raises(PermissionError):
        registry.reserve('doc-synthetic')
    token = effective_owner.set('user-a')
    try:
        source_id = registry.reserve('doc-synthetic')
        assert QuarantinedSources(path).read(source_id)['owner_id'] == 'user-a'
        assert registry.read(source_id)['state'] == 'pending'
    finally:
        effective_owner.reset(token)
    token = effective_owner.set('user-b')
    try:
        assert registry.read(source_id) is None
        assert not registry.revoke(source_id)
    finally:
        effective_owner.reset(token)
    token = effective_owner.set('user-a')
    try:
        assert registry.revoke(source_id)
        assert registry.read(source_id)['state'] == 'revoked'
        assert not registry.revoke(source_id)
    finally:
        effective_owner.reset(token)
