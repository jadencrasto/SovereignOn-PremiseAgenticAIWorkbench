"""Quarantined source transitions remain non-authorizing."""
import pytest
import hashlib
import json
from backend.auth.request_context import effective_owner, effective_clearance
from backend.security.provenance_foundation import QuarantinedSources


def test_readback_is_one_shot_and_restart_cannot_promote_stale_work(tmp_path):
    registry = QuarantinedSources(tmp_path / 'sources.db')
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('operator')
    try:
        def manifest(source):
            return json.dumps([{'chunk_id': source + '_chunk_0', 'chunk_index': 0,
                'sha256': hashlib.sha256(b'synthetic chunk').hexdigest()}])
        cfg = json.dumps({'embedding_model': 'fake', 'source_revision': 1, 'dimensions': 2})
        first = registry.reserve_document(b'synthetic one')
        registry.record_manifest(first, manifest_json=manifest(first), index_model='fake', index_configuration_json=cfg)
        registry.record_readback(first, manifest_json=manifest(first), index_model='fake', nondurable=True)
        with pytest.raises(PermissionError):
            registry.record_readback(first, manifest_json=manifest(first), index_model='fake', nondurable=False)
        assert registry.read(first)['verification_state'] == 'readback_verified_nondurable'
        pending = registry.reserve_document(b'synthetic two')
        registry.record_manifest(pending, manifest_json=manifest(pending), index_model='fake', index_configuration_json=cfg)
        missing = registry.reserve_document(b'synthetic three')
        registry.mark_restart_unverified()
        with pytest.raises(PermissionError):
            registry.record_readback(pending, manifest_json=manifest(pending), index_model='fake', nondurable=False)
        with pytest.raises(PermissionError):
            registry.record_manifest(missing, manifest_json=manifest(missing), index_model='fake', index_configuration_json=cfg)
        assert registry.read(pending)['verification_state'] == 'manifest_pending'
        assert registry.read(missing)['manifest_json'] is None
        assert registry.retrieval_eligible(pending) is False
        assert registry.revoke(pending)
        with pytest.raises(PermissionError):
            registry.record_readback(pending, manifest_json=manifest(pending), index_model='fake', nondurable=True)
    finally:
        effective_clearance.reset(clearance); effective_owner.reset(owner)
