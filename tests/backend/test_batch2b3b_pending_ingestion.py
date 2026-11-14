from unittest.mock import AsyncMock, MagicMock
import hashlib
import pytest
from backend.auth.request_context import effective_clearance, effective_owner
from backend.config import Settings
from backend.rag.service import DocumentService
from backend.rag.store import VectorStore, _InMemoryFallbackCollection
from backend.security.provenance_foundation import QuarantinedSources


@pytest.mark.asyncio
async def test_pending_record_precedes_file_and_retains_readback_evidence(tmp_path):
    from unittest.mock import patch
    from backend.rag.service import _persist_new_upload
    cfg = Settings(upload_dir=tmp_path / 'uploads', chunk_size=80, chunk_overlap=10)
    registry = QuarantinedSources(tmp_path / 'db.sqlite')
    store = VectorStore.__new__(VectorStore)
    store._collection = _InMemoryFallbackCollection()
    store._client = None
    embedder = MagicMock()
    async def embed(texts):
        import sqlite3
        with sqlite3.connect(registry.path) as conn:
            rows = conn.execute("SELECT source_id,state FROM quarantined_source_registry").fetchall()
        assert len(rows) == 1 and rows[0][1] == 'pending'
        assert (cfg.upload_dir / (rows[0][0] + '.bin')).read_bytes() == b'synthetic data'
        return [[1.0] for _ in texts]
    embedder.embed_many = AsyncMock(side_effect=embed)
    service = DocumentService(cfg, embedder, store, MagicMock(), source_registry=registry)
    owner = effective_owner.set('alice')
    clearance = effective_clearance.set('viewer')
    try:
        def check_pending_before_file(root, content, vector_store, reserved_id):
            record = registry.read(reserved_id)
            assert record['state'] == 'pending'
            assert not root.exists()
            return _persist_new_upload(root, content, vector_store, reserved_id)
        with patch('backend.rag.service._persist_new_upload', side_effect=check_pending_before_file):
            result = await service.ingest_document('synthetic.txt', b'synthetic data', clearance='viewer')
        assert result.status == 'pending'
        record = registry.read(result.document_id)
        assert record['state'] == 'pending'
        assert record['owner_id'] == 'alice'
        assert record['content_sha256'] == hashlib.sha256(b'synthetic data').hexdigest()
        assert record['verification_state'] == 'readback_verified_nondurable'
        assert not registry.retrieval_eligible(result.document_id)
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)


@pytest.mark.asyncio
async def test_revocation_commits_before_failed_index_cleanup(tmp_path):
    cfg = Settings(upload_dir=tmp_path / 'uploads', chunk_size=80, chunk_overlap=10)
    registry = QuarantinedSources(tmp_path / 'db.sqlite')
    store = VectorStore.__new__(VectorStore)
    store._collection = _InMemoryFallbackCollection()
    store._client = None
    embedder = MagicMock()
    embedder.embed_many = AsyncMock(side_effect=lambda texts: [[1.0] for _ in texts])
    service = DocumentService(cfg, embedder, store, MagicMock(), source_registry=registry)
    owner = effective_owner.set('alice')
    clearance = effective_clearance.set('viewer')
    try:
        result = await service.ingest_document('synthetic.txt', b'synthetic data', clearance='viewer')
        def fail(_):
            assert registry.read(result.document_id)['state'] == 'revoked'
            raise RuntimeError('synthetic cleanup failure')
        store.delete_document = fail
        with pytest.raises(RuntimeError):
            service.delete_document(result.document_id)
        assert QuarantinedSources(registry.path).read(result.document_id)['state'] == 'revoked'
        assert not registry.retrieval_eligible(result.document_id)
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'extra', 'mismatch', 'revoked'])
async def test_incomplete_index_or_revocation_never_records_verified(tmp_path, failure):
    import sqlite3
    cfg = Settings(upload_dir=tmp_path / 'uploads', chunk_size=80, chunk_overlap=10)
    registry = QuarantinedSources(tmp_path / 'db.sqlite')
    store = VectorStore.__new__(VectorStore)
    store._collection = _InMemoryFallbackCollection()
    store._client = None
    original = store._maintenance_get_document_chunks
    def tamper(doc_id):
        result = original(doc_id)
        if failure == 'revoked':
            assert registry.revoke(doc_id)
        elif failure == 'missing':
            result['ids'].clear()
        elif failure == 'extra':
            result['ids'].append('unexpected')
        else:
            result['documents'][0] = 'altered synthetic content'
        return result
    store._maintenance_get_document_chunks = tamper
    embedder = MagicMock()
    embedder.embed_many = AsyncMock(side_effect=lambda texts: [[1.0] for _ in texts])
    service = DocumentService(cfg, embedder, store, MagicMock(), source_registry=registry)
    owner = effective_owner.set('alice')
    clearance = effective_clearance.set('viewer')
    try:
        with pytest.raises((RuntimeError, PermissionError)):
            await service.ingest_document('synthetic.txt', b'synthetic data', clearance='viewer')
        with sqlite3.connect(registry.path) as conn:
            state, evidence = conn.execute('SELECT state, verification_state FROM quarantined_source_registry').fetchone()
        assert state == ('revoked' if failure == 'revoked' else 'pending')
        assert evidence == 'manifest_pending'
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)


@pytest.mark.asyncio
async def test_mismatched_clearance_rejected_before_reservation(tmp_path):
    cfg = Settings(upload_dir=tmp_path / 'uploads')
    registry = QuarantinedSources(tmp_path / 'db.sqlite')
    service = DocumentService(cfg, MagicMock(), MagicMock(), MagicMock(), source_registry=registry)
    owner = effective_owner.set('alice')
    clearance = effective_clearance.set('viewer')
    try:
        with pytest.raises(PermissionError):
            await service.ingest_document('synthetic.txt', b'synthetic', clearance='admin')
        assert not cfg.upload_dir.exists()
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)


@pytest.mark.asyncio
async def test_index_failure_remains_pending_after_restart_and_cross_owner_denied(tmp_path):
    import sqlite3
    cfg = Settings(upload_dir=tmp_path / 'uploads', chunk_size=80, chunk_overlap=10)
    registry = QuarantinedSources(tmp_path / 'db.sqlite')
    store = VectorStore.__new__(VectorStore)
    store._collection = _InMemoryFallbackCollection()
    store._client = None
    store.add_chunks = MagicMock(side_effect=RuntimeError('synthetic index failure'))
    embedder = MagicMock()
    embedder.embed_many = AsyncMock(side_effect=lambda texts: [[1.0] for _ in texts])
    service = DocumentService(cfg, embedder, store, MagicMock(), source_registry=registry)
    owner = effective_owner.set('alice')
    clearance = effective_clearance.set('viewer')
    try:
        with pytest.raises(RuntimeError):
            await service.ingest_document('synthetic.txt', b'synthetic data', clearance='viewer')
        with sqlite3.connect(registry.path) as conn:
            source_id, state, evidence, storage_ref = conn.execute(
                'SELECT source_id,state,verification_state,storage_ref FROM quarantined_source_registry').fetchone()
        assert state == 'pending' and evidence == 'manifest_pending'
        assert (cfg.upload_dir / storage_ref).read_bytes() == b'synthetic data'
        restarted = QuarantinedSources(registry.path)
        assert restarted.read(source_id)['state'] == 'pending'
        other = effective_owner.set('bob')
        try:
            assert restarted.read(source_id) is None
            assert not restarted.revoke(source_id)
        finally:
            effective_owner.reset(other)
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)


@pytest.mark.asyncio
async def test_source_mismatch_keeps_pending_and_does_not_index(tmp_path, monkeypatch):
    import sqlite3
    from backend.rag.service import _persist_new_upload
    cfg = Settings(upload_dir=tmp_path / 'uploads', chunk_size=80, chunk_overlap=10)
    registry = QuarantinedSources(tmp_path / 'db.sqlite')
    store = VectorStore.__new__(VectorStore)
    store._collection = _InMemoryFallbackCollection()
    store._client = None
    def corrupt(root, content, vector_store, reserved_id):
        document_id, path = _persist_new_upload(root, content, vector_store, reserved_id)
        path.write_bytes(b'altered synthetic data')
        return document_id, path
    monkeypatch.setattr('backend.rag.service._persist_new_upload', corrupt)
    service = DocumentService(cfg, MagicMock(), store, MagicMock(), source_registry=registry)
    owner = effective_owner.set('alice')
    clearance = effective_clearance.set('viewer')
    try:
        with pytest.raises(OSError, match='integrity'):
            await service.ingest_document('synthetic.txt', b'synthetic data', clearance='viewer')
        with sqlite3.connect(registry.path) as conn:
            state, evidence = conn.execute('SELECT state, verification_state FROM quarantined_source_registry').fetchone()
        assert state == 'pending' and evidence is None
        assert store.count() == 0
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)
