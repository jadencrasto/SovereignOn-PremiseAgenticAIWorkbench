from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile

from backend.agent.engine import AgentEngine
from backend.agent.memory import ConversationMemory
from backend.api.documents import delete_document, upload_document
from backend.api.knowledge_graph import get_knowledge_graph
from backend.api.models import preload_model
from backend.auth.models import User
from backend.auth.request_context import effective_owner
from backend.config import Settings

SECRET = r'C:\private\opaque.bin'
USER = User(id='a', username='a', password_hash='synthetic', role='operator',
            clearance='L1', created_at='2026-01-01T00:00:00Z')


@pytest.mark.asyncio
async def test_missing_filename_remains_a_useful_validation_error():
    with pytest.raises(HTTPException) as exc:
        await upload_document(UploadFile(filename='', file=BytesIO(b'x')), None, USER)
    assert exc.value.status_code == 400
    assert 'filename' in exc.value.detail.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize('exception', [ValueError, RuntimeError])
async def test_upload_does_not_expose_service_exception(exception):
    class Service:
        _settings = SimpleNamespace(max_file_size_mb=1)
        async def ingest_document(self, *args, **kwargs):
            raise exception(SECRET)
    with pytest.raises(HTTPException) as exc:
        await upload_document(UploadFile(filename='sample.txt', file=BytesIO(b'x')), Service(), USER)
    assert exc.value.status_code == (400 if exception is ValueError else 503)
    assert SECRET not in str(exc.value.detail)


@pytest.mark.asyncio
async def test_delete_does_not_expose_service_exception():
    class Service:
        def delete_document(self, *args):
            raise ValueError(SECRET)
    with pytest.raises(HTTPException) as exc:
        await delete_document('synthetic', Service(), USER)
    assert exc.value.status_code == 404
    assert SECRET not in str(exc.value.detail)


@pytest.mark.asyncio
async def test_vision_resolution_error_does_not_emit_exception(tmp_path):
    token = effective_owner.set('a')
    try:
        router = MagicMock()
        router.resolve_vision_model.side_effect = RuntimeError(SECRET)
        engine = AgentEngine(Settings(), router, ConversationMemory(tmp_path / 'memory.json'))
        events = [item async for item in engine.chat_stream_with_tools_multimodal('s', 'hello', 'synthetic')]
        assert all(SECRET not in item for item in events if isinstance(item, str))
        assert any(isinstance(item, str) and 'Vision' in item for item in events)
    finally:
        effective_owner.reset(token)


@pytest.mark.asyncio
async def test_knowledge_graph_service_error_not_disclosed():
    service = MagicMock()
    service.get_full_graph.side_effect = RuntimeError(SECRET)
    state = SimpleNamespace(settings=SimpleNamespace(auth_enabled=True, app_env='development',
                            allow_legacy_kg_fallback=False), graph_service=service)
    request = SimpleNamespace(app=SimpleNamespace(state=state), headers={})
    with pytest.raises(HTTPException) as exc:
        await get_knowledge_graph(request, None, USER)
    assert exc.value.status_code == 503
    assert SECRET not in str(exc.value.detail)


@pytest.mark.asyncio
async def test_model_preload_error_not_disclosed():
    class Request:
        async def json(self):
            raise ValueError(SECRET)
    result = await preload_model(Request(), USER)
    assert result['status'] == 'warning'
    assert SECRET not in result.get('error', '')


@pytest.mark.asyncio
async def test_vision_analysis_error_does_not_emit_exception(tmp_path, monkeypatch):
    from backend.multimodal.service import MultimodalService
    async def fail(*args, **kwargs):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(MultimodalService, 'analyze_image', fail)
    token = effective_owner.set('a')
    try:
        router = MagicMock()
        router.resolve_vision_model.return_value = (MagicMock(), 'synthetic')
        engine = AgentEngine(Settings(), router, ConversationMemory(tmp_path / 'memory.json'))
        events = [item async for item in engine.chat_stream_with_tools_multimodal('s', 'hello', 'synthetic')]
        assert all(SECRET not in item for item in events if isinstance(item, str))
        assert all(SECRET not in item.get('summary', '') for item in events if isinstance(item, dict))
    finally:
        effective_owner.reset(token)


@pytest.mark.asyncio
async def test_development_unhandled_error_does_not_disclose_exception():
    import httpx
    from backend.main import create_app
    app = create_app(Settings(app_env='development'))
    @app.get('/synthetic-error-test')
    async def fail():
        raise RuntimeError(SECRET)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url='http://test') as client:
        response = await client.get('/synthetic-error-test')
    assert response.status_code == 500
    assert SECRET not in response.json()['detail']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['exception', 'validation'])
async def test_planner_errors_do_not_become_public_text(tmp_path, failure):
    from unittest.mock import AsyncMock
    token = effective_owner.set('a')
    try:
        router = MagicMock()
        router.get_provider_for_model.return_value = (MagicMock(), 'synthetic')
        engine = AgentEngine(Settings(), router, ConversationMemory(tmp_path / 'memory.json'))
        engine._task_manager = MagicMock()
        engine._task_manager.create_task.return_value = SimpleNamespace(task_id='t')
        engine._task_manager.get_task.return_value = None
        engine._planner = MagicMock()
        engine._planner.create_plan = AsyncMock(side_effect=RuntimeError(SECRET) if failure == 'exception' else None)
        engine._plan_validator = MagicMock()
        engine._plan_validator.validate.return_value = [SECRET]
        events = [event async for event in engine.run_agent_task('s', 'hello')]
        assert all(SECRET not in event for event in events if isinstance(event, str))
        assert all(SECRET not in event.get('error', '') for event in events if isinstance(event, dict))
    finally:
        effective_owner.reset(token)


def test_nonartifact_tool_error_is_not_replayed_to_model(tmp_path):
    from backend.tools.registry import ToolResult
    token = effective_owner.set('a')
    try:
        memory = ConversationMemory(tmp_path / 'memory.json')
        memory.create_session(session_id='s')
        engine = AgentEngine(Settings(), MagicMock(), memory)
        observation = engine._format_observation('file_read', ToolResult(tool='file_read', success=False, error=SECRET))
        messages = engine._build_task_reasoning_messages('s', 'hello', [
            {'tool': 'file_read', 'success': False, 'error': SECRET}], [])
        assert SECRET not in observation
        assert all(SECRET not in item.content for item in messages)
    finally:
        effective_owner.reset(token)
