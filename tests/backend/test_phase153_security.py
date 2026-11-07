from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.agent.memory import ConversationMemory
from backend.agent.task_store import TaskStore
from backend.auth.dependencies import get_current_user
from backend.auth.models import User
from backend.auth.request_context import effective_owner
from backend.api.artifacts import router as artifact_router
from backend.security.conversation_access import may_read_assistant, source_provenance


def user(name, role):
    return User(id=name, username=name, password_hash='synthetic', role=role, created_at='2026-01-01T00:00:00Z')


def test_artifact_routes_enforce_owner_task_and_admin(tmp_path):
    root = tmp_path / 'sandbox'
    root.mkdir()
    for name in ('a.txt', 'b.txt', 'legacy.txt', 'bad-task.txt'):
        (root / name).write_text('synthetic', encoding='utf-8')
    store = TaskStore(tmp_path / 'tasks.db')
    store.save_task({'task_id':'task-b', 'session_id':'b-session', 'user_request':'synthetic', 'status':'queued', 'user_id':'b'})
    store.save_artifact('a.txt', 'synthetic', user_id='a')
    store.save_artifact('b.txt', 'synthetic', user_id='b', task_id='task-b')
    store.save_artifact('bad-task.txt', 'synthetic', user_id='a', task_id='task-b')
    owner = effective_owner.set('b')
    try:
        memory = ConversationMemory(tmp_path / 'memory.json')
        memory.create_session(session_id='b-session')
    finally:
        effective_owner.reset(owner)
    app = FastAPI()
    app.state.settings = SimpleNamespace(sandbox_dir=root)
    app.state.task_store = store
    app.state.engine = SimpleNamespace(_memory=memory)
    app.include_router(artifact_router)
    identity = {'user':user('a', 'operator')}
    app.dependency_overrides[get_current_user] = lambda: identity['user']
    with TestClient(app) as client:
        response = client.get('/api/artifacts')
        assert response.status_code == 200
        assert [item['filename'] for item in response.json()['artifacts']] == ['a.txt']
        assert 'path' not in response.json()['artifacts'][0]
        assert client.get('/api/artifacts/a.txt').status_code == 200
        assert client.get('/api/artifacts/a.txt/preview').status_code == 200
        for name in ('b.txt', 'legacy.txt', 'bad-task.txt'):
            assert client.get(f'/api/artifacts/{name}').status_code == 404
            assert client.get(f'/api/artifacts/{name}/preview').status_code == 404
        assert client.get('/api/artifacts/%2e%2e%5csecret').status_code in (400, 404)
        identity['user'] = user('b', 'viewer')
        assert client.get('/api/artifacts/b.txt').status_code == 200
        assert client.get('/api/artifacts/a.txt').status_code == 404
        identity['user'] = user('root', 'admin')
        assert client.get('/api/artifacts/a.txt').status_code == 200
        assert client.get('/api/artifacts/legacy.txt').status_code == 200
        assert client.get('/api/artifacts/bad-task.txt').status_code == 404


def test_source_policy_clearance_reclassification_and_missing(tmp_path):
    token = effective_owner.set('a')
    try:
        memory = ConversationMemory(tmp_path/'memory.json')
        refs = [SimpleNamespace(document_id='d1', chunk_id='c1'), SimpleNamespace(document_id='d2', chunk_id='c2')]
        msg = memory.add_assistant_message('s', 'classified synthetic', access_provenance=source_provenance(refs))
        docs = {'d1': {'document_id':'d1','chunks':[{'chunk_id':'c1'}]}, 'd2': {'document_id':'d2','chunks':[{'chunk_id':'c2'}]}}
        service = MagicMock()
        classification = {'d1':'viewer', 'd2':'admin'}
        levels = {'viewer':1, 'operator':2, 'admin':3}
        service.get_document_details.side_effect = lambda document_id, user_clearance: (
            docs.get(document_id) if levels[user_clearance] >= levels[classification[document_id]] else None)
        assert may_read_assistant(msg, service, 'admin')
        assert not may_read_assistant(msg, service, 'viewer')
        classification['d2'] = 'viewer'
        assert may_read_assistant(msg, service, 'viewer')
        classification['d2'] = 'admin'  # reclassification after an answer was saved
        assert not may_read_assistant(msg, service, 'viewer')
        docs['d2'] = None  # deleted source: fail closed for every clearance
        assert not may_read_assistant(msg, service, 'admin')
        docs['d2'] = {'document_id':'d2','chunks':[{'chunk_id':'other'}]}
        assert not may_read_assistant(msg, service, 'admin')
        legacy = memory.add_assistant_message('s', 'unknown legacy')
        assert not may_read_assistant(legacy, service, 'admin')
        ordinary = memory.add_assistant_message('s', 'ordinary', access_provenance=source_provenance([]))
        assert may_read_assistant(ordinary, None, 'viewer')
    finally:
        effective_owner.reset(token)


def test_attachment_descriptor_is_owner_bound_and_contains_no_bytes(tmp_path):
    path = tmp_path / 'memory.json'
    token = effective_owner.set('a')
    try:
        memory = ConversationMemory(path)
        item = memory.add_user_message('s', 'describe', attachment={'attachment_id':'12345678-1234-1234-1234-123456789abc', 'filename':'image.png', 'mime_type':'image/png', 'size_bytes':10, 'bytes':'secret'})
        assert 'bytes' not in item.extra_fields['attachments_v1'][0]
        assert 'secret' not in str(item.extra_fields)
        other = effective_owner.set('b')
        try:
            with pytest.raises(PermissionError):
                ConversationMemory(path).get_history('s')
        finally:
            effective_owner.reset(other)
        with pytest.raises(ValueError):
            memory.add_user_message('s', 'bad', attachment={'attachment_id':'12345678-1234-1234-1234-123456789abc', 'filename':'../secret.png', 'mime_type':'image/png', 'size_bytes':10})
    finally:
        effective_owner.reset(token)

@pytest.mark.asyncio
async def test_plain_chat_records_provenance_and_excludes_old_assistant_context(tmp_path):
    from unittest.mock import AsyncMock
    from backend.agent.engine import AgentEngine
    from backend.config import Settings
    from backend.models.base import ChatResponse
    token = effective_owner.set('a')
    try:
        memory = ConversationMemory(tmp_path/'memory.json')
        memory.add_assistant_message('s', 'legacy confidential')
        router, provider = MagicMock(), MagicMock()
        router.get_provider_for_model.return_value = (provider, 'synthetic')
        provider.chat = AsyncMock(return_value=ChatResponse(content='grounded', model='synthetic', provider='fake'))
        engine = AgentEngine(Settings(), router, memory)
        engine._retrieve_context = AsyncMock(return_value=[SimpleNamespace(document_id='d1', chunk_id='c1', page=1, filename='fixture', score=0.2, text='synthetic source')])
        await engine.chat('s', 'question', user_clearance='admin')
        sent = provider.chat.call_args.args[0].messages
        assert all('legacy confidential' not in message.content for message in sent)
        saved = ConversationMemory(tmp_path/'memory.json').get_history('s')[-1]
        assert saved.extra_fields['access_provenance_v1']['sources'] == [{'document_id':'d1', 'chunk_id':'c1'}]
    finally:
        effective_owner.reset(token)
