"""Approval SSE boundaries using in-process routes and synthetic task state."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from backend.auth.models import User
from test_a21_conversation_offline import make_app, setup_engine
from test_a22_async_offline import run_offline


def fixture(tmp_path, role='admin'):
    engine, _ = setup_engine(tmp_path)
    engine._task_manager = MagicMock()
    engine._task_manager.list_tasks.return_value = [SimpleNamespace(session_id='s', user_id='a', task_id='t')]
    engine._approval_manager = MagicMock()
    engine._approval_manager.get_pending_for_task.return_value = SimpleNamespace(approval_id='p')
    app = make_app(engine)
    app.state.auth_store.get_user_by_id.side_effect = lambda uid: User(
        id=uid, username=uid, password_hash='x', role=role, clearance='L3',
        is_active=True, must_change_password=False, created_at='2026-01-01T00:00:00Z')
    return engine, app


def event_types(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def test_viewer_denial_is_terminal_and_does_not_consume_approval(tmp_path):
    engine, app = fixture(tmp_path, 'viewer')
    engine.resume_agent_task = MagicMock(side_effect=AssertionError('must not dispatch'))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            response = await client.post('/api/chat', json={'session_id': 's', 'message': 'approve', 'stream': True})
            assert response.status_code == 200
            events = event_types(response)
            assert [event['type'] for event in events] == ['error']
            assert 'Permission denied' in events[0]['content']
            assert (await client.post('/api/chat', headers={'Authorization': 'Bearer b'},
                json={'session_id': 's', 'message': 'approve', 'stream': True})).status_code == 404
    run_offline(run())
    engine.resume_agent_task.assert_not_called()
    engine._approval_manager.approve.assert_not_called()
    engine._approval_manager.reject.assert_not_called()
    engine._approval_manager.verify_approval_for_execution.assert_not_called()


@pytest.mark.parametrize('terminal', ['error', 'task_failed', 'task_cancelled'])
@pytest.mark.parametrize('partial', [False, True])
def test_intercepted_terminal_event_closes_generator(tmp_path, terminal, partial):
    engine, app = fixture(tmp_path)
    closed = []
    async def fake_resume(**kwargs):
        try:
            if partial:
                yield 'partial'
            yield {'type': terminal, 'task_id': 't', 'content': 'unavailable'}
            yield {'type': 'tool_result', 'tool': 'calculator', 'success': True}
            yield 'late output'
        finally:
            closed.append(True)
    engine.resume_agent_task = fake_resume
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            response = await client.post('/api/chat', json={'session_id': 's', 'message': 'approve', 'stream': True})
            assert response.status_code == 200
            events = event_types(response)
            assert [event['type'] for event in events] == (['delta'] if partial else []) + [terminal]
            assert 'late output' not in response.text
            assert closed == [True]
    run_offline(run())
    engine._approval_manager.approve.assert_not_called()


def test_real_resume_remains_blocked_before_approval_consumption(tmp_path):
    engine, app = fixture(tmp_path)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            for action in ('approve', 'reject'):
                response = await client.post('/api/chat', json={'session_id': 's', 'message': action, 'stream': True})
                assert [event['type'] for event in event_types(response)] == ['error']
    run_offline(run())
    engine._approval_manager.approve.assert_not_called()
    engine._approval_manager.reject.assert_not_called()
