import json
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.agent.approval import ApprovalManager
from backend.agent.engine import AgentEngine
from backend.agent.memory import ConversationMemory
from backend.agent.planner import AgentPlan, PlanStep
from backend.agent.task import TaskManager
from backend.agent.task_store import TaskStore
from backend.api.tasks import router
from backend.auth.dependencies import get_current_user
from backend.auth.models import User
from backend.config import Settings


def make_user(name, role='operator', clearance='L1'):
    return User(id=name, username=name, password_hash='synthetic', role=role, clearance=clearance,
                created_at='2026-01-01T00:00:00Z')


def test_task_routes_redact_and_refuse_resume_without_mutation(tmp_path):
    store = TaskStore(tmp_path / 'tasks.db')
    step = PlanStep(id='s1', description='SYNTHETIC_SECRET_DESCRIPTION', tool_name='calculator',
                    arguments={'expression':'SYNTHETIC_SECRET_ARG'}, status='awaiting_approval',
                    result='SYNTHETIC_SECRET_STEP', error='SYNTHETIC_SECRET_ERROR', requires_approval=True)
    plan = AgentPlan(task_id='t1', objective='SYNTHETIC_SECRET_OBJECTIVE', steps=[step])
    store.save_task({'task_id':'t1','session_id':'sess','user_id':'a','status':'awaiting_approval',
        'clearance':'L3','user_request':'SYNTHETIC_SECRET_REQUEST', 'result':'SYNTHETIC_SECRET_RESULT',
        'error':'SYNTHETIC_SECRET_ERROR', 'plan_json':plan.model_dump_json()})
    store.save_task({'task_id':'t2','session_id':'other','user_id':'b','status':'completed',
        'user_request':'OTHER_SECRET', 'result':'OTHER_SECRET'})
    manager = TaskManager(store)
    approvals = ApprovalManager(store)
    approval = approvals.request_approval('t1','s1','calculator',{'expression':'SYNTHETIC_SECRET_ARG'},reason='SYNTHETIC_SECRET_REASON')
    app = FastAPI()
    app.include_router(router)
    app.state.task_manager, app.state.approval_manager, app.state.engine = manager, approvals, SimpleNamespace()
    identity = {'user':make_user('a')}
    app.dependency_overrides[get_current_user] = lambda: identity['user']
    with TestClient(app) as client:
        before = store.get_task('t1')
        listing = client.get('/api/tasks')
        assert listing.status_code == 200 and listing.json()['total'] == 1
        assert 'SYNTHETIC_SECRET' not in listing.text and 'OTHER_SECRET' not in listing.text
        detail = client.get('/api/tasks/t1')
        assert detail.status_code == 200
        assert detail.json()['status'] == 'awaiting_approval' and detail.json()['plan'] is None
        assert 'SYNTHETIC_SECRET' not in detail.text
        assert client.get('/api/tasks/t1/approvals').status_code == 403
        for action in ('approve','reject'):
            response = client.post('/api/tasks/t1/approve', json={'action':action})
            assert response.status_code == 403 and 'SYNTHETIC_SECRET' not in response.text
        assert store.get_approval(approval.approval_id)['status'] == 'pending'
        assert store.get_task('t1') == before
        assert client.get('/api/tasks/t2').status_code == 404
        assert client.get('/api/tasks/monitor').status_code == 403
        identity['user'] = make_user('root','admin','L3')
        assert 'SYNTHETIC_SECRET' not in client.get('/api/tasks/t1').text
        monitored = client.get('/api/tasks/monitor')
        assert monitored.status_code == 200 and 'SYNTHETIC_SECRET' not in monitored.text


@pytest.mark.asyncio
async def test_direct_resume_blocks_before_read_or_approval(tmp_path):
    engine = AgentEngine(Settings(), MagicMock(), ConversationMemory(tmp_path/'memory.json'))
    engine._task_manager = MagicMock()
    engine._approval_manager = MagicMock()
    events = [event async for event in engine.resume_agent_task('synthetic', 'approval', True, user_role='admin', user_id='a')]
    assert events == [{'type':'error','content':'Task continuation unavailable: source authorization cannot be verified.'}]
    engine._task_manager.get_task.assert_not_called()
    engine._approval_manager.approve.assert_not_called()

@pytest.mark.asyncio
async def test_chat_approval_intercept_does_not_consume_or_report_done(tmp_path):
    from starlette.requests import Request
    from backend.api.chat import chat
    from backend.schemas.chat import ChatRequest
    engine = AgentEngine(Settings(), MagicMock(), ConversationMemory(tmp_path/'memory.json'))
    engine._task_manager = MagicMock()
    engine._task_manager.list_tasks.return_value = [SimpleNamespace(session_id='s', user_id='a', task_id='t1')]
    engine._approval_manager = MagicMock()
    engine._approval_manager.get_pending_for_task.return_value = SimpleNamespace(approval_id='ap1')
    router = MagicMock()
    router.resolve_model.return_value = ('ollama','synthetic')
    request = Request({'type':'http','method':'POST','path':'/api/chat','headers':[]})
    response = await chat(ChatRequest(session_id='s', message='approve', stream=True), request,
                          engine=engine, model_router=router, current_user=make_user('a'))
    chunks = [part async for part in response.body_iterator]
    text = ''.join(part.decode() if isinstance(part, bytes) else part for part in chunks)
    assert 'source authorization cannot be verified' in text and '"type": "done"' not in text
    engine._approval_manager.approve.assert_not_called()


def test_artifact_routes_withhold_unverified_task_outputs_for_owner_and_admin(tmp_path):
    from backend.api.artifacts import router as artifacts_router
    root = tmp_path / 'sandbox'
    root.mkdir()
    (root / 'synthetic.txt').write_text('SYNTHETIC_SECRET_RESULT', encoding='utf-8')
    store = TaskStore(tmp_path / 'tasks.db')
    store.save_artifact('synthetic.txt', 'synthetic', user_id='a')
    app = FastAPI()
    app.include_router(artifacts_router)
    app.state.task_store = store
    app.state.settings = SimpleNamespace(sandbox_dir=root)
    identity = {'user':make_user('a')}
    app.dependency_overrides[get_current_user] = lambda: identity['user']
    with TestClient(app) as client:
        for actor in (make_user('a'), make_user('b'), make_user('root','admin','L3')):
            identity['user'] = actor
            assert client.get('/api/artifacts').json()['artifacts'] == []
            assert client.get('/api/artifacts/synthetic.txt').status_code == 404
            assert client.get('/api/artifacts/synthetic.txt/preview').status_code == 404


@pytest.mark.asyncio
async def test_artifact_verifier_dispatch_cannot_read_unverified_content():
    from unittest.mock import AsyncMock
    from backend.tools.calculator import CalculatorInput
    from backend.tools.registry import ToolDefinition, ToolRegistry
    action = AsyncMock(return_value={'content':'SYNTHETIC_SECRET'})
    registry = ToolRegistry()
    registry.register(ToolDefinition(name='artifact_verifier', description='fixture', input_schema=CalculatorInput, execute_fn=action))
    result = await registry.execute('artifact_verifier', {'expression':'1'}, user_role='admin', user_id='a')
    assert not result.success and 'SYNTHETIC_SECRET' not in str(result)
    action.assert_not_awaited()
