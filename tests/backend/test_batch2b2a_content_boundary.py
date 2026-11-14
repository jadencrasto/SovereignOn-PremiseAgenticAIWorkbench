import pytest
from backend.agent.engine import AgentEngine
from backend.rag.store import VectorStore, _InMemoryFallbackCollection
from backend.tools.file_read import FileReadInput, create_file_read
from backend.tools.file_list import FileListInput, create_file_list
from backend.tools.registry import ToolResult

SECRET = 'synthetic confidential document'


@pytest.mark.asyncio
async def test_workspace_read_and_listing_cannot_disclose_uploads(tmp_path):
    (tmp_path / 'private.txt').write_text(SECRET, encoding='utf-8')
    reader, listing = create_file_read(tmp_path), create_file_list(tmp_path)
    with pytest.raises(PermissionError):
        await reader(FileReadInput(filename='private.txt'))
    assert await listing(FileListInput()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['../outside.txt', 'private.txt'])
async def test_workspace_traversal_and_symlink_denied(tmp_path, path):
    if path == 'private.txt':
        try:
            (tmp_path / 'private.txt').symlink_to(tmp_path.parent / 'outside.txt')
        except OSError:
            pytest.skip('Windows symlink privilege unavailable')
    with pytest.raises(PermissionError):
        await create_file_read(tmp_path)(FileReadInput(filename=path))


def test_direct_vector_read_apis_do_not_expose_content():
    store = VectorStore.__new__(VectorStore)
    store._collection = _InMemoryFallbackCollection()
    store._collection.upsert(ids=['c'], embeddings=[[1.0]], documents=[SECRET],
                             metadatas=[{'document_id':'doc', 'filename':'private.txt'}])
    with pytest.raises(PermissionError):
        store.query([1.0])
    with pytest.raises(PermissionError):
        store.get_document_chunks('doc')
    assert store.list_documents() == []


def test_file_tool_result_cannot_bypass_via_agent_formatters():
    raw = {'filename':'private.txt', 'content': SECRET}
    result = ToolResult(tool='file_read', success=True, result=raw)
    assert SECRET not in AgentEngine._format_observation('file_read', result)
    assert SECRET not in AgentEngine._format_direct_tool_answer('file_read', result)
    assert SECRET not in AgentEngine._format_step_result_content('file_read', raw)


def test_index_cleanup_is_blocked_without_verified_storage(tmp_path):
    from types import SimpleNamespace
    from backend.auth.request_context import effective_owner, effective_clearance
    from backend.security.provenance_foundation import QuarantinedSources
    from backend.rag.service import DocumentService
    owner = effective_owner.set('synthetic-owner')
    clearance = effective_clearance.set('viewer')
    registry = QuarantinedSources(tmp_path / 'sources.db')
    source_id = registry.reserve_document(b'synthetic document')
    store = VectorStore.__new__(VectorStore)
    store._collection = _InMemoryFallbackCollection()
    store._collection.upsert(ids=['c'], embeddings=[[1.0]], documents=[SECRET],
                             metadatas=[{'document_id':source_id, 'filename':'synthetic.txt'}])
    service = DocumentService.__new__(DocumentService)
    service._store = store
    service._source_registry = registry
    service._settings = SimpleNamespace(upload_dir=tmp_path)
    try:
        with pytest.raises(RuntimeError, match='cleanup unavailable'):
            service.delete_document(source_id)
        assert store.count() == 1
        assert registry.read(source_id)['state'] == 'revoked'
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)
