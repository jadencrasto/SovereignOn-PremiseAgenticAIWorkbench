from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock
import json

import pytest
from fastapi import HTTPException, UploadFile
from backend.agent.task import TaskStateError
from backend.api.tasks import cancel_task
from backend.api.models import scan_local_models
from backend.api.chat import chat_multimodal, _stream_sse
from backend.agent.memory import ConversationMemory
from backend.auth.models import User
from backend.auth.request_context import effective_owner

SECRET = r'C:\private\storage\opaque.bin'
USER = User(id='a', username='a', password_hash='synthetic', role='operator', clearance='L1', created_at='2026-01-01T00:00:00Z')


def test_direct_nonartifact_error_does_not_expose_exception():
    from backend.agent.engine import AgentEngine
    from backend.tools.registry import ToolResult
    result = ToolResult(tool='file_read', success=False, error=SECRET)
    assert SECRET not in AgentEngine._format_direct_tool_answer('file_read', result)


def test_unknown_success_result_does_not_expose_internal_dictionary():
    from backend.agent.engine import AgentEngine
    from backend.tools.registry import ToolResult
    result = ToolResult(tool='unknown', success=True, result={'created_path': SECRET})
    assert 'opaque.bin' not in AgentEngine._format_direct_tool_answer('unknown', result)


def test_unknown_success_observation_does_not_expose_internal_dictionary():
    from backend.agent.engine import AgentEngine
    from backend.tools.registry import ToolResult
    result = ToolResult(tool='unknown', success=True, result={'created_path': SECRET})
    assert 'opaque.bin' not in AgentEngine._format_observation('unknown', result)


@pytest.mark.asyncio
async def test_task_cancel_error_does_not_expose_exception():
    manager = MagicMock()
    manager.get_task.return_value = SimpleNamespace(user_id='a')
    manager.cancel_task.side_effect = TaskStateError(SECRET)
    with pytest.raises(HTTPException) as exc:
        await cancel_task('task', manager, USER)
    assert exc.value.status_code == 400
    assert SECRET not in exc.value.detail


@pytest.mark.asyncio
async def test_model_scan_error_does_not_expose_exception(monkeypatch):
    import httpx
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): raise RuntimeError(SECRET)
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(model_router=MagicMock())))
    result = await scan_local_models(request, USER)
    assert result['status'] == 'offline'
    assert SECRET not in result['error']


@pytest.mark.asyncio
async def test_image_validation_does_not_expose_exception(tmp_path, monkeypatch):
    from backend.multimodal.image_processor import ImageProcessor, ImageValidationError
    def fail(*args, **kwargs): raise ImageValidationError(message=SECRET, code='malformed')
    monkeypatch.setattr(ImageProcessor, 'process', fail)
    token = effective_owner.set('a')
    try:
        memory = ConversationMemory(tmp_path / 'memory.json')
        memory.create_session(session_id='s')
        engine = SimpleNamespace(_memory=memory)
        router = SimpleNamespace(resolve_model=lambda _: ('local', 'synthetic'))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(upload_dir=tmp_path)))
        image = UploadFile(filename='test.png', file=BytesIO(b'synthetic'))
        with pytest.raises(HTTPException) as exc:
            await chat_multimodal(request, engine, router, 'hello', 's', None, True, True, image, USER)
        assert exc.value.status_code == 422
        assert SECRET not in exc.value.detail
    finally:
        effective_owner.reset(token)


@pytest.mark.asyncio
async def test_stream_error_has_stable_code():
    class Engine:
        _memory = SimpleNamespace(session_exists=lambda _: True, stream_identity=lambda _: 'identity')
        async def chat_stream(self, *args, **kwargs):
            raise RuntimeError(SECRET)
            yield ''
    events = [json.loads(item[6:]) async for item in _stream_sse(Engine(), 's', 'hello', None, 'test')]
    error = next(item for item in events if item['type'] == 'error')
    assert error['error_code'] == 'OPERATION_UNAVAILABLE'
    assert SECRET not in error['content']


@pytest.mark.asyncio
async def test_error_correlation_id_is_server_generated():
    import httpx
    from backend.main import create_app
    from backend.config import Settings
    app = create_app(Settings(app_env='development'))
    @app.get('/synthetic-correlation-test')
    async def fail(): raise RuntimeError(SECRET)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test') as client:
        response = await client.get('/synthetic-correlation-test', headers={'X-Request-ID': 'untrusted-secret'})
    assert response.status_code == 500
    assert response.json()['request_id'] != 'untrusted-secret'
