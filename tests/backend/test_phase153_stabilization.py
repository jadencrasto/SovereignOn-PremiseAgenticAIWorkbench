"""Synthetic acceptance guards for unresolved security boundaries."""
from unittest.mock import MagicMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.agent.engine import AgentEngine
from backend.agent.memory import ConversationMemory
from backend.agent.task import TaskManager
from backend.agent.task_store import TaskStore
from backend.api.tasks import router
from backend.auth.dependencies import get_current_user
from backend.auth.models import User
from backend.auth.request_context import effective_owner
from backend.config import Settings


@pytest.mark.xfail(strict=True, reason='Task detail lacks source-provenance clearance revalidation')
def test_downgraded_owner_cannot_read_classified_task_result(tmp_path):
    store = TaskStore(tmp_path / 'tasks.db')
    store.save_task({'task_id':'fixture', 'session_id':'session', 'user_request':'synthetic',
                     'status':'completed', 'user_id':'a', 'clearance':'L3', 'result':'SYNTHETIC_RESTRICTED'})
    app = FastAPI()
    app.state.task_manager = TaskManager(store)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: User(id='a', username='a', password_hash='synthetic',
        role='operator', clearance='L1', created_at='2026-01-01T00:00:00Z')
    with TestClient(app) as client:
        response = client.get('/api/tasks/fixture')
    assert 'SYNTHETIC_RESTRICTED' not in response.text


@pytest.mark.xfail(strict=True, reason='Safe assistant turns are currently excluded from model context')
def test_authorized_source_independent_turn_remains_in_model_context(tmp_path):
    token = effective_owner.set('a')
    try:
        memory = ConversationMemory(tmp_path / 'memory.json')
        memory.create_session(session_id='s')
        memory.add_assistant_message('s', 'previous calculation', access_provenance={
            'version':1, 'kind':'deterministic_calculation', 'sources':[]})
        memory.add_user_message('s', 'continue')
        engine = AgentEngine(Settings(), MagicMock(), memory)
        assert any(m.content == 'previous calculation' for m in engine._build_messages('s', 'continue', []))
    finally:
        effective_owner.reset(token)


@pytest.mark.xfail(strict=True, reason='Artifact registry can replace filename ownership on conflict')
def test_artifact_registration_does_not_reassign_existing_owner(tmp_path):
    store = TaskStore(tmp_path / 'tasks.db')
    store.save_artifact('fixture.txt', 'synthetic', user_id='a')
    store.save_artifact('fixture.txt', 'synthetic', user_id='b')
    assert store.get_artifact('fixture.txt')['user_id'] == 'a'
