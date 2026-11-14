from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.api.chat import chat
from backend.auth.models import User
from backend.auth.request_context import effective_owner
from backend.schemas.chat import ChatRequest


def fixture(tmp_path):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
    memory.add_user_message('s', 'synthetic')
    user = User(id='a', username='a', password_hash='synthetic', role='operator',
                clearance='L1', created_at='2026-01-01T00:00:00Z')
    router = SimpleNamespace(resolve_model=lambda model: ('local', 'synthetic'))
    return registry, memory, user, router


@pytest.mark.asyncio
async def test_nonstream_runtime_error_does_not_disclose_internal_path(tmp_path):
    token = effective_owner.set('a')
    try:
        _, memory, user, router = fixture(tmp_path)
        class Engine:
            _memory = memory
            async def chat(self, *args, **kwargs):
                raise RuntimeError(r'C:\private\artifacts\opaque.bin')
        with pytest.raises(HTTPException) as exc:
            await chat(ChatRequest(session_id='s', message='hello', stream=False), None,
                       engine=Engine(), model_router=router, current_user=user)
        assert exc.value.status_code == 503
        assert 'private' not in str(exc.value.detail)
        assert 'opaque.bin' not in str(exc.value.detail)
    finally:
        effective_owner.reset(token)


@pytest.mark.asyncio
async def test_nonstream_revoked_after_generation_is_not_returned(tmp_path):
    token = effective_owner.set('a')
    try:
        registry, memory, user, router = fixture(tmp_path)
        class Engine:
            _memory = memory
            async def chat(self, *args, **kwargs):
                registry.revoke('s')
                return 'synthetic secret', []
        with pytest.raises(HTTPException) as exc:
            await chat(ChatRequest(session_id='s', message='hello', stream=False), None,
                       engine=Engine(), model_router=router, current_user=user)
        assert exc.value.status_code == 404
    finally:
        effective_owner.reset(token)


@pytest.mark.asyncio
async def test_approval_intercept_revoked_before_emission(tmp_path):
    token = effective_owner.set('a')
    try:
        registry, memory, user, router = fixture(tmp_path)
        task = SimpleNamespace(session_id='s', user_id='a', task_id='t')
        class Engine:
            _memory = memory
            _task_manager = SimpleNamespace(list_tasks=lambda **kw: [task])
            _approval_manager = SimpleNamespace(get_pending_for_task=lambda _: SimpleNamespace(approval_id='p'))
            async def resume_agent_task(self, **kwargs):
                yield 'synthetic secret'
        response = await chat(ChatRequest(session_id='s', message='approve', stream=True), None,
                              engine=Engine(), model_router=router, current_user=user)
        registry.revoke('s')
        chunks = [chunk async for chunk in response.body_iterator]
        assert all('synthetic secret' not in chunk for chunk in chunks)
    finally:
        effective_owner.reset(token)


@pytest.mark.asyncio
async def test_approval_intercept_stops_after_revocation(tmp_path):
    token = effective_owner.set('a')
    try:
        registry, memory, user, router = fixture(tmp_path)
        task = SimpleNamespace(session_id='s', user_id='a', task_id='t')
        class Engine:
            _memory = memory
            _task_manager = SimpleNamespace(list_tasks=lambda **kw: [task])
            _approval_manager = SimpleNamespace(get_pending_for_task=lambda _: SimpleNamespace(approval_id='p'))
            async def resume_agent_task(self, **kwargs):
                yield 'first'
                registry.revoke('s')
                yield 'synthetic secret'
        response = await chat(ChatRequest(session_id='s', message='approve', stream=True), None,
                              engine=Engine(), model_router=router, current_user=user)
        chunks = [chunk async for chunk in response.body_iterator]
        assert any('first' in chunk for chunk in chunks)
        assert all('synthetic secret' not in chunk for chunk in chunks)
    finally:
        effective_owner.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['recreate', 'registry_error', 'cross_owner'])
async def test_nonstream_revalidates_identity_and_registry(tmp_path, monkeypatch, case):
    token = effective_owner.set('a')
    try:
        registry, memory, user, router = fixture(tmp_path)
        if case == 'cross_owner':
            effective_owner.set('b')
            user = user.model_copy(update={'id': 'b', 'username': 'b'})
        class Engine:
            _memory = memory
            async def chat(self, *args, **kwargs):
                if case == 'recreate':
                    memory.delete_session('s')
                    memory.create_session(session_id='s')
                elif case == 'registry_error':
                    monkeypatch.setattr(registry, 'observed_revoked', lambda *_: (_ for _ in ()).throw(OSError('private path')))
                return 'synthetic secret', []
        with pytest.raises(HTTPException) as exc:
            await chat(ChatRequest(session_id='s', message='hello', stream=False), None,
                       engine=Engine(), model_router=router, current_user=user)
        assert exc.value.status_code == 404
        assert 'private path' not in str(exc.value.detail)
    finally:
        effective_owner.reset(token)
