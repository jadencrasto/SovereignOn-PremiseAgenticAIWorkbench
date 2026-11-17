"""Real planner, task manager, registry and engine with synthetic local data."""
import json
from unittest.mock import MagicMock
import pytest
from pydantic import BaseModel
from backend.agent.planner import AgentPlanner, should_use_planning
from backend.agent.plan_validator import PlanValidator
from backend.agent.task import TaskManager, TaskStatus
from backend.agent.task_store import TaskStore
from backend.auth.request_context import effective_owner, request_tools_enabled
from backend.models.base import ChatResponse
from backend.tools.calculator import CalculatorInput, execute_calculator
from backend.tools.registry import ToolDefinition, ToolRegistry
from test_a21_conversation_offline import immediate, setup_engine
from backend.auth.request_context import dispatch_identity_resolver
from types import SimpleNamespace


@pytest.fixture(autouse=True)
def synthetic_dispatch_identity():
    token = dispatch_identity_resolver.set(lambda: SimpleNamespace(id='a', role='admin', clearance='L3', is_active=True))
    try:
        yield
    finally:
        dispatch_identity_resolver.reset(token)


def setup_workflow(tmp_path, plan, execute=execute_calculator, role='admin'):
    engine, _ = setup_engine(tmp_path)
    registry=ToolRegistry()
    registry.register(ToolDefinition(name='calculator',description='Arithmetic',input_schema=CalculatorInput,execute_fn=execute))
    class Provider:
        provider_name='fake'
        async def chat(self, request):
            return ChatResponse(content=plan,model='fake',provider='fake')
    provider=Provider();engine._router.get_provider_for_model=lambda _: (provider,'fake')
    engine._tool_registry=registry;engine._planner=AgentPlanner(max_plan_steps=5)
    engine._plan_validator=PlanValidator(registry,max_plan_steps=5)
    engine._task_manager=TaskManager(TaskStore(tmp_path/'tasks.db'))
    engine._approval_manager=MagicMock()
    return engine


def collect(engine, text='Calculate 25 + 17, then multiply the result by 3.', role='admin'):
    async def run(): return [item async for item in engine.run_agent_task('s',text,user_role=role,user_id='a')]
    token = dispatch_identity_resolver.set(lambda: SimpleNamespace(id='a', role=role, clearance=None, is_active=True))
    try:
        return immediate(run())
    finally:
        dispatch_identity_resolver.reset(token)


def test_planned_calculation_uses_verified_prior_result(tmp_path):
    seen=[]
    async def calculate(args):
        seen.append(args.expression);return await execute_calculator(args)
    plan=json.dumps([{'description':'Add','tool_name':'calculator','arguments':{'expression':'25 + 17'}}, {'description':'Multiply','tool_name':'calculator','arguments':{'expression':'@step_1.result * 3'}}])
    engine=setup_workflow(tmp_path,plan,calculate)
    owner=effective_owner.set('a'); tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        assert should_use_planning('Calculate 25 + 17, then multiply the result by 3.')
        events=collect(engine)
        assert seen==['25 + 17','(42) * 3']
        assert any(isinstance(e,dict) and e.get('type')=='task_completed' for e in events)
        assert any('126' in e for e in events if isinstance(e,str))
        task=engine._task_manager.list_tasks(user_id='a')[0]
        assert task.status==TaskStatus.COMPLETED
        assert [s.status for s in task.plan.steps]==['completed','completed']
    finally: request_tools_enabled.reset(tools);effective_owner.reset(owner)


@pytest.mark.parametrize('plan',[
    '[{"description":"bad","tool_name":"unknown","arguments":{}}]',
    '[{"description":"bad","tool_name":"calculator","arguments":{}}]',
    '[{"description":"bad","tool_name":"calculator","arguments":{"expression":"@step_2.result + 1"}}]',
    'not json',
])
def test_invalid_plan_never_executes(tmp_path,plan):
    seen=[]
    async def calculate(args): seen.append(args);return await execute_calculator(args)
    engine=setup_workflow(tmp_path,plan,calculate);owner=effective_owner.set('a'); tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.'); events=collect(engine)
        assert not seen and any(isinstance(e,dict) and e.get('type')=='task_failed' for e in events)
        assert engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.FAILED
    finally: request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_invalid_result_stops_dependent_step(tmp_path):
    seen=[]
    async def fake(args): seen.append(args.expression);return {'expression':args.expression,'result':999}
    plan=json.dumps([{'description':'Add','tool_name':'calculator','arguments':{'expression':'25+17'}}, {'description':'Multiply','tool_name':'calculator','arguments':{'expression':'@step_1.result * 3'}}])
    engine=setup_workflow(tmp_path,plan,fake);owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.');events=collect(engine)
        assert seen==['25+17']
        assert any(isinstance(e,dict) and e.get('type')=='task_failed' for e in events)
        assert not any(isinstance(e,dict) and e.get('type')=='task_completed' for e in events)
    finally: request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_viewer_denied_before_execution(tmp_path):
    seen=[]
    async def calculate(args): seen.append(args);return await execute_calculator(args)
    plan='[{"description":"Add","tool_name":"calculator","arguments":{"expression":"25+17"}}]'
    engine=setup_workflow(tmp_path,plan,calculate);owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.'); events=collect(engine,role='viewer')
        assert seen==[] and any(isinstance(e,dict) and e.get('type')=='task_failed' for e in events)
    finally: request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_tool_error_stops_workflow_without_retry(tmp_path):
    seen=[]
    async def failed(args): seen.append(args.expression);raise ValueError('synthetic error')
    plan='[{"description":"Add","tool_name":"calculator","arguments":{"expression":"25+17"}}]'
    engine=setup_workflow(tmp_path,plan,failed);owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.');events=collect(engine)
        assert seen==['25+17'] and any(isinstance(e,dict) and e.get('type')=='task_failed' for e in events)
    finally: request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_pending_approval_pauses_and_resume_is_blocked(tmp_path):
    from types import SimpleNamespace
    class Input(BaseModel): value: str
    calls=[]
    async def mutating(args): calls.append(args.value);return {'ok':True}
    plan='[{"description":"Write","tool_name":"synthetic_write","arguments":{"value":"x"},"requires_approval":false}]'
    engine=setup_workflow(tmp_path,plan)
    engine._tool_registry.register(ToolDefinition(name='synthetic_write',description='synthetic',input_schema=Input,execute_fn=mutating,requires_approval=True,read_only=False))
    engine._approval_manager.request_approval.return_value=SimpleNamespace(approval_id='ap',risk_level='high',expires_at='2099-01-01T00:00:00Z')
    owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.');events=collect(engine,'Perform the synthetic action.')
        assert calls==[] and any(isinstance(e,dict) and e.get('type')=='approval_required' for e in events)
        assert engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.AWAITING_APPROVAL
        async def resume(): return [e async for e in engine.resume_agent_task('x','ap',True,user_role='admin',user_id='a')]
        assert immediate(resume())[0]['type']=='error' and calls==[]
    finally: request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_interrupted_generator_records_interruption(tmp_path):
    plan='[{"description":"Add","tool_name":"calculator","arguments":{"expression":"25+17"}}]'
    engine=setup_workflow(tmp_path,plan);owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        async def interrupt():
            stream=engine.run_agent_task('s','Calculate 25+17 then report it',user_role='admin',user_id='a')
            event=await stream.__anext__();assert event['type']=='task_started'
            await stream.aclose()
        immediate(interrupt())
        assert engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.FAILED_INTERRUPTED
    finally: request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_synthetic_asgi_route_reaches_planner_and_returns_verified_result(tmp_path):
    import httpx
    from test_a21_conversation_offline import make_app
    from test_a22_async_offline import run_offline
    plan=json.dumps([{'description':'Add','tool_name':'calculator','arguments':{'expression':'25+17'}},{'description':'Multiply','tool_name':'calculator','arguments':{'expression':'@step_1.result * 3'}}])
    engine=setup_workflow(tmp_path,plan);app=make_app(engine)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/api/chat',headers={'Authorization':'Bearer a'},json={'session_id':'s','message':'Calculate 25 + 17, then multiply the result by 3.','stream':True,'tools_enabled':True})
            assert response.status_code==200
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert any(e['type']=='plan_created' for e in events)
            assert any(e['type']=='tool_result' and e.get('summary')=='Result: 126' for e in events)
            assert any(e['type']=='task_completed' for e in events)
            assert events[-1]['type']=='done'
    run_offline(run())


def test_cancel_active_tool_is_not_reported_as_success(tmp_path):
    import asyncio
    from test_a22_async_offline import run_offline
    calls=[];cleaned=[]
    async def slow(args):
        calls.append(args.expression)
        try: await asyncio.sleep(1)
        finally: cleaned.append(True)
    plan='[{"description":"Add","tool_name":"calculator","arguments":{"expression":"25+17"}}]'
    engine=setup_workflow(tmp_path,plan,slow);owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        async def run():
            seen=[]
            async def consume():
                async for event in engine.run_agent_task('s','Calculate 25+17 then report',user_role='admin',user_id='a'): seen.append(event)
            task=asyncio.create_task(consume())
            await asyncio.sleep(.03)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert not any(isinstance(e,dict) and e.get('type')=='task_completed' for e in seen)
        run_offline(run())
        assert calls==['25+17'] and cleaned==[True]
        assert engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.FAILED_INTERRUPTED
    finally: request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_direct_arithmetic_does_not_fabricate_tool_event(tmp_path):
    from backend.api.chat import _stream_arithmetic_result
    engine=setup_workflow(tmp_path,'[]');owner=effective_owner.set('a')
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        async def run(): return [json.loads(line[6:]) async for line in _stream_arithmetic_result('s','What is 25 + 17?','42','fake',engine)]
        events=immediate(run())
        assert [e['type'] for e in events]==['delta','done']
        assert '42' in events[0]['content']
        answer = engine._memory.get_history('s')[-1]
        assert engine._memory.verify_assistant_provenance('s', answer)
        assert 'tool_events_v1' not in answer.extra_fields
    finally: effective_owner.reset(owner)


def test_blocked_document_search_does_not_claim_success(tmp_path):
    from backend.tools.document_search import DocumentSearchInput
    calls=[]
    async def search(args): calls.append(args.query);return []
    plan='[{"description":"Search","tool_name":"document_search","arguments":{"query":"confidential report"}}]'
    engine=setup_workflow(tmp_path,plan)
    engine._tool_registry.register(ToolDefinition(name='document_search',description='Documents',input_schema=DocumentSearchInput,execute_fn=search))
    owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.');events=collect(engine,'Find and then analyze the confidential report.')
        assert calls==[]
        assert any(isinstance(e,dict) and e.get('type')=='tool_result' and e.get('success') is False for e in events)
        assert any(isinstance(e,dict) and e.get('type')=='task_failed' for e in events)
        assert not any(isinstance(e,dict) and e.get('type')=='task_completed' for e in events)
    finally: request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_direct_question_uses_plain_chat_without_tool_dispatch(tmp_path):
    import httpx
    from unittest.mock import AsyncMock
    from backend.models.base import ChatChunk
    from test_a21_conversation_offline import make_app
    from test_a22_async_offline import run_offline
    engine=setup_workflow(tmp_path,'[]')
    class Provider:
        provider_name='fake'
        async def chat_stream(self, request): yield ChatChunk('A pump moves fluid.',True)
    engine._router.get_provider_for_model=lambda _: (Provider(),'fake')
    engine._tool_registry.execute=AsyncMock()
    app=make_app(engine)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/api/chat',headers={'Authorization':'Bearer a'},json={'session_id':'s','message':'Explain what a centrifugal pump is.','stream':True,'tools_enabled':True})
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert response.status_code==200
            assert ''.join(e['content'] for e in events if e['type']=='delta')=='A pump moves fluid.'
            assert 'tool_start' not in response.text
            engine._tool_registry.execute.assert_not_awaited()
    run_offline(run())
