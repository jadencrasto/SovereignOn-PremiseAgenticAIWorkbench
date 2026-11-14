from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.api.chat import chat, chat_multimodal
from backend.auth.models import User
from backend.auth.request_context import effective_owner
from backend.schemas.chat import ChatRequest


@pytest.mark.asyncio
async def test_new_nonstream_session_is_active_before_inference(tmp_path):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        class Engine:
            _memory = memory
            async def chat(self, session_id, *args, **kwargs):
                assert memory.session_exists(session_id)
                assert registry.rows()[0]['state'] == 'active'
                return 'synthetic', []
        user = User(id='a', username='a', password_hash='synthetic', role='operator', clearance='L1',
                    created_at='2026-01-01T00:00:00Z')
        response = await chat(ChatRequest(session_id='new', message='synthetic', stream=False), None,
                              engine=Engine(), model_router=SimpleNamespace(resolve_model=lambda _: ('local','test')),
                              current_user=user)
        assert response.message.content == 'synthetic'
    finally:
        effective_owner.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['begin', 'activate'])
async def test_registry_failure_stops_inference_and_retry(tmp_path, monkeypatch, failure):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        class Engine:
            _memory = memory
            called = False
            async def chat(self, *args, **kwargs):
                self.called = True
                return 'synthetic', []
        engine = Engine()
        user = User(id='a', username='a', password_hash='synthetic', role='operator', clearance='L1',
                    created_at='2026-01-01T00:00:00Z')
        router = SimpleNamespace(resolve_model=lambda _: ('local', 'test'))
        monkeypatch.setattr(registry, failure, lambda *args: (_ for _ in ()).throw(OSError('synthetic failure')))
        request = ChatRequest(session_id='new', message='synthetic', stream=False)
        with pytest.raises(HTTPException):
            await chat(request, None, engine=engine, model_router=router, current_user=user)
        assert not engine.called
        if failure == 'activate':
            assert registry.rows()[0]['state'] == 'pending'
            with pytest.raises(HTTPException):
                await chat(request, None, engine=engine, model_router=router, current_user=user)
            assert not engine.called
    finally:
        effective_owner.reset(token)


@pytest.mark.asyncio
async def test_new_plain_stream_binds_before_generator(tmp_path):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        class Engine:
            _memory = memory
            async def chat_stream(self, session_id, *args, **kwargs):
                assert memory.session_exists(session_id) and registry.rows()[0]['state'] == 'active'
                yield 'synthetic'
        user = User(id='a', username='a', password_hash='synthetic', role='operator', clearance='L1',
                    created_at='2026-01-01T00:00:00Z')
        response = await chat(ChatRequest(session_id='new', message='hello', stream=True, tools_enabled=False), None,
                              engine=Engine(), model_router=SimpleNamespace(resolve_model=lambda _: ('local','test')),
                              current_user=user)
        assert registry.rows()[0]['state'] == 'active'
        chunks = [chunk async for chunk in response.body_iterator]
        assert any('synthetic' in chunk for chunk in chunks)
    finally:
        effective_owner.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['tool', 'planner', 'image'])
async def test_new_other_streams_bind_before_dispatch(tmp_path, monkeypatch, mode):
    from io import BytesIO
    from starlette.datastructures import UploadFile
    import backend.agent.planner as planner
    import backend.multimodal.image_processor as image_processor
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        calls = []
        class Engine:
            _memory = memory
            _tool_registry = object()
            _task_manager = object() if mode == 'planner' else None
            _approval_manager = None
            async def chat_stream_with_tools(self, session_id, *args, **kwargs):
                calls.append(('tool', registry.rows()[0]['state']))
                yield 'synthetic'
            async def run_agent_task(self, session_id, *args, **kwargs):
                calls.append(('planner', registry.rows()[0]['state']))
                yield 'synthetic'
            async def chat_stream_with_tools_multimodal(self, session_id, *args, **kwargs):
                calls.append(('image', registry.rows()[0]['state']))
                yield 'synthetic'
        user = User(id='a', username='a', password_hash='synthetic', role='operator', clearance='L1',
                    created_at='2026-01-01T00:00:00Z')
        router = SimpleNamespace(resolve_model=lambda _: ('local', 'test'))
        monkeypatch.setattr(planner, 'is_short_conversational_followup', lambda *_: False)
        monkeypatch.setattr(planner, 'is_general_knowledge_query', lambda *_: False)
        monkeypatch.setattr(planner, 'should_use_planning', lambda **_: mode == 'planner')
        if mode == 'image':
            class FakeProcessor:
                def __init__(self, **kwargs): pass
                def process(self, **kwargs):
                    return SimpleNamespace(attachment_id='11111111-1111-4111-8111-111111111111',
                        original_filename='synthetic.png', mime_type='image/png', size_bytes=1,
                        width=1, height=1, base64_data='AA==')
            monkeypatch.setattr(image_processor, 'ImageProcessor', FakeProcessor)
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(upload_dir=tmp_path)))
            response = await chat_multimodal(request, engine=Engine(), model_router=router, message='synthetic',
                session_id='new', model=None, stream=True, tools_enabled=True,
                image=UploadFile(filename='synthetic.png', file=BytesIO(b'x')), current_user=user)
        else:
            response = await chat(ChatRequest(session_id='new', message='synthetic workflow', stream=True, tools_enabled=True),
                                  None, engine=Engine(), model_router=router, current_user=user)
        chunks = [chunk async for chunk in response.body_iterator]
        assert any('synthetic' in chunk for chunk in chunks)
        assert calls == [(mode, 'active')]
    finally:
        effective_owner.reset(token)
