import pytest
from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.api.chat import _stream_sse, read_session
from backend.auth.models import User
from fastapi import HTTPException
from backend.auth.request_context import effective_owner


@pytest.mark.asyncio
async def test_stream_stops_after_revocation_and_does_not_finish_generator(tmp_path):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        class Engine:
            _memory = memory
            finished = False
            async def chat_stream(self, session_id, *args, **kwargs):
                memory.add_user_message(session_id, 'synthetic')
                yield 'first'
                registry.revoke(session_id)
                yield 'must not be emitted'
                self.finished = True
                memory.add_assistant_message(session_id, 'must not persist')
        engine = Engine()
        chunks = [chunk async for chunk in _stream_sse(engine, 's', 'synthetic', None, 'test')]
        assert any('first' in chunk for chunk in chunks)
        assert all('must not be emitted' not in chunk for chunk in chunks)
        assert not engine.finished
    finally:
        effective_owner.reset(token)


@pytest.mark.asyncio
async def test_history_route_rechecks_after_metadata_read(tmp_path, monkeypatch):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        memory.add_user_message('s', 'synthetic secret')
        original = memory.get_title
        def revoke_after_title(session_id):
            result = original(session_id)
            registry.revoke(session_id)
            return result
        monkeypatch.setattr(memory, 'get_title', revoke_after_title)
        class Engine:
            _memory = memory
            _task_manager = None
            _approval_manager = None
            _doc_service = None
        user = User(id='a', username='a', password_hash='synthetic', role='operator',
                    clearance='L1', created_at='2026-01-01T00:00:00Z')
        with pytest.raises(HTTPException) as exc:
            await read_session('s', engine=Engine(), current_user=user)
        assert exc.value.status_code == 404
    finally:
        effective_owner.reset(token)
