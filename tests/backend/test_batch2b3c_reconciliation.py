import json
from unittest.mock import AsyncMock, MagicMock
import pytest
from backend.auth.request_context import effective_clearance, effective_owner
from backend.config import Settings
from backend.rag.service import DocumentService
from backend.rag.store import VectorStore, _InMemoryFallbackCollection
from backend.security.provenance_foundation import QuarantinedSources


@pytest.mark.asyncio
async def test_index_configuration_records_unknown_model_and_fallback(tmp_path):
    cfg = Settings(upload_dir=tmp_path / 'uploads', chroma_persist_dir=tmp_path / 'chroma', chunk_size=80, chunk_overlap=10)
    reg = QuarantinedSources(tmp_path / 'sources.db')
    store = VectorStore.__new__(VectorStore)
    store._client = None
    store._collection = _InMemoryFallbackCollection()
    embed = MagicMock()
    embed.embed_many = AsyncMock(side_effect=lambda texts: [[1.0, 0.0] for _ in texts])
    owner = effective_owner.set('a')
    clearance = effective_clearance.set('viewer')
    try:
        service = DocumentService(cfg, embed, store, MagicMock(), source_registry=reg)
        result = await service.ingest_document('synthetic.txt', b'synthetic fixture')
        config = json.loads(reg.read(result.document_id)['index_configuration_json'])
        assert config['embedding_model'] == cfg.embedding_model
        assert config['model_digest'] is None
        assert config['dimensions'] == 2
        assert config['backend_evidence'] == 'in_memory_nondurable'
        assert config['chunk_size'] == 80
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)


def test_restart_never_repromotes_and_preserves_evidence(tmp_path):
    reg = QuarantinedSources(tmp_path / 'db.sqlite')
    owner = effective_owner.set('a')
    clearance = effective_clearance.set('viewer')
    try:
        pending = reg.reserve_document(b'synthetic')
        revoked = reg.reserve_document(b'other')
        assert reg.revoke(revoked)
        assert reg.mark_restart_unverified() == 2
        restarted = QuarantinedSources(reg.path)
        assert restarted.read(pending)['reconciliation_status'] == 'restart_unverified'
        assert restarted.read(revoked)['reconciliation_status'] == 'revoked_cleanup_review'
        assert restarted.read(revoked)['state'] == 'revoked'
        assert restarted.mark_restart_unverified() == 2
        assert not restarted.retrieval_eligible(pending)
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)


@pytest.mark.asyncio
async def test_inconsistent_embedding_dimensions_remain_pending(tmp_path):
    import sqlite3
    cfg = Settings(upload_dir=tmp_path / 'uploads', chunk_size=20, chunk_overlap=2)
    reg = QuarantinedSources(tmp_path / 'sources.db')
    store = VectorStore.__new__(VectorStore)
    store._client = None
    store._collection = _InMemoryFallbackCollection()
    embed = MagicMock()
    embed.embed_many = AsyncMock(return_value=[[1.0], [1.0, 2.0]])
    owner = effective_owner.set('a')
    clearance = effective_clearance.set('viewer')
    try:
        service = DocumentService(cfg, embed, store, MagicMock(), source_registry=reg)
        with pytest.raises(RuntimeError, match='dimensionality'):
            await service.ingest_document('synthetic.txt', b'First synthetic paragraph.\n\nSecond synthetic paragraph.')
        with sqlite3.connect(reg.path) as conn:
            state, evidence = conn.execute('SELECT state, verification_state FROM quarantined_source_registry').fetchone()
        assert state == 'pending' and evidence is None
        assert store.count() == 0
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)
