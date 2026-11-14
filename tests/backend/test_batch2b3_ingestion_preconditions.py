"""Synthetic evidence for pending upload storage, not proof of activation."""
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import UploadFile
from backend.api.documents import upload_document
from backend.auth.models import User
from backend.auth.request_context import effective_clearance, effective_owner
from backend.config import Settings
from backend.rag.service import DocumentService
from backend.rag.store import VectorStore, _InMemoryFallbackCollection
from backend.security.provenance_foundation import QuarantinedSources


@pytest.mark.asyncio
async def test_current_upload_remains_pending_and_retrieval_denied(tmp_path):
    user = User(id='synthetic-a', username='synthetic-a', password_hash='', role='operator',
                clearance='L1', created_at='2026-01-01T00:00:00Z')
    cfg = Settings(upload_dir=tmp_path / 'uploads', chunk_size=80, chunk_overlap=10)
    store = VectorStore.__new__(VectorStore)
    store._collection = _InMemoryFallbackCollection()
    embedder = MagicMock()
    embedder.embed_many = AsyncMock(side_effect=lambda texts: [[1.0] for _ in texts])
    retriever = MagicMock()
    retriever.retrieve = AsyncMock()
    registry = QuarantinedSources(tmp_path / 'sources.db')
    service = DocumentService(cfg, embedder, store, retriever, source_registry=registry)
    owner_token = effective_owner.set(user.id)
    clearance_token = effective_clearance.set('viewer')
    try:
        upload = UploadFile(filename='synthetic.txt', file=BytesIO(b'synthetic public fixture'))
        result = await upload_document(upload, service, user)
        assert result.document_id.startswith('doc_')
        assert store.count() > 0
        assert registry.read(result.document_id)['state'] == 'pending'
        assert not registry.retrieval_eligible(result.document_id)
        assert await service.retrieve('synthetic', user_clearance='admin') == []
        assert service.get_document_details(result.document_id, user_clearance='admin') is None
        retriever.retrieve.assert_not_awaited()
    finally:
        effective_clearance.reset(clearance_token)
        effective_owner.reset(owner_token)


@pytest.mark.asyncio
async def test_same_filename_and_prefix_preserve_distinct_uploads(tmp_path):
    cfg = Settings(upload_dir=tmp_path / 'uploads', chunk_size=800, chunk_overlap=100)
    store = VectorStore.__new__(VectorStore)
    store._collection = _InMemoryFallbackCollection()
    embedder = MagicMock()
    embedder.embed_many = AsyncMock(side_effect=lambda texts: [[1.0] for _ in texts])
    service = DocumentService(cfg, embedder, store, MagicMock(), source_registry=QuarantinedSources(tmp_path / 'sources.db'))
    first = b'A' * 1024 + b' first synthetic tail'
    second = b'A' * 1024 + b' second synthetic tail'
    owner_token = effective_owner.set('synthetic-owner')
    clearance_token = effective_clearance.set('viewer')
    try:
        first_result = await service.ingest_document('synthetic.txt', first, clearance='viewer')
        second_result = await service.ingest_document('synthetic.txt', second, clearance='viewer')
        assert first_result.document_id != second_result.document_id
        assert (cfg.upload_dir / f'{first_result.document_id}.bin').read_bytes() == first
        assert (cfg.upload_dir / f'{second_result.document_id}.bin').read_bytes() == second
    finally:
        effective_clearance.reset(clearance_token)
        effective_owner.reset(owner_token)
