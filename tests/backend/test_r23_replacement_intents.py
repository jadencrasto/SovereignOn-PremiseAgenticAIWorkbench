"""Non-authorizing durable replacement intents; SQLite-only offline tests."""
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from backend.auth.request_context import effective_owner, effective_clearance
from backend.security.provenance_foundation import QuarantinedSources, document_content_allowed
from test_r22_source_transactions import inputs, transaction


def observed_source(registry):
    sid = registry.reserve_document(b'original synthetic source')
    manifest, cfg = inputs(sid)
    transaction(registry, sid, manifest, cfg)
    registry.record_readback(sid, manifest_json=manifest, index_model='fake', nondurable=True)
    return sid, manifest, cfg


def test_intent_is_persistent_but_never_stages_or_activates(tmp_path):
    owner = effective_owner.set('owner'); clear = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid, _, _ = observed_source(registry)
        old = registry.read(sid)
        operation = registry.begin_replacement(sid, expected_revision=1, content=b'candidate')
        record = registry.read(sid)
        assert record['replacement_operation_id'] == operation
        assert record['replacement_expected_revision'] == 1 and record['replacement_proposed_revision'] == 2
        assert record['replacement_expected_generation'] == 'generation-1'
        assert len(record['replacement_proposed_generation']) == 32
        assert record['replacement_decision'] == 'undecided'
        assert registry.replacement_status(sid, operation) == 'storage_boundary_required'
        assert record['source_revision'] == old['source_revision'] and record['content_sha256'] == old['content_sha256']
        assert record['state'] == 'pending' and not registry.retrieval_eligible(sid) and not document_content_allowed(sid, 2)
        with pytest.raises(PermissionError): registry.begin_replacement(sid, expected_revision=1, content=b'another')
        with pytest.raises(ValueError): registry.begin_replacement(sid, expected_revision=2, content=b'another')
        # Reopening the real database preserves the blocked intent; there is no automatic promotion.
        registry = QuarantinedSources(registry.path)
        registry.mark_restart_unverified()
        assert registry.replacement_status(sid, operation) == 'recovery_required'
        assert registry.read(sid)['source_revision'] == 1 and not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clear); effective_owner.reset(owner)


def test_missing_generation_or_incomplete_readback_rejects_intent(tmp_path):
    owner = effective_owner.set('owner'); clear = effective_clearance.set('viewer')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid = registry.reserve_document(b'old')
        with pytest.raises(PermissionError): registry.begin_replacement(sid, expected_revision=1, content=b'new')
        manifest, cfg = inputs(sid); cfg_dict = json.loads(cfg); del cfg_dict['index_generation']
        transaction(registry, sid, manifest, json.dumps(cfg_dict))
        registry.record_readback(sid, manifest_json=manifest, index_model='fake', nondurable=True)
        with pytest.raises(PermissionError, match='generation unavailable'):
            registry.begin_replacement(sid, expected_revision=1, content=b'new')
        assert registry.read(sid)['replacement_operation_id'] is None
    finally: effective_clearance.reset(clear); effective_owner.reset(owner)


def test_competing_replacements_only_one_intent(tmp_path):
    owner = effective_owner.set('owner'); clear = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid, _, _ = observed_source(registry)
        barrier = threading.Barrier(2, timeout=5)
        def worker():
            o=effective_owner.set('owner'); c=effective_clearance.set('operator')
            try:
                barrier.wait()
                try: return ('created', registry.begin_replacement(sid, expected_revision=1, content=b'new'))
                except PermissionError: return ('rejected', None)
            finally: effective_clearance.reset(c); effective_owner.reset(o)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(worker) for _ in range(2)]
            result=[future.result(timeout=8) for future in futures]
        assert sorted(x[0] for x in result) == ['created','rejected']
        assert registry.read(sid)['source_revision'] == 1 and not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clear); effective_owner.reset(owner)


def test_duplicate_operation_id_rejected_by_database(tmp_path, monkeypatch):
    owner = effective_owner.set('owner'); clear = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); first, _, _ = observed_source(registry); second, _, _ = observed_source(registry)
        monkeypatch.setattr('backend.security.provenance_foundation.uuid.uuid4', lambda: SimpleNamespace(hex='a'*32))
        operation = registry.begin_replacement(first, expected_revision=1, content=b'candidate')
        with pytest.raises(sqlite3.IntegrityError): registry.begin_replacement(second, expected_revision=1, content=b'candidate')
        assert registry.replacement_status(first, operation) == 'storage_boundary_required'
        assert registry.read(second)['replacement_operation_id'] is None
    finally: effective_clearance.reset(clear); effective_owner.reset(owner)


def test_sqlite_intent_failure_rolls_back(tmp_path):
    owner = effective_owner.set('owner'); clear = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid, _, _ = observed_source(registry)
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute("CREATE TRIGGER fail_replacement BEFORE UPDATE OF replacement_operation_id ON quarantined_source_registry BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with pytest.raises(sqlite3.IntegrityError): registry.begin_replacement(sid, expected_revision=1, content=b'new')
        row = registry.read(sid)
        assert row['replacement_operation_id'] is None and row['source_revision'] == 1 and row['state'] == 'pending'
        assert not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clear); effective_owner.reset(owner)


def test_revocation_and_corrupt_or_conflicting_intent_never_activate(tmp_path):
    owner = effective_owner.set('owner'); clear = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid, _, _ = observed_source(registry)
        op = registry.begin_replacement(sid, expected_revision=1, content=b'new')
        other = effective_owner.set('other')
        try:
            with pytest.raises(PermissionError): registry.replacement_status(sid, op)
        finally: effective_owner.reset(other)
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute("UPDATE quarantined_source_registry SET replacement_decision='committed' WHERE source_id=?", (sid,))
        assert registry.replacement_status(sid, op) == 'corrupt_operation'
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute("UPDATE quarantined_source_registry SET replacement_decision='undecided', source_revision=2 WHERE source_id=?", (sid,))
        assert registry.replacement_status(sid, op) == 'revision_conflict'
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute('UPDATE quarantined_source_registry SET source_revision=1 WHERE source_id=?', (sid,))
        assert registry.revoke(sid)
        assert registry.replacement_status(sid, op) == 'revoked_recovery_required'
        registry.mark_restart_unverified()
        assert registry.replacement_status(sid, op) == 'revoked_recovery_required'
        assert registry.read(sid)['state'] == 'revoked' and not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clear); effective_owner.reset(owner)


@pytest.mark.parametrize('column,value', [
    ('replacement_decision', 'committed'), ('replacement_stage', 'staged'),
    ('replacement_content_sha256', 'bad'), ('replacement_proposed_revision', 3),
    ('replacement_expected_generation', ''), ('replacement_proposed_generation', 'bad'),
])
def test_corrupt_intent_is_not_recoverable_or_authorizing(tmp_path, column, value):
    owner = effective_owner.set('owner'); clear = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid, _, _ = observed_source(registry)
        op = registry.begin_replacement(sid, expected_revision=1, content=b'new')
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute('UPDATE quarantined_source_registry SET ' + column + '=? WHERE source_id=?', (value, sid))
        assert registry.replacement_status(sid, op) == 'corrupt_operation'
        registry.mark_restart_unverified()
        assert registry.replacement_status(sid, op) == 'corrupt_operation'
        assert registry.read(sid)['source_revision'] == 1 and not registry.retrieval_eligible(sid)
    finally: effective_clearance.reset(clear); effective_owner.reset(owner)


def test_incomplete_intent_cannot_repeat_earlier_callbacks(tmp_path):
    owner = effective_owner.set('owner'); clear = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'db.sqlite'); sid, manifest, cfg = observed_source(registry)
        registry.begin_replacement(sid, expected_revision=1, content=b'new')
        with pytest.raises(PermissionError): registry.record_readback(sid, manifest_json=manifest, index_model='fake', nondurable=True)
        with pytest.raises(PermissionError): transaction(registry, sid, manifest, cfg)
        assert registry.read(sid)['source_revision'] == 1
    finally: effective_clearance.reset(clear); effective_owner.reset(owner)
