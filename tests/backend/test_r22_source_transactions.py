"""SQLite-only source transaction tests. No file or index is trusted here."""
import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from backend.auth.request_context import effective_owner, effective_clearance
from backend.security.provenance_foundation import QuarantinedSources, document_content_allowed


def inputs(sid, generation='generation-1'):
    manifest = json.dumps([{'chunk_id':sid+'_chunk_0', 'chunk_index':0,
                            'sha256':hashlib.sha256(b'chunk').hexdigest()}])
    config = json.dumps({'embedding_model':'fake', 'source_revision':1, 'dimensions':2,
                         'index_generation':generation})
    return manifest, config


def transaction(registry, sid, manifest, config):
    registry.record_manifest(sid, manifest_json=manifest, index_model='fake', index_configuration_json=config)


def test_real_sqlite_metadata_transition_and_config_binding(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid = registry.reserve_document(b'synthetic')
        manifest, cfg = inputs(sid)
        transaction(registry, sid, manifest, cfg)
        row = registry.read(sid)
        assert row['state'] == 'pending' and row['verification_state'] == 'manifest_pending'
        assert len(row['manifest_binding_sha256']) == 64
        with pytest.raises(PermissionError): transaction(registry, sid, manifest, cfg)
        # A syntactically valid changed generation previously passed readback.
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute('UPDATE quarantined_source_registry SET index_configuration_json=? WHERE source_id=?',
                         (inputs(sid, 'generation-2')[1], sid))
        with pytest.raises(ValueError, match='metadata changed'):
            registry.record_readback(sid, manifest_json=manifest, index_model='fake', nondurable=True)
        assert registry.read(sid)['verification_state'] == 'manifest_pending'
        assert not registry.retrieval_eligible(sid) and not document_content_allowed(sid, 1)
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_successful_readback_is_not_authorization_and_restart_never_promotes(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('viewer')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid = registry.reserve_document(b'synthetic')
        manifest, cfg = inputs(sid); transaction(registry, sid, manifest, cfg)
        registry.record_readback(sid, manifest_json=manifest, index_model='fake', nondurable=True)
        with pytest.raises(PermissionError):
            registry.record_readback(sid, manifest_json=manifest, index_model='fake', nondurable=True)
        assert registry.read(sid)['verification_state'] == 'readback_verified_nondurable'
        registry = QuarantinedSources(registry.path)
        registry.mark_restart_unverified()
        assert registry.read(sid)['reconciliation_status'] == 'restart_unverified'
        assert not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_sqlite_failure_rolls_back_manifest_and_binding_together(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('viewer')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid = registry.reserve_document(b'synthetic')
        manifest, cfg = inputs(sid)
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute("CREATE TRIGGER fail_manifest BEFORE UPDATE OF manifest_json ON quarantined_source_registry BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        with pytest.raises(sqlite3.IntegrityError): transaction(registry, sid, manifest, cfg)
        row = registry.read(sid)
        assert row['manifest_json'] is None and row['manifest_binding_sha256'] is None and row['verification_state'] is None
        assert not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_two_concurrent_registry_writers_cannot_both_commit(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid = registry.reserve_document(b'synthetic')
        manifest, cfg = inputs(sid); barrier = threading.Barrier(2, timeout=5)
        def writer():
            own = effective_owner.set('owner'); clear = effective_clearance.set('operator')
            try:
                barrier.wait()
                try:
                    transaction(registry, sid, manifest, cfg)
                    return 'committed'
                except PermissionError:
                    return 'rejected'
            finally: effective_clearance.reset(clear); effective_owner.reset(own)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(writer) for _ in range(2)]
            outcomes = [future.result(timeout=8) for future in futures]
        assert sorted(outcomes) == ['committed', 'rejected']
        assert registry.read(sid)['verification_state'] == 'manifest_pending'
        assert not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_stale_revision_revocation_missing_binding_and_missing_store_fail_closed(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('viewer')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite')
        stale = registry.reserve_document(b'synthetic'); m, cfg = inputs(stale); transaction(registry, stale, m, cfg)
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute('UPDATE quarantined_source_registry SET source_revision=2 WHERE source_id=?', (stale,))
        with pytest.raises(PermissionError): registry.record_readback(stale, manifest_json=m, index_model='fake', nondurable=True)
        missing = registry.reserve_document(b'synthetic'); m2, cfg2 = inputs(missing); transaction(registry, missing, m2, cfg2)
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute('UPDATE quarantined_source_registry SET manifest_binding_sha256=NULL WHERE source_id=?', (missing,))
        with pytest.raises(ValueError, match='metadata changed'): registry.record_readback(missing, manifest_json=m2, index_model='fake', nondurable=True)
        revoked = registry.reserve_document(b'synthetic'); m3, cfg3 = inputs(revoked); transaction(registry, revoked, m3, cfg3)
        assert registry.revoke(revoked)
        with pytest.raises(PermissionError): registry.record_readback(revoked, manifest_json=m3, index_model='fake', nondurable=True)
        assert not registry.retrieval_eligible(revoked)
        registry.path = tmp_path/'nonexistent.sqlite'
        with pytest.raises(sqlite3.OperationalError): registry.read(revoked)
        with pytest.raises(sqlite3.OperationalError): registry.reserve_document(b'not written')
        assert not registry.path.exists()
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_sqlite_writer_lock_failure_is_explicit(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('viewer')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid = registry.reserve_document(b'synthetic')
        manifest, cfg = inputs(sid)
        lock = sqlite3.connect(str(registry.path), isolation_level=None)
        try:
            lock.execute('BEGIN IMMEDIATE')
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                transaction(registry, sid, manifest, cfg)
            lock.execute('ROLLBACK')
        finally: lock.close()
        assert registry.read(sid)['manifest_json'] is None
        assert not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_restart_cannot_complete_interrupted_manifest(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('viewer')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid = registry.reserve_document(b'synthetic')
        manifest, cfg = inputs(sid); transaction(registry, sid, manifest, cfg)
        registry = QuarantinedSources(registry.path)
        registry.mark_restart_unverified()
        with pytest.raises(PermissionError):
            registry.record_readback(sid, manifest_json=manifest, index_model='fake', nondurable=True)
        row = registry.read(sid)
        assert row['state'] == 'pending' and row['reconciliation_status'] == 'restart_unverified'
        assert row['verification_state'] == 'manifest_pending' and not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


@pytest.mark.parametrize('column,value', [
    ('verification_state','unexpected'), ('index_model','orphan'),
    ('index_configuration_json','{}'), ('verification_timestamp','unexpected'),
    ('document_reference','wrong'), ('revoked_at','unexpected'),
])
def test_manifest_does_not_overwrite_corrupt_prior_state(tmp_path, column, value):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('viewer')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid = registry.reserve_document(b'synthetic')
        manifest, cfg = inputs(sid)
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute('UPDATE quarantined_source_registry SET ' + column + '=? WHERE source_id=?', (value, sid))
        with pytest.raises(PermissionError): transaction(registry, sid, manifest, cfg)
        row = registry.read(sid)
        assert row[column] == value and row['manifest_json'] is None
        assert not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_restart_preserves_unknown_recovery_state(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('viewer')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid = registry.reserve_document(b'synthetic')
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute("UPDATE quarantined_source_registry SET reconciliation_status='unknown_corruption' WHERE source_id=?", (sid,))
        registry.mark_restart_unverified()
        assert registry.read(sid)['reconciliation_status'] == 'unknown_corruption'
        manifest, cfg = inputs(sid)
        with pytest.raises(PermissionError): transaction(registry, sid, manifest, cfg)
        assert not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_cleanup_api_reports_blocked_after_revocation():
    from unittest.mock import MagicMock
    from fastapi import HTTPException
    from backend.api.documents import delete_document
    from test_a21_conversation_offline import immediate
    service = MagicMock(); service.delete_document.side_effect = RuntimeError('private diagnostic')
    with pytest.raises(HTTPException) as error:
        immediate(delete_document('synthetic', service, MagicMock()))
    assert error.value.status_code == 503
    assert error.value.headers['X-Error-Code'] == 'DOCUMENT_UNAVAILABLE'
    assert 'private diagnostic' not in str(error.value.detail)


def test_changed_source_digest_and_owner_cannot_complete_readback(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid = registry.reserve_document(b'synthetic')
        manifest, cfg = inputs(sid); transaction(registry, sid, manifest, cfg)
        other = effective_owner.set('other')
        try:
            with pytest.raises(PermissionError):
                registry.record_readback(sid, manifest_json=manifest, index_model='fake', nondurable=True)
        finally: effective_owner.reset(other)
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute('UPDATE quarantined_source_registry SET content_sha256=? WHERE source_id=?', ('a'*64, sid))
        with pytest.raises(ValueError, match='metadata changed'):
            registry.record_readback(sid, manifest_json=manifest, index_model='fake', nondurable=True)
        assert registry.read(sid)['verification_state'] == 'manifest_pending'
        assert not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)
