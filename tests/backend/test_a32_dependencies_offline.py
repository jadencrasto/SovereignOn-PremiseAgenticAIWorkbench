"""Dependency bindings through actual planner, task manager and registry."""
import json
import pytest
from pydantic import BaseModel
from backend.tools.registry import ToolDefinition
from backend.auth.request_context import effective_owner, request_tools_enabled
from backend.agent.task import TaskStatus
from test_a31_planning_offline import setup_workflow, collect

class MultiplyInput(BaseModel):
    left: int
    right: int
class MultiplyOutput(BaseModel):
    result: int

def bind(step, field='result'): return {'step_id':step,'field':field}
def step(expr): return {'description':'Calculate','tool_name':'calculator','arguments':{'expression':expr}}
def multiply(left='step_1',right='step_2'):
    return {'description':'Multiply','tool_name':'synthetic_multiply','arguments':{},'bindings':{'left':bind(left),'right':bind(right)}}

def setup(tmp_path, steps, output=lambda a:a.left*a.right, verified=True, enabled=True):
    engine=setup_workflow(tmp_path,json.dumps(steps)); seen=[]
    async def execute(args): seen.append((args.left,args.right));return {'result':output(args)}
    engine._tool_registry.register(ToolDefinition(name='synthetic_multiply',description='Multiply verified values',input_schema=MultiplyInput,execute_fn=execute,enabled=enabled,result_schema=MultiplyOutput if verified else None,result_verifier=(lambda a,r:r['result']==a['left']*a['right']) if verified else None))
    return engine, seen

def run(engine):
    owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        events=collect(engine,'Calculate the two values, then multiply them.')
        task=engine._task_manager.list_tasks(user_id='a')[0]
        return events,task
    finally:request_tools_enabled.reset(tools);effective_owner.reset(owner)

def test_two_verified_independent_sources_feed_read_only_tool(tmp_path):
    engine,seen=setup(tmp_path,[step('12+8'),step('15-5'),multiply()])
    events,task=run(engine)
    assert seen==[(20,10)] and task.status==TaskStatus.COMPLETED
    assert [s.status for s in task.plan.steps]==['completed']*3
    assert any(isinstance(e,str) and '200' in e for e in events)
    assert task.plan.steps[2].bindings['left'].step_id=='step_1'

@pytest.mark.parametrize('steps',[
    [step('1+1'),multiply('missing','step_1')],
    [multiply('step_2','step_2'),step('1+1')],
    [multiply('step_2','step_2'),multiply('step_1','step_1')],
    [step('1+1'),{'description':'bad','tool_name':'synthetic_multiply','arguments':{'left':3},'bindings':{'left':bind('step_1'),'right':bind('step_1')}}],
    [step('1+1'),{'description':'bad','tool_name':'synthetic_multiply','arguments':{},'bindings':{'left':bind('step_1','unknown'),'right':bind('step_1')}}],
])
def test_invalid_bindings_rejected_before_execution(tmp_path,steps):
    engine,seen=setup(tmp_path,steps);events,task=run(engine)
    assert seen==[] and task.status==TaskStatus.FAILED
    assert any(isinstance(e,dict) and e.get('type')=='task_failed' for e in events)

def test_invalid_output_fails_without_downstream_execution(tmp_path):
    steps=[step('1+1'),step('3+1'),multiply(),{'description':'again','tool_name':'synthetic_multiply','arguments':{},'bindings':{'left':bind('step_3'),'right':bind('step_1')}}]
    engine,seen=setup(tmp_path,steps,output=lambda a:999)
    events,task=run(engine)
    assert seen==[(2,4)] and task.status==TaskStatus.FAILED
    assert not any(isinstance(e,dict) and e.get('type')=='task_completed' for e in events)

def test_unverified_source_is_rejected(tmp_path):
    steps=[step('1+1'),step('3+1'),multiply(),{'description':'again','tool_name':'synthetic_multiply','arguments':{},'bindings':{'left':bind('step_3'),'right':bind('step_1')}}]
    engine,seen=setup(tmp_path,steps,verified=False)
    events,task=run(engine)
    assert seen==[] and task.status==TaskStatus.FAILED

def test_disabled_target_rejected(tmp_path):
    engine,seen=setup(tmp_path,[step('1+1'),step('3+1'),multiply()],enabled=False)
    _,task=run(engine)
    assert seen==[] and task.status==TaskStatus.FAILED


def test_chained_validated_read_only_outputs(tmp_path):
    steps=[step('12+8'),step('15-5'),multiply(),{'description':'Multiply again','tool_name':'synthetic_multiply','arguments':{},'bindings':{'left':bind('step_3'),'right':bind('step_1')}}]
    engine,seen=setup(tmp_path,steps);events,task=run(engine)
    assert seen==[(20,10),(200,20)] and task.status==TaskStatus.COMPLETED
    assert any(isinstance(e,str) and '4000' in e for e in events)


def test_wrong_type_is_rejected_after_substitution(tmp_path):
    class StringInput(BaseModel): text: str
    class StringOutput(BaseModel): text: str
    async def source(args): return {'text':'not a number'}
    engine,seen=setup(tmp_path,[{'description':'get','tool_name':'synthetic_source','arguments':{'text':'test'}},step('1+1'),multiply()])
    engine._tool_registry.register(ToolDefinition(name='synthetic_source',description='source',input_schema=StringInput,execute_fn=source,result_schema=StringOutput,result_verifier=lambda a,r:r['text']=='not a number'))
    # The target expects integer; source output passes its own contract but not the target's.
    plan=[{'description':'get','tool_name':'synthetic_source','arguments':{'text':'test'}},step('1+1'),multiply()]
    plan[2]['bindings']['left'] = bind('step_1', 'text')
    from backend.models.base import ChatResponse
    class Provider:
        provider_name='fake'
        async def chat(self,request):return ChatResponse(content=json.dumps(plan),model='fake',provider='fake')
    engine._router.get_provider_for_model=lambda _: (Provider(),'fake')
    events,task=run(engine)
    assert seen==[] and task.status==TaskStatus.FAILED
    assert any(isinstance(e,str) and 'unavailable or invalid' in e for e in events)


def test_interrupted_side_effect_is_uncertain_and_never_retried(tmp_path):
    import asyncio
    from test_a22_async_offline import run_offline
    from backend.agent.task import TaskStatus
    from backend.auth.request_context import effective_owner,request_tools_enabled,dispatch_identity_resolver
    from types import SimpleNamespace
    class Input(BaseModel): value: int
    calls=[];cleaned=[]
    async def side_effect(args):
        calls.append(args.value)
        try: await asyncio.sleep(1)
        finally:cleaned.append(True)
    plan=[{'description':'synthetic action','tool_name':'synthetic_write','arguments':{'value':1}}]
    engine=setup_workflow(tmp_path,json.dumps(plan))
    engine._tool_registry.register(ToolDefinition(name='synthetic_write',description='test',input_schema=Input,execute_fn=side_effect,read_only=False))
    owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    identity=dispatch_identity_resolver.set(lambda: SimpleNamespace(id='a', role='admin', clearance='L3', is_active=True))
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        async def exercise():
            async def consume():
                async for _ in engine.run_agent_task('s','Do the synthetic action',user_role='admin',user_id='a'): pass
            task=asyncio.create_task(consume());await asyncio.sleep(.03);task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        run_offline(exercise())
        task=engine._task_manager.list_tasks(user_id='a')[0]
        assert calls==[1] and cleaned==[True] and task.status==TaskStatus.FAILED_INTERRUPTED
        assert 'outcomes may require review' in task.error
    finally:dispatch_identity_resolver.reset(identity);request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_route_multiple_dependencies_emit_grounded_final_result(tmp_path):
    import httpx
    from test_a21_conversation_offline import make_app
    from test_a22_async_offline import run_offline
    engine, seen=setup(tmp_path,[step('12+8'),step('15-5'),multiply()]);app=make_app(engine)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/api/chat',headers={'Authorization':'Bearer a'},json={'session_id':'s','message':'Calculate 12 + 8 and 15 - 5, then multiply the two results.','stream':True,'tools_enabled':True})
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert response.status_code==200 and seen==[(20,10)]
            assert any(e.get('type')=='task_completed' for e in events)
            assert '200' in ''.join(e.get('content','') for e in events if e.get('type')=='delta')
    run_offline(exercise())


def test_bound_side_effecting_target_is_not_executed(tmp_path):
    engine,seen=setup(tmp_path,[step('1+1'),step('2+2'),multiply()])
    engine._tool_registry.get('synthetic_multiply').read_only=False
    events,task=run(engine)
    assert seen==[] and task.status==TaskStatus.FAILED


def test_multiple_prerequisites_with_existing_calculator_syntax(tmp_path):
    steps=[step('12+8'),step('15-5'),step('@step_1.result * @step_2.result')]
    engine,seen=setup(tmp_path,steps);events,task=run(engine)
    assert seen==[] and task.status==TaskStatus.COMPLETED
    assert any(isinstance(e,str) and '200' in e for e in events)
