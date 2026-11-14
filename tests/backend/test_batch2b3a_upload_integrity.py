from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from backend.config import Settings
from backend.rag.service import DocumentService, _persist_new_upload
from backend.rag.store import VectorStore, _InMemoryFallbackCollection
from backend.security.provenance_foundation import QuarantinedSources
from backend.auth.request_context import effective_owner, effective_clearance


@pytest.fixture(autouse=True)
def synthetic_identity():
    owner = effective_owner.set('synthetic-owner')
    clearance = effective_clearance.set('viewer')
    try:
        yield
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)


def store():
    result = VectorStore.__new__(VectorStore)
    result._collection = _InMemoryFallbackCollection()
    return result


def test_forced_collision_does_not_overwrite(tmp_path):
    existing = tmp_path / ('doc_v1_' + 'a' * 32 + '.bin')
    existing.write_bytes(b'preserved')
    with patch('backend.rag.service.uuid.uuid4', return_value=SimpleNamespace(hex='a' * 32)):
        with pytest.raises(OSError):
            _persist_new_upload(tmp_path, b'new', store())
    assert existing.read_bytes() == b'preserved'


def test_concurrent_identical_uploads_get_distinct_files(tmp_path):
    vector_store = store()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _persist_new_upload(tmp_path, b'synthetic', vector_store), range(2)))
    assert results[0][0] != results[1][0]
    assert all(path.read_bytes() == b'synthetic' for _, path in results)


def test_digest_mismatch_is_rejected_and_file_preserved(tmp_path, monkeypatch):
    from pathlib import Path
    original = Path.open
    def open_corrupt(path, mode='r', *args, **kwargs):
        return BytesIO(b'corrupt') if mode == 'rb' and path.suffix == '.bin' else original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', open_corrupt)
    with pytest.raises(OSError, match='integrity'):
        _persist_new_upload(tmp_path, b'synthetic', store())
    assert len(list(tmp_path.glob('doc_v1_*.bin'))) == 1


def test_exclusive_creation_failure_never_falls_back(tmp_path):
    with patch('backend.rag.service.os.open', side_effect=PermissionError('synthetic')):
        with pytest.raises(PermissionError):
            _persist_new_upload(tmp_path, b'synthetic', store())
    assert list(tmp_path.iterdir()) == []


def test_partial_write_rejected_without_removing_orphan(tmp_path):
    import os
    original = os.fdopen
    class PartialWriter:
        def __init__(self, fd, mode): self.stream = original(fd, mode)
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def write(self, data): return self.stream.write(data[:1])
    with patch('backend.rag.service.os.fdopen', side_effect=lambda fd, mode: PartialWriter(fd, mode)):
        with pytest.raises(OSError, match='incomplete'):
            _persist_new_upload(tmp_path, b'synthetic', store())
    assert [p.read_bytes() for p in tmp_path.glob('doc_v1_*.bin')] == [b's']


def test_index_id_collision_retries_without_touching_old_index(tmp_path):
    vector_store = store()
    old_id = 'doc_v1_' + 'a' * 32
    vector_store._collection.upsert(ids=[old_id + '_chunk_0'], embeddings=[[1.0]], documents=['old'],
                                    metadatas=[{'document_id':old_id}])
    ids = [SimpleNamespace(hex='a' * 32), SimpleNamespace(hex='b' * 32)]
    with patch('backend.rag.service.uuid.uuid4', side_effect=ids):
        new_id, path = _persist_new_upload(tmp_path, b'new', vector_store)
    assert new_id == 'doc_v1_' + 'b' * 32
    assert path.read_bytes() == b'new'
    assert vector_store._maintenance_get_document_chunks(old_id)['documents'] == ['old']


@pytest.mark.asyncio
async def test_index_failure_preserves_new_file_and_legacy_file(tmp_path):
    cfg = Settings(upload_dir=tmp_path / 'uploads', chunk_size=80, chunk_overlap=10)
    cfg.upload_dir.mkdir()
    legacy = cfg.upload_dir / 'doc_legacy_synthetic.txt'
    legacy.write_bytes(b'legacy data')
    vector_store = store()
    vector_store.add_chunks = MagicMock(side_effect=RuntimeError('synthetic failure'))
    embedder = MagicMock()
    embedder.embed_many = AsyncMock(side_effect=lambda texts: [[1.0] for _ in texts])
    service = DocumentService(cfg, embedder, vector_store, MagicMock(), source_registry=QuarantinedSources(tmp_path / 'sources.db'))
    with pytest.raises(RuntimeError):
        await service.ingest_document('synthetic.txt', b'synthetic data')
    assert legacy.read_bytes() == b'legacy data'
    assert [p.read_bytes() for p in cfg.upload_dir.glob('doc_v1_*.bin')] == [b'synthetic data']


@pytest.mark.asyncio
async def test_index_uses_server_identity_and_revision(tmp_path):
    cfg = Settings(upload_dir=tmp_path / 'uploads', chunk_size=80, chunk_overlap=10)
    vector_store = store()
    embedder = MagicMock()
    embedder.embed_many = AsyncMock(side_effect=lambda texts: [[1.0] for _ in texts])
    service = DocumentService(cfg, embedder, vector_store, MagicMock(), source_registry=QuarantinedSources(tmp_path / 'sources.db'))
    result = await service.ingest_document('synthetic.txt', b'synthetic data')
    records = vector_store._maintenance_get_document_chunks(result.document_id)
    assert records['ids'] and all(x.startswith(result.document_id + '_chunk_') for x in records['ids'])
    assert all(x['document_id'] == result.document_id and x['source_revision'] == 1 for x in records['metadatas'])
    assert (cfg.upload_dir / (result.document_id + '.bin')).read_bytes() == b'synthetic data'


@pytest.mark.asyncio
async def test_identical_uploads_and_deletion_preserve_other_files(tmp_path):
    cfg = Settings(upload_dir=tmp_path / 'uploads', chunk_size=80, chunk_overlap=10)
    vector_store = store()
    embedder = MagicMock()
    embedder.embed_many = AsyncMock(side_effect=lambda texts: [[1.0] for _ in texts])
    service = DocumentService(cfg, embedder, vector_store, MagicMock(), source_registry=QuarantinedSources(tmp_path / 'sources.db'))
    first = await service.ingest_document('synthetic.txt', b'synthetic data')
    second = await service.ingest_document('synthetic.txt', b'synthetic data')
    assert first.document_id != second.document_id
    legacy = cfg.upload_dir / 'doc_legacy_synthetic.txt'
    legacy.write_bytes(b'legacy')
    service.delete_document(first.document_id)
    assert not (cfg.upload_dir / (first.document_id + '.bin')).exists()
    assert (cfg.upload_dir / (second.document_id + '.bin')).read_bytes() == b'synthetic data'
    assert legacy.read_bytes() == b'legacy'
