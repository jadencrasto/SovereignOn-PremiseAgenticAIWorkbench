"""Regression for the pending/unregistered retrieval bypass documented in 2B.1."""
import pytest

from backend.auth.request_context import effective_clearance, effective_owner
from backend.rag.retriever import RetrievalBlocked, Retriever
from backend.security.provenance_foundation import QuarantinedSources
from backend.tools.document_search import DocumentSearchInput, create_document_search


class SyntheticStore:
    def __init__(self, source_id):
        self.source_id = source_id

    def count(self):
        return 1

    def query(self, **kwargs):
        return {'ids': [['synthetic-chunk']], 'documents': [['synthetic pending content']],
                'metadatas': [[{'document_id': self.source_id, 'filename': 'synthetic.txt',
                                'chunk_index': 0, 'clearance': 'viewer'}]],
                'distances': [[0.1]]}


class SyntheticEmbedder:
    async def embed(self, query):
        return [1.0]


@pytest.mark.asyncio
@pytest.mark.parametrize('registered', [True, False])
async def test_retriever_and_tool_deny_without_authoritative_source(tmp_path, registered):
    owner = effective_owner.set('synthetic-owner')
    clearance = effective_clearance.set('viewer')
    try:
        registry = QuarantinedSources(tmp_path / 'synthetic.db')
        source_id = registry.reserve_document(b'synthetic source') if registered else 'unregistered-source'
        assert not registry.retrieval_eligible(source_id)
        if registered:
            assert registry.read(source_id)['state'] == 'pending'
        retriever = Retriever(SyntheticEmbedder(), SyntheticStore(source_id))
        with pytest.raises(RetrievalBlocked):
            await retriever.retrieve('synthetic', user_clearance='viewer')
        tool = create_document_search(retriever)
        with pytest.raises(PermissionError):
            await tool(DocumentSearchInput(query='synthetic'), user_role='viewer')
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)
