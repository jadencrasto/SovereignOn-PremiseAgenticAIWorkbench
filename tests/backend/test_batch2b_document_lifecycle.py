from concurrent.futures import ThreadPoolExecutor
import hashlib
import pytest
from backend.auth.request_context import effective_owner, effective_clearance
from backend.security.provenance_foundation import QuarantinedSources


def test_pending_document_is_server_owned_and_never_eligible(tmp_path):
    registry = QuarantinedSources(tmp_path / 'documents.db')
    owner = effective_owner.set('alice')
    clearance = effective_clearance.set('operator')
    try:
        source = registry.reserve_document(b'synthetic document')
        row = registry.read(source)
        assert row['owner_id'] == 'alice'
        assert row['classification'] == 'operator'
        assert row['content_sha256'] == hashlib.sha256(b'synthetic document').hexdigest()
        assert row['source_revision'] == 1
        assert row['state'] == 'pending'
        assert not registry.retrieval_eligible(source)
        with pytest.raises(TypeError):
            registry.reserve_document(b'data', owner_id='bob')
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)


def test_document_revocation_and_legacy_are_never_eligible_after_restart(tmp_path):
    path = tmp_path / 'documents.db'
    registry = QuarantinedSources(path)
    owner = effective_owner.set('alice')
    clearance = effective_clearance.set('viewer')
    try:
        legacy = registry.reserve('legacy')
        source = registry.reserve_document(b'synthetic')
        assert registry.revoke(source)
        restarted = QuarantinedSources(path)
        assert restarted.read(source)['state'] == 'revoked'
        assert not restarted.retrieval_eligible(source)
        assert not restarted.retrieval_eligible(legacy)
        assert not restarted.retrieval_eligible('forged')
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)


def test_concurrent_registration_never_reuses_identity_or_activates(tmp_path):
    registry = QuarantinedSources(tmp_path / 'documents.db')
    def create(owner_id):
        owner = effective_owner.set(owner_id)
        clearance = effective_clearance.set('viewer')
        try:
            source = registry.reserve_document(b'identical')
            assert registry.read(source)['owner_id'] == owner_id
            assert not registry.retrieval_eligible(source)
            return source
        finally:
            effective_clearance.reset(clearance)
            effective_owner.reset(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(create, ['alice', 'bob']))
    assert ids[0] != ids[1]


def test_missing_clearance_and_empty_content_fail_closed(tmp_path):
    registry = QuarantinedSources(tmp_path / 'documents.db')
    owner = effective_owner.set('alice')
    try:
        with pytest.raises(PermissionError):
            registry.reserve_document(b'synthetic')
        clearance = effective_clearance.set('viewer')
        try:
            with pytest.raises(ValueError):
                registry.reserve_document(b'')
        finally:
            effective_clearance.reset(clearance)
    finally:
        effective_owner.reset(owner)


def test_additive_migration_preserves_existing_pending_record(tmp_path):
    import sqlite3
    path = tmp_path / 'documents.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE quarantined_source_registry (source_id TEXT PRIMARY KEY, document_reference TEXT NOT NULL, owner_id TEXT NOT NULL, state TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT)')
        conn.execute("INSERT INTO quarantined_source_registry VALUES ('legacy','untrusted','alice','pending',1,'synthetic',NULL)")
    registry = QuarantinedSources(path)
    owner = effective_owner.set('alice')
    try:
        row = registry.read('legacy')
        assert row['document_reference'] == 'untrusted'
        assert row['content_sha256'] is None
        assert not registry.retrieval_eligible('legacy')
    finally:
        effective_owner.reset(owner)
