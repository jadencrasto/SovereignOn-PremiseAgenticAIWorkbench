from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest

from backend.agent.engine import AgentEngine
from backend.rag.retriever import Retriever
from backend.rag.service import DocumentService
from backend.security.conversation_access import may_read_assistant
from backend.tools.document_search import DocumentSearchInput, create_document_search
from backend.tools.registry import ToolResult
from tests.backend.test_batch2b1_retrieval_preconditions import SyntheticEmbedder, SyntheticStore

SECRET = 'synthetic pending content'


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['pending', 'revoked', 'legacy', 'unregistered'])
async def test_retriever_denies_unverified_vector_content(source):
    retriever = Retriever(SyntheticEmbedder(), SyntheticStore(source))
    with pytest.raises(PermissionError):
        await retriever.retrieve('synthetic', user_clearance='admin')


@pytest.mark.asyncio
async def test_document_search_denies_even_forged_retriever():
    fake = MagicMock()
    fake.retrieve = AsyncMock(return_value=[SimpleNamespace(text=SECRET, score=0.1)])
    with pytest.raises(PermissionError):
        await create_document_search(fake)(DocumentSearchInput(query='synthetic'), user_role='admin')
    fake.retrieve.assert_not_awaited()


def test_document_service_list_and_detail_withhold_store_contents():
    service = DocumentService.__new__(DocumentService)
    service._store = MagicMock()
    service._store.list_documents.return_value = [{'document_id':'doc', 'filename':'private.txt'}]
    service._store.document_exists.return_value = True
    service._store.get_document_chunks.return_value = {'ids':['c'], 'documents':[SECRET], 'metadatas':[{'clearance':'viewer'}]}
    assert service.list_documents(user_clearance='admin') == []
    assert service.get_document_details('doc', user_clearance='admin') is None


def test_unverified_conversation_source_and_tool_output_are_withheld():
    message = SimpleNamespace(extra_fields={'access_provenance_v1': {'version':1, 'kind':'plain_chat',
                                     'sources':[{'document_id':'doc', 'chunk_id':'c'}]}})
    service = MagicMock()
    service.get_document_details.return_value = {'document_id':'doc', 'chunks':[{'chunk_id':'c'}]}
    assert not may_read_assistant(message, service, 'admin')
    result = ToolResult(tool='document_search', success=True, result=[{'text':SECRET, 'filename':'x'}])
    assert SECRET not in AgentEngine._format_observation('document_search', result)
    assert SECRET not in AgentEngine._format_direct_tool_answer('document_search', result)
    assert SECRET not in AgentEngine._format_step_result_content('document_search', result.result)


@pytest.mark.asyncio
async def test_agent_inner_does_not_trust_injected_service(tmp_path):
    from backend.agent.memory import ConversationMemory
    from backend.config import Settings
    service = MagicMock()
    service.retrieve = AsyncMock(return_value=[SimpleNamespace(text=SECRET)])
    engine = AgentEngine(Settings(), MagicMock(), ConversationMemory(tmp_path / 'memory.json'), doc_service=service)
    assert await engine._retrieve_context_inner('synthetic') == []
    service.retrieve.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_routes_do_not_trust_injected_service():
    from fastapi import HTTPException
    from backend.api.documents import get_document, list_documents
    from backend.auth.models import User
    user = User(id='a', username='a', password_hash='synthetic', role='operator', clearance='L1', created_at='2026-01-01T00:00:00Z')
    service = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await list_documents(service, user)
    assert exc.value.status_code == 503
    service.list_documents.assert_not_called()
    with pytest.raises(HTTPException) as exc:
        await get_document('doc', service, user)
    assert exc.value.status_code == 404
    service.get_document_details.assert_not_called()


@pytest.mark.asyncio
async def test_knowledge_graph_tool_withholds_injected_service():
    from backend.tools.knowledge_graph import create_knowledge_graph_query
    graph = MagicMock()
    result = await create_knowledge_graph_query(graph)(query='synthetic')
    assert result['found'] is False
    graph.query.assert_not_called()


@pytest.mark.asyncio
async def test_actual_pending_and_revoked_registry_rows_are_denied(tmp_path):
    from backend.auth.request_context import effective_clearance, effective_owner
    from backend.security.provenance_foundation import QuarantinedSources
    owner = effective_owner.set('a')
    clearance = effective_clearance.set('viewer')
    try:
        registry = QuarantinedSources(tmp_path / 'sources.db')
        source = registry.reserve_document(b'synthetic')
        retriever = Retriever(SyntheticEmbedder(), SyntheticStore(source))
        assert registry.read(source)['state'] == 'pending'
        with pytest.raises(PermissionError):
            await retriever.retrieve('synthetic')
        assert registry.revoke(source)
        assert registry.read(source)['state'] == 'revoked'
        with pytest.raises(PermissionError):
            await retriever.retrieve('synthetic')
    finally:
        effective_clearance.reset(clearance)
        effective_owner.reset(owner)


@pytest.mark.asyncio
async def test_live_knowledge_graph_api_denies_document_derived_results():
    from fastapi import HTTPException
    from backend.api.knowledge_graph import get_knowledge_graph
    from backend.auth.models import User
    graph = MagicMock()
    state = SimpleNamespace(settings=SimpleNamespace(auth_enabled=True, app_env='development'), graph_service=graph)
    request = SimpleNamespace(app=SimpleNamespace(state=state), headers={})
    user = User(id='a', username='a', password_hash='synthetic', role='operator', clearance='L1', created_at='2026-01-01T00:00:00Z')
    with pytest.raises(HTTPException) as exc:
        await get_knowledge_graph(request, None, user)
    assert exc.value.status_code == 503
    graph.get_full_graph.assert_not_called()
