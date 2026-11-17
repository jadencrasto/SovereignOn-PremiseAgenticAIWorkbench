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


def test_authorized_source_independent_turn_remains_in_model_context(tmp_path):
    token = effective_owner.set('a')
    try:
        memory = ConversationMemory(tmp_path / 'memory.json')
        memory.create_session(session_id='s')
        memory.record_plain_response('s', memory.stream_identity('s'), 'previous calculation',
                                     producer='calculator', calculation=True)
        memory.add_assistant_message('s', 'unverified historical text')
        memory.add_assistant_message('s', 'invalid calculation', access_provenance={
            'version':1, 'kind':'deterministic_calculation', 'sources':[]})
        memory.add_assistant_message('s', 'source-derived prior answer', access_provenance={
            'version':1, 'kind':'plain_chat', 'sources':[{'document_id':'d','chunk_id':'c'}]})
        memory.add_user_message('s', 'continue')
        engine = AgentEngine(Settings(), MagicMock(), memory)
        messages = engine._build_messages('s', 'continue', [])
        assert any(m.content == 'previous calculation' for m in messages)
        assert all(m.content not in ('unverified historical text', 'invalid calculation', 'source-derived prior answer') for m in messages)
    finally:
        effective_owner.reset(token)


def test_artifact_registration_does_not_reassign_existing_owner(tmp_path):
    store = TaskStore(tmp_path / 'tasks.db')
    artifact = tmp_path / 'fixture.txt'
    artifact.write_text('synthetic owner a content')
    store.save_artifact('fixture.txt', 'synthetic', user_id='a')
    with pytest.raises(ValueError):
        store.save_artifact('fixture.txt', 'synthetic', user_id='b')
    assert store.get_artifact('fixture.txt')['user_id'] == 'a'
    assert store.get_artifact('fixture.txt')['path'] == 'synthetic'
    assert artifact.read_text() == 'synthetic owner a content'
