"""Real app lifespan/routes with isolated persistence and offline boundaries.

Chroma and Ollama boundaries are replaced: these tests do not establish live
model, real vector-store, or OS-level network behavior.
"""
from unittest.mock import AsyncMock, patch
import json
import socket

from fastapi.testclient import TestClient

from backend.agent.memory import ConversationMemory
from backend.config import Settings


class OfflineVectorStore:
    def __init__(self, persist_dir):
        self.persist_dir = persist_dir

    def count(self):
        return 0

    def list_documents(self):
        return []


_original_connect = socket.socket.connect


def local_connect(sock, address):
    if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1'):
        raise AssertionError('Unexpected non-loopback socket connection')
    return _original_connect(sock, address)


def make_settings(tmp_path):
    return Settings(
        app_env='development', auth_enabled=False, allow_dev_clearance_simulation=True,
        upload_dir=tmp_path / 'uploads', log_dir=tmp_path / 'logs',
        sandbox_dir=tmp_path / 'sandbox', chroma_persist_dir=tmp_path / 'chroma',
        tasks_dir=tmp_path / 'tasks', tasks_db_path=tmp_path / 'tasks' / 'tasks.db',
        max_file_size_mb=1,
    )


def test_real_app_lifespan_sessions_and_multipart(tmp_path, monkeypatch):
    import backend.main as main
    from backend.models.base import ChatChunk
    settings = make_settings(tmp_path)
    memory_path = tmp_path / 'conversations.json'
    memory_path.write_text(json.dumps({'legacy-session': [{'role': 'user', 'content': 'Synthetic legacy message'}]}))
    monkeypatch.setattr(main, 'VectorStore', OfflineVectorStore)
    monkeypatch.setattr(main, 'ConversationMemory', lambda: ConversationMemory(storage_file=memory_path))
    monkeypatch.setattr(main.EmbeddingService, 'health_check', AsyncMock(return_value=False))
    monkeypatch.setattr('backend.models.ollama_provider.OllamaProvider.health_check', AsyncMock(return_value=False))
    async def fake_stream(self, request):
        yield ChatChunk(delta='Synthetic response', done=True)
    monkeypatch.setattr('backend.models.ollama_provider.OllamaProvider.chat_stream', fake_stream)
    # No network is expected; this Python-level guard is not an OS isolation claim.
    with patch('socket.socket.connect', local_connect):
        app = main.create_app(settings)
        with TestClient(app) as client:
            assert client.get('/api/health/live').status_code == 200
            headers = {'X-User-Role': 'admin', 'X-Requested-With': 'test'}
            assert client.get('/api/chat/legacy-export-policy', headers=headers).json() == {'allowed': True}
            assert client.get('/api/chat/legacy-export-policy', headers={**headers, 'Authorization': 'Bearer invalid'}).status_code == 401
            body = {'session_id': 'synthetic-session', 'message': '2 + 2', 'stream': True, 'tools_enabled': True}
            first = client.post('/api/chat', json=body, headers=headers)
            assert first.status_code == 200
            assert '4' in first.text
            disabled = client.post('/api/chat', json={**body, 'tools_enabled': False}, headers=headers)
            assert disabled.status_code == 200
            assert 'Synthetic response' in disabled.text
            assert 'tool_start' not in disabled.text and 'tool_result' not in disabled.text
            assert 'synthetic-session' in client.get('/api/chat/sessions', headers=headers).json()['sessions']
            assert 'legacy-session' in client.get('/api/chat/sessions', headers=headers).json()['sessions']
            assert client.post('/api/chat', json={**body, 'session_id': 'legacy-session'}, headers=headers).status_code == 200
            assert json.loads(memory_path.read_text())['legacy-session']['owner_id'] == 'user_dev_admin'
            other = {'X-User-Role': 'viewer', 'X-Requested-With': 'test'}
            assert client.post('/api/chat', json=body, headers=other).status_code == 404
            assert client.delete('/api/chat/sessions/synthetic-session', headers=other).status_code == 404
            response = client.post('/api/documents', content=b'not multipart', headers={
                **headers, 'Content-Type': 'multipart/form-data; boundary=missing',
            })
            assert response.status_code in (400, 422)
            oversized = client.post('/api/documents', files={
                'file': ('synthetic.txt', b'x' * (2 * 1024 * 1024 + 1), 'text/plain'),
            }, headers=headers)
            assert oversized.status_code == 413

        app2 = main.create_app(settings)
        with TestClient(app2) as client:
            assert set(client.get('/api/chat/sessions', headers=headers).json()['sessions']) == {'synthetic-session', 'legacy-session'}
            assert client.delete('/api/chat/sessions/synthetic-session', headers=headers).status_code == 200
            assert client.get('/api/chat/sessions', headers=headers).json()['sessions'] == ['legacy-session']
            assert json.loads(memory_path.read_text())['legacy-session']['messages'][0]['content'] == 'Synthetic legacy message'


def test_actual_auth_sessions_isolate_conversations_and_tasks(tmp_path, monkeypatch):
    import backend.main as main
    from backend.auth.models import User
    from backend.agent.planner import AgentPlan, PlanStep
    from backend.agent.task import TaskStatus
    from backend.tools.calculator import CalculatorInput, execute_calculator
    from backend.tools.registry import ToolDefinition
    settings = make_settings(tmp_path)
    settings.auth_enabled = True
    monkeypatch.setattr(main, 'VectorStore', OfflineVectorStore)
    monkeypatch.setattr(main, 'ConversationMemory', lambda: ConversationMemory(storage_file=tmp_path / 'memory.json'))
    monkeypatch.setattr(main.EmbeddingService, 'health_check', AsyncMock(return_value=False))
    monkeypatch.setattr('backend.models.ollama_provider.OllamaProvider.health_check', AsyncMock(return_value=False))
    with patch('socket.socket.connect', local_connect):
        app = main.create_app(settings)
        with TestClient(app) as client:
            for name in ('user_a', 'user_b'):
                app.state.auth_store.create_user(User(id=name, username=name, password_hash='synthetic',
                    role='operator', clearance='L2', created_at='2026-01-01T00:00:00Z'))
            token_a, _ = app.state.session_manager.create_session('user_a')
            token_b, _ = app.state.session_manager.create_session('user_b')
            headers_a = {'Authorization': f'Bearer {token_a}'}
            headers_b = {'Authorization': f'Bearer {token_b}'}
            assert client.get('/api/chat/legacy-export-policy', headers=headers_a).json() == {'allowed': False}
            body = {'session_id': 'fixture-a', 'message': '2 + 2', 'stream': True, 'tools_enabled': True}
            assert client.post('/api/chat', json=body, headers=headers_a).status_code == 200
            owned_history = client.get('/api/chat/sessions/fixture-a', headers=headers_a)
            assert owned_history.status_code == 200
            assert any(message['content'] == '2 + 2' for message in owned_history.json()['messages'])
            assert client.get('/api/chat/sessions/fixture-a', headers=headers_b).status_code == 404
            assert client.patch('/api/chat/sessions/fixture-a/title', json={'title': 'Private title'}, headers=headers_b).status_code == 404
            assert client.patch('/api/chat/sessions/fixture-a/title', json={'title': 'Private title'}, headers=headers_a).status_code == 200
            assert client.get('/api/chat/sessions/fixture-a', headers=headers_a).json()['title'] == 'Private title'
            assert client.get('/api/chat/sessions/missing', headers=headers_a).status_code == 404
            assert client.post('/api/chat', json=body, headers=headers_b).status_code == 404
            assert client.get('/api/chat/sessions', headers=headers_b).json()['sessions'] == []
            task = app.state.task_manager.create_task('fixture-a', 'synthetic', user_id='user_a', user_role='operator')
            assert client.get(f'/api/tasks/{task.task_id}', headers=headers_a).status_code == 200
            assert client.get(f'/api/tasks/{task.task_id}', headers=headers_b).status_code == 404
            tool = ToolDefinition(name='fixture_calc', description='Synthetic calculation',
                                  input_schema=CalculatorInput, execute_fn=execute_calculator,
                                  read_only=True, requires_approval=True)
            app.state.tool_registry.register(tool)
            manager = app.state.task_manager
            manager.update_status(task.task_id, TaskStatus.PLANNING)
            step = PlanStep(id='s1', description='Calculate synthetic value', tool_name='fixture_calc',
                            arguments={'expression': '2+3'}, requires_approval=True)
            manager.set_plan(task.task_id, AgentPlan(task_id=task.task_id, objective='Calculate', steps=[step]))
            manager.update_step_status(task.task_id, 's1', 'awaiting_approval')
            manager.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)
            approval = app.state.approval_manager.request_approval(task.task_id, 's1', 'fixture_calc', {'expression': '2+3'})
            assert client.post(f'/api/tasks/{task.task_id}/approve', json={'action': 'approve'}, headers=headers_b).status_code == 404
            response = client.post(f'/api/tasks/{task.task_id}/approve', json={'action': 'approve'}, headers=headers_a)
            assert response.status_code == 200
            assert 'tool_result' in response.text and '"success": true' in response.text
            assert app.state.task_store.get_approval(approval.approval_id)['status'] == 'consumed'
            assert client.post(f'/api/tasks/{task.task_id}/approve', json={'action': 'approve'}, headers=headers_a).status_code in (400, 404)
            assert client.delete('/api/chat/sessions/fixture-a', headers=headers_b).status_code == 404
            assert client.get('/api/chat/sessions/fixture-a', headers=headers_a).status_code == 200
            assert client.delete('/api/chat/sessions/fixture-a', headers=headers_a).status_code == 200
            assert client.get('/api/chat/sessions/fixture-a', headers=headers_a).status_code == 404
