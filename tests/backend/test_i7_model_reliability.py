"""Synthetic model-output fixtures; no model service or normal socket event loop."""
import json
from pathlib import Path
import pytest
from backend.auth.request_context import effective_owner, request_tools_enabled
from backend.agent.task import TaskStatus
from backend.agent.engine import AgentEngine, InvalidModelToolRequest
from backend.models.base import ChatChunk, ChatResponse
from test_a21_conversation_offline import immediate
from test_a31_planning_offline import setup_workflow, collect
from test_a33_production_tools_offline import setup_conversion, tool_stream
from test_a2_provider_offline import FakeResponse, provider, request

FIXTURES=json.loads((Path(__file__).parent.parent/'fixtures'/'i7'/'model_outputs.json').read_text(encoding='utf-8-sig'))


@pytest.mark.parametrize('key',['refusal','truncated','unsupported','duplicate_field','prose_wrapped'])
def test_invalid_plans_do_not_execute(tmp_path,key):
    calls=[]
    async def execute(args): calls.append(args); return {'expression':args.expression,'result':2}
    engine=setup_workflow(tmp_path,FIXTURES[key],execute)
    owner=effective_owner.set('a'); tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        events=collect(engine)
        assert not calls
        assert any(isinstance(item,dict) and item.get('type')=='task_failed' for item in events)
        assert engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.FAILED
    finally: request_tools_enabled.reset(tools); effective_owner.reset(owner)


def test_fenced_plan_and_exact_unwrapped_plan_are_supported(tmp_path):
    for index,raw in enumerate([FIXTURES['simple_plan'],'```json\n'+FIXTURES['simple_plan']+'\n```']):
        engine=setup_workflow(tmp_path/str(index),raw)
        owner=effective_owner.set('a'); tools=request_tools_enabled.set(True)
        try:
            engine._memory.prepare_new_chat('s','a','Be helpful.')
            events=collect(engine)
            assert any(isinstance(item,dict) and item.get('type')=='task_completed' for item in events)
        finally: request_tools_enabled.reset(tools); effective_owner.reset(owner)


@pytest.mark.parametrize('raw',[
    '[{"description":"bad","tool_name":"calculator","arguments":{},"unexpected":true}]',
    '[{"description":"bad","tool_name":"calculator","arguments":{"expression":"1+1"}}] []',
    '[{"description":"bad","tool_name":"calculator","arguments":{"expression":NaN}}]',
    json.dumps([{'description':'Add','tool_name':'calculator','arguments':{'expression':str(i)+'+1'}} for i in range(6)]),
])
def test_extra_ambiguous_or_oversized_plans_fail_before_dispatch(tmp_path,raw):
    calls=[]
    async def execute(args): calls.append(args); return {'expression':args.expression,'result':2}
    engine=setup_workflow(tmp_path,raw,execute)
    owner=effective_owner.set('a'); tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        events=collect(engine)
        assert not calls and any(isinstance(item,dict) and item.get('type')=='task_failed' for item in events)
    finally: request_tools_enabled.reset(tools); effective_owner.reset(owner)


def test_duplicate_tagged_tool_calls_never_execute(tmp_path):
    engine=setup_conversion(tmp_path,[]); engine._max_tool_iterations=3; calls=[]
    async def no_execution(args): calls.append(args); return {'expression':args.expression,'result':2}
    engine._tool_registry.get('calculator').execute_fn=no_execution
    class Provider:
        provider_name='fake'
        async def chat_stream(self,request): yield ChatChunk(FIXTURES['duplicate_tool_calls'],True)
    engine._router.get_provider_for_model=lambda _: (Provider(),'fake')
    owner=effective_owner.set('a'); tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        with pytest.raises(InvalidModelToolRequest):
            tool_stream(engine,'Calculate an answer')
        assert not calls
    finally: request_tools_enabled.reset(tools); effective_owner.reset(owner)


def test_tagged_duplicate_fields_or_extra_fields_are_rejected():
    for raw in ['<tool_call>{"name":"calculator","name":"file_write","arguments":{}}</tool_call>',
                '<tool_call>{"name":"calculator","arguments":{},"success":true}</tool_call>',
                '<tool_call>{"name":"calculator","arguments":{},}</tool_call>']:
        assert AgentEngine._parse_tool_call(raw) is None


def test_model_claim_without_receipt_is_not_treated_as_tool_execution(tmp_path):
    engine=setup_conversion(tmp_path,[]); engine._max_tool_iterations=3
    class Provider:
        provider_name='fake'
        async def chat_stream(self,request): yield ChatChunk(FIXTURES['fabricated_success'],True)
    engine._router.get_provider_for_model=lambda _: (Provider(),'fake')
    owner=effective_owner.set('a'); tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        events=tool_stream(engine,'Calculate the result')
        assert any(isinstance(e,str) and 'No tool execution was recorded' in e for e in events)
        assert not any(isinstance(e,dict) and e.get('type') in ('tool_start','tool_result','task_completed') for e in events)
    finally: request_tools_enabled.reset(tools); effective_owner.reset(owner)


def test_incomplete_reasoning_stream_cannot_complete_task(tmp_path):
    engine=setup_workflow(tmp_path,'[{"description":"Explain","tool_name":null,"arguments":{}}]')
    closed=[]
    class Provider:
        provider_name='fake'
        async def chat(self,request):
            return ChatResponse(content='[{"description":"Explain","tool_name":null,"arguments":{}}]',model='fake',provider='fake')
        async def chat_stream(self,request):
            try: yield ChatChunk('claimed completed',False)
            finally: closed.append(True)
    engine._router.get_provider_for_model=lambda _: (Provider(),'fake')
    owner=effective_owner.set('a'); tools=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        with pytest.raises(RuntimeError,match='interrupted'): collect(engine,'Compare two concepts and summarize.')
        assert closed==[True]
        assert engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.FAILED_INTERRUPTED
    finally: request_tools_enabled.reset(tools); effective_owner.reset(owner)


def test_provider_stops_after_first_terminal_and_interrupted_stream_fails(monkeypatch):
    import json as jsonlib
    async def take(p): return [c async for c in p.chat_stream(request())]
    chunks=immediate(take(provider(monkeypatch,FakeResponse(lines=[jsonlib.dumps(x) for x in FIXTURES['duplicate_terminal']]))))
    assert len(chunks)==1 and chunks[0].delta=='done' and chunks[0].done
    with pytest.raises(RuntimeError,match='interrupted'):
        immediate(take(provider(monkeypatch,FakeResponse(lines=[jsonlib.dumps(x) for x in FIXTURES['stream_interrupted']]))))
