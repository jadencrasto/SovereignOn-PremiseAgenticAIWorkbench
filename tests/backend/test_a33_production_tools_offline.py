"""Production unit conversion and alternate tool path, using fake providers."""
import json
from unittest.mock import AsyncMock
import pytest
from backend.auth.request_context import effective_owner, request_tools_enabled
from backend.agent.task import TaskStatus
from backend.models.base import ChatChunk
from backend.tools.unit_conversion import register_unit_conversion, UnitConversionInput, execute_unit_conversion
from test_a31_planning_offline import setup_workflow, collect
from test_a21_conversation_offline import immediate, make_app
from test_a22_async_offline import run_offline


def setup_conversion(tmp_path, plan):
    engine=setup_workflow(tmp_path,json.dumps(plan))
    register_unit_conversion(engine._tool_registry)
    return engine


def conversion_steps():
    return [
        {'description':'Calculate the distance','tool_name':'calculator','arguments':{'expression':'2+3'}},
        {'description':'Convert kilometres to metres','tool_name':'unit_conversion',
         'arguments':{'from_unit':'km','to_unit':'m'},'bindings':{'value':{'step_id':'step_1','field':'result'}}},
    ]


def test_actual_production_modules_and_route_emit_verified_conversion(tmp_path):
    import httpx
    engine=setup_conversion(tmp_path,conversion_steps());app=make_app(engine)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/api/chat',headers={'Authorization':'Bearer a'},json={'session_id':'s','message':'Calculate 2 + 3, then convert that distance from kilometres to metres.','stream':True,'tools_enabled':True})
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert response.status_code==200
            assert any(e.get('type')=='plan_created' for e in events)
            assert any(e.get('type')=='tool_result' and e.get('tool')=='unit_conversion' and e.get('success') and e.get('summary')=='Result: 5000.0 m' for e in events)
            assert any(e.get('type')=='task_completed' for e in events)
            assert '5000.0 m' in ''.join(e.get('content','') for e in events if e.get('type')=='delta')
    run_offline(run())


@pytest.mark.parametrize('value,source,target,expected',[(5,'km','m',5000.0),(120,'s','min',2.0),(2500,'g','kg',2.5),(-10,'cm','m',-.1)])
def test_conversion_contract(value,source,target,expected):
    result=immediate(execute_unit_conversion(UnitConversionInput(value=value,from_unit=source,to_unit=target)))
    assert result['result']==expected


def test_incompatible_and_bad_output_fail_closed(tmp_path):
    engine=setup_conversion(tmp_path,conversion_steps())
    called=[]
    async def bad(args):called.append(True);return {'value':args.value,'from_unit':args.from_unit,'to_unit':args.to_unit,'result':999.0}
    engine._tool_registry.get('unit_conversion').execute_fn=bad
    owner=effective_owner.set('a'); tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.');events=collect(engine,'Calculate 2+3 then convert the distance.')
        assert called==[True] and engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.FAILED
        assert not any(isinstance(e,dict) and e.get('type')=='task_completed' for e in events)
        with pytest.raises(ValueError): immediate(execute_unit_conversion(UnitConversionInput(value=1,from_unit='m',to_unit='kg')))
    finally:request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_disabled_conversion_and_viewer_denied(tmp_path):
    engine=setup_conversion(tmp_path,conversion_steps());owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        engine._tool_registry.disable('unit_conversion');events=collect(engine)
        assert any(isinstance(e,dict) and e.get('type')=='task_failed' for e in events)
        engine._tool_registry.enable('unit_conversion')
        result=immediate(engine._tool_registry.execute('unit_conversion',{'value':2,'from_unit':'km','to_unit':'m'},user_role='viewer'))
        assert not result.success
    finally:request_tools_enabled.reset(tools);effective_owner.reset(owner)


def tool_stream(engine,text):
    async def run():return [e async for e in engine.chat_stream_with_tools('s',text,user_role='admin',user_id='a')]
    return immediate(run())


def test_tool_loop_rejects_malformed_call_without_fabricated_result(tmp_path):
    engine=setup_conversion(tmp_path,[]);engine._max_tool_iterations=3
    class Provider:
        provider_name='fake'
        async def chat_stream(self,request): yield ChatChunk('<tool_call>{broken}</tool_call>',True)
    engine._router.get_provider_for_model=lambda _: (Provider(),'fake')
    owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.');events=tool_stream(engine,'Convert 2 km to metres')
        assert any(isinstance(e,str) and 'invalid tool request' in e for e in events)
        assert not any(isinstance(e,dict) and e.get('type')=='tool_result' for e in events)
    finally:request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_tool_loop_stops_on_failure_and_does_not_ask_model_to_fabricate(tmp_path):
    engine=setup_conversion(tmp_path,[]);engine._max_tool_iterations=3;requests=[]
    class Provider:
        provider_name='fake'
        async def chat_stream(self,request):
            requests.append(1)
            yield ChatChunk('<tool_call>{"name":"unit_conversion","arguments":{"value":1,"from_unit":"m","to_unit":"kg"}}</tool_call>',True)
    engine._router.get_provider_for_model=lambda _: (Provider(),'fake')
    owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.');events=tool_stream(engine,'Convert metres to kilograms')
        assert len(requests)==1
        assert any(isinstance(e,dict) and e.get('type')=='tool_result' and e.get('success') is False for e in events)
        assert not any(isinstance(e,dict) and e.get('type')=='tool_result' and e.get('success') is True for e in events)
    finally:request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_planned_cancellation_before_tool_dispatch(tmp_path):
    engine=setup_conversion(tmp_path,conversion_steps());calls=[]
    original=engine._tool_registry.get('calculator').execute_fn
    async def track(args):calls.append(True);return await original(args)
    engine._tool_registry.get('calculator').execute_fn=track
    owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        async def run():
            stream=engine.run_agent_task('s','Calculate 2+3 then convert',user_role='admin',user_id='a')
            async for event in stream:
                if isinstance(event,dict) and event.get('type')=='tool_start':
                    task=engine._task_manager.list_tasks(user_id='a')[0]
                    engine._task_manager.cancel_task(task.task_id)
            await stream.aclose()
        immediate(run());assert calls==[]
        assert engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.CANCELLED
    finally:request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_tracked_tool_cancellation_before_dispatch(tmp_path):
    from backend.api.chat import _stream_sse_with_tools_tracked
    engine=setup_conversion(tmp_path,[]);engine._max_tool_iterations=3;calls=[]
    original=engine._tool_registry.get('unit_conversion').execute_fn
    async def track(args):calls.append(True);return await original(args)
    engine._tool_registry.get('unit_conversion').execute_fn=track
    class Provider:
        provider_name='fake'
        async def chat_stream(self,request):yield ChatChunk('<tool_call>{"name":"unit_conversion","arguments":{"value":1,"from_unit":"km","to_unit":"m"}}</tool_call>',True)
    engine._router.get_provider_for_model=lambda _: (Provider(),'fake')
    owner=effective_owner.set('a');tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        async def run():
            stream=_stream_sse_with_tools_tracked(engine,'s','Convert one kilometre',None,'fake',user_role='admin',user_id='a')
            async for line in stream:
                event=json.loads(line[6:])
                if event.get('type')=='task_started':engine._task_manager.cancel_task(event['task_id'])
            await stream.aclose()
        immediate(run());assert calls==[]
        assert engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.CANCELLED
    finally:request_tools_enabled.reset(tools);effective_owner.reset(owner)


def test_tracked_tool_route_returns_verified_result_and_persisted_completion(tmp_path):
    import httpx
    engine=setup_conversion(tmp_path,[]);engine._max_tool_iterations=3
    class Provider:
        provider_name='fake'
        async def chat_stream(self,request):yield ChatChunk('<tool_call>{"name":"unit_conversion","arguments":{"value":2,"from_unit":"km","to_unit":"m"}}</tool_call>',True)
    engine._router.get_provider_for_model=lambda _: (Provider(),'fake');app=make_app(engine)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/api/chat',headers={'Authorization':'Bearer a'},json={'session_id':'s','message':'Convert 2 km to metres.','stream':True,'tools_enabled':True})
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert any(e['type']=='tool_result' and e.get('summary')=='Result: 2000.0 m' for e in events)
            assert any(e['type']=='task_completed' for e in events)
            assert '2000.0 m' in ''.join(e.get('content','') for e in events if e['type']=='delta')
        task=engine._task_manager.list_tasks(user_id='a')[0]
        assert task.status==TaskStatus.COMPLETED and task.plan.steps[0].status=='completed'
    run_offline(run())


def test_tracked_tool_route_failure_never_reports_completion(tmp_path):
    import httpx
    engine=setup_conversion(tmp_path,[]);engine._max_tool_iterations=3
    class Provider:
        provider_name='fake'
        async def chat_stream(self,request):yield ChatChunk('<tool_call>{"name":"unit_conversion","arguments":{"value":2,"from_unit":"kg","to_unit":"m"}}</tool_call>',True)
    engine._router.get_provider_for_model=lambda _: (Provider(),'fake');app=make_app(engine)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/api/chat',headers={'Authorization':'Bearer a'},json={'session_id':'s','message':'Convert 2 kg to metres.','stream':True,'tools_enabled':True})
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert any(e['type']=='tool_result' and e.get('success') is False for e in events)
            assert any(e['type']=='task_failed' for e in events)
            assert not any(e['type']=='task_completed' for e in events)
        task=engine._task_manager.list_tasks(user_id='a')[0]
        assert task.status==TaskStatus.FAILED and task.plan.steps[0].status=='failed'
    run_offline(run())
