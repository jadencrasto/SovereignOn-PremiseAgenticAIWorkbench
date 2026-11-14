"""Scoped I4 production paths with deterministic synthetic planner responses."""
import json
from backend.auth.request_context import effective_owner, request_tools_enabled
from backend.agent.task import TaskStatus
from test_a21_conversation_offline import make_app
from test_a22_async_offline import run_offline
from test_a31_planning_offline import setup_workflow, collect
from test_a33_production_tools_offline import setup_conversion


def test_calculation_two_steps_through_production_chat_route(tmp_path):
    import httpx
    plan=json.dumps([{'description':'Add','tool_name':'calculator','arguments':{'expression':'25 + 17'}},
                     {'description':'Multiply','tool_name':'calculator','arguments':{'expression':'@step_1.result * 3'}}])
    engine=setup_workflow(tmp_path,plan); app=make_app(engine)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/api/chat',headers={'Authorization':'Bearer a'},json={'session_id':'s','message':'Calculate 25 + 17, then multiply the answer by 3.','stream':True,'tools_enabled':True})
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert response.status_code==200
            summaries=[e.get('summary') for e in events if e['type']=='tool_result' and e.get('success') is True]
            assert summaries == ['Result: 42','Result: 126']
            assert sum(e['type']=='task_completed' for e in events)==1 and events[-1]['type']=='done'
    run_offline(exercise())
    assert engine._task_manager.list_tasks(user_id='a')[0].status == TaskStatus.COMPLETED


def test_production_planner_selects_conversion(tmp_path):
    plan=[{'description':'Convert','tool_name':'unit_conversion','arguments':{'value':5,'from_unit':'km','to_unit':'m'}}]
    engine=setup_conversion(tmp_path,plan)
    owner=effective_owner.set('a'); enabled=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        events=collect(engine,'Convert 5 kilometres to metres.')
        assert any(isinstance(e,dict) and e.get('type')=='tool_result' and e.get('summary')=='Result: 5000.0 m' for e in events)
        assert engine._task_manager.list_tasks(user_id='a')[0].status == TaskStatus.COMPLETED
    finally: request_tools_enabled.reset(enabled); effective_owner.reset(owner)


def test_unverified_calculator_output_cannot_feed_next_step(tmp_path):
    plan=json.dumps([{'description':'Add','tool_name':'calculator','arguments':{'expression':'25 + 17'}},
                     {'description':'Multiply','tool_name':'calculator','arguments':{'expression':'@step_1.result * 3'}}])
    engine=setup_workflow(tmp_path,plan)
    calculator=engine._tool_registry.get('calculator')
    calculator.result_verifier=None
    calls=[]; original=calculator.execute_fn
    async def observed(args):
        calls.append(args.expression)
        return await original(args)
    calculator.execute_fn=observed
    owner=effective_owner.set('a'); enabled=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        events=collect(engine)
        assert calls==['25 + 17']
        assert any(isinstance(e,dict) and e.get('type')=='task_failed' for e in events)
        assert not any(isinstance(e,dict) and e.get('type')=='task_completed' for e in events)
        assert engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.FAILED
    finally: request_tools_enabled.reset(enabled); effective_owner.reset(owner)
