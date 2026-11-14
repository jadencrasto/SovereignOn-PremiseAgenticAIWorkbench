"""Production route and stream checks using synthetic providers, no sockets."""
import json
from backend.auth.request_context import effective_owner
from backend.agent.planner import is_general_knowledge_query, is_short_conversational_followup, should_use_planning
from backend.agent.task import TaskStatus
from test_a21_conversation_offline import immediate, setup_engine, make_app
from test_a22_async_offline import run_offline
from test_a33_production_tools_offline import setup_conversion


def test_numeric_question_routes_to_planner_not_conversation():
    query='What is 2.5 times 4, then convert that many kilometres into metres?'
    assert not is_general_knowledge_query(query)
    assert should_use_planning(query)
    assert not is_short_conversational_followup('Yes, convert 5 km to metres')
    assert is_general_knowledge_query('What is unit conversion?')
    assert not should_use_planning(query, tools_enabled=False)


def test_cross_tool_request_on_production_route(tmp_path):
    import httpx
    plan=[{'description':'Calculate','tool_name':'calculator','arguments':{'expression':'2.5 * 4'}},
          {'description':'Convert','tool_name':'unit_conversion','arguments':{'from_unit':'km','to_unit':'m'},
           'bindings':{'value':{'step_id':'step_1','field':'result'}}}]
    engine=setup_conversion(tmp_path,plan); app=make_app(engine)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/api/chat',headers={'Authorization':'Bearer a'},json={'session_id':'s','message':'What is 2.5 times 4, then convert that many kilometres into metres?','stream':True,'tools_enabled':True})
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert response.status_code==200
            summaries=[event.get('summary') for event in events if event['type']=='tool_result' and event.get('success') is True]
            assert summaries==['Result: 10.0', 'Result: 10000.0 m']
            assert sum(event['type']=='task_completed' for event in events)==1
            assert events[-1]['type']=='done'
    run_offline(exercise())
    assert engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.COMPLETED


def test_closing_image_stream_closes_inner_generator(tmp_path):
    from backend.api.chat import _stream_sse_multimodal
    engine,_=setup_engine(tmp_path); token=effective_owner.set('a'); closed=[]
    async def fake(*args,**kwargs):
        try:
            yield 'partial synthetic response'
            yield 'must not arrive'
        finally: closed.append(True)
    engine.chat_stream_with_tools_multimodal=fake
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        async def run():
            stream=_stream_sse_multimodal(engine,'s','Describe','encoded',None,'fake',False,None,user_role='admin',user_id='a')
            first=await stream.__anext__()
            assert 'partial synthetic response' in first
            await stream.aclose()
        immediate(run())
        assert closed==[True]
    finally: effective_owner.reset(token)
