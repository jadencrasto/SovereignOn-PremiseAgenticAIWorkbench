"""In-process chat route and ephemeral provenance; synthetic files and providers only."""
from types import SimpleNamespace
from copy import deepcopy
from unittest.mock import MagicMock
import httpx
import pytest
import sniffio
from fastapi import FastAPI
from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.agent.engine import AgentEngine
from backend.api.chat import router as chat_router, get_engine, get_router_obj
from backend.auth.request_context import ClearanceContextMiddleware, effective_owner
from backend.auth.models import User
from backend.models.base import ChatResponse, ChatChunk


def immediate(coro):
    token = sniffio.current_async_library_cvar.set('asyncio')
    try:
        try: coro.send(None)
        except StopIteration as done: return done.value
        raise AssertionError('unexpected asynchronous suspension')
    finally:
        coro.close(); sniffio.current_async_library_cvar.reset(token)


def setup_engine(tmp_path):
    memory = ConversationMemory(tmp_path / 'memory.json', security_registry=ConversationRegistry(tmp_path / 'registry.db'))
    captured = []
    class Provider:
        provider_name = 'fake'
        async def chat(self, request):
            captured.append([(m.role, m.content) for m in request.messages])
            return ChatResponse(content='Pumps move fluid.', model='fake', provider='fake')
        async def chat_stream(self, request):
            captured.append([(m.role, m.content) for m in request.messages])
            yield ChatChunk('Pumps move ', False)
            yield ChatChunk('fluid.', True)
    provider = Provider()
    class Router:
        def resolve_model(self, model): return ('fake', 'fake')
        def get_provider_for_model(self, model): return provider, 'fake'
    engine = AgentEngine.__new__(AgentEngine)
    engine._memory = memory; engine._router = Router(); engine._doc_service = None
    engine._tool_registry = None; engine._system_prompt = 'Be helpful.'; engine._agent_config = {}
    return engine, captured


def make_app(engine):
    # Pre-existing synthetic sessions for continuation-focused A2/A3 tests.
    token = effective_owner.set('a')
    try:
        for session_id in ('s', 'session-a'):
            engine._memory.prepare_new_chat(session_id, 'a', 'Be helpful.')
    finally:
        effective_owner.reset(token)
    app = FastAPI(); app.add_middleware(ClearanceContextMiddleware); app.include_router(chat_router)
    app.state.engine = engine; app.state.model_router = engine._router
    async def engine_dependency(): return engine
    async def router_dependency(): return engine._router
    app.dependency_overrides[get_engine] = engine_dependency
    app.dependency_overrides[get_router_obj] = router_dependency
    app.state.settings = SimpleNamespace(auth_enabled=True, app_env='development')
    app.state.session_manager = MagicMock()
    app.state.session_manager.validate_session.side_effect = lambda token: SimpleNamespace(user_id=token) if token in ('a','b') else None
    app.state.auth_store = MagicMock()
    app.state.auth_store.get_user_by_id.side_effect = lambda uid: User(id=uid, username=uid, password_hash='x', role='admin', clearance='L3', is_active=True, must_change_password=False, created_at='2026-01-01T00:00:00Z')
    return app


def test_actual_http_route_two_turns_and_ownership(tmp_path):
    engine, captured = setup_engine(tmp_path); app = make_app(engine)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            async def post(user, text):
                return await client.post('/api/chat', headers={'Authorization': 'Bearer '+user}, json={'session_id':'session-a','message':text,'stream':False,'tools_enabled':False})
            assert (await post('a', 'Explain pumps')).status_code == 200
            assert (await post('a', 'Explain that simply')).status_code == 200
            assert captured[1] == [('system','Be helpful.'), ('user','Explain pumps'), ('assistant','Pumps move fluid.'), ('user','Explain that simply')]
            history = await client.get('/api/chat/sessions/session-a', headers={'Authorization':'Bearer a'})
            assert history.status_code == 200 and history.json()['messages'][1]['content'] == 'Pumps move fluid.'
            assert (await post('b','secret?')).status_code == 404
            assert (await client.get('/api/chat/sessions/session-a', headers={'Authorization':'Bearer b'})).status_code == 404
            assert (await client.post('/api/chat', json={'message':'hello', 'stream':False})).status_code == 401
    immediate(exercise())


def test_proof_tamper_restart_and_revocation(tmp_path):
    engine, _ = setup_engine(tmp_path); memory=engine._memory
    token=effective_owner.set('a')
    try:
        memory.prepare_new_chat('s','a','Be helpful.')
        immediate(engine.chat('s','Explain pumps'))
        history=memory.get_history('s'); answer=history[-1]
        assert memory.verify_independent_plain_chat('s',answer)
        answer.content='tampered'
        assert not memory.verify_independent_plain_chat('s',answer)
        original=memory.get_history('s')[-1]
        assert memory.verify_independent_plain_chat('s', original)
        restarted=ConversationMemory(tmp_path / 'memory.json', security_registry=ConversationRegistry(tmp_path / 'registry.db'))
        assert not restarted.verify_independent_plain_chat('s', restarted.get_history('s')[-1])
        memory.delete_session('s')
        with pytest.raises(PermissionError): memory.get_history('s')
    finally: effective_owner.reset(token)


def test_stream_and_cancel_persist_partial_without_replay(tmp_path):
    engine, captured = setup_engine(tmp_path); memory=engine._memory
    token=effective_owner.set('a')
    try:
        memory.prepare_new_chat('s','a','Be helpful.')
        async def exercise():
            stream=engine.chat_stream('s','hello')
            first=await stream.__anext__()
            assert first
            await stream.aclose()
            return first
        partial = immediate(exercise())
        msg = memory.get_history('s')[-1]
        assert msg.content == partial and msg.extra_fields['response_state_v1'] == 'interrupted'
        restarted = ConversationMemory(tmp_path / 'memory.json', security_registry=ConversationRegistry(tmp_path / 'registry.db'))
        restored = restarted.get_history('s')[-1]
        assert restored.content == partial and restored.extra_fields['response_state_v1'] == 'interrupted'
        assert not restarted.verify_independent_plain_chat('s', restored)
        async def complete(): return [x async for x in engine.chat_stream('s','again')]
        chunks=immediate(complete())
        assert ''.join(x for x in chunks if isinstance(x,str)) == 'Pumps move fluid.'
        assert ('assistant', partial) not in captured[-1]
        assert memory.verify_independent_plain_chat('s', memory.get_history('s')[-1])
    finally: effective_owner.reset(token)


def test_incomplete_provenance_binds_status_identity_and_content(tmp_path):
    engine, _ = setup_engine(tmp_path)
    memory = engine._memory
    token = effective_owner.set('a')
    try:
        memory.prepare_new_chat('s', 'a', 'Be helpful.')
        identity = memory.stream_identity('s')
        original = memory.record_plain_response('s', identity, 'partial', status='interrupted',
                                                producer='fake/fake')
        assert memory.verify_independent_plain_chat('s', original)
        for change in ('status', 'remove_status', 'id', 'timestamp', 'content'):
            altered = deepcopy(original)
            if change == 'status': altered.extra_fields['response_state_v1'] = 'failed'
            elif change == 'remove_status': altered.extra_fields.pop('response_state_v1')
            elif change == 'id': altered.id = 'other'
            elif change == 'timestamp': altered.created_at = 'other'
            else: altered.content = 'changed'
            assert not memory.verify_independent_plain_chat('s', altered)
        memory.prepare_new_chat('other', 'a', 'Be helpful.')
        assert not memory.verify_independent_plain_chat('other', original)
        restarted = ConversationMemory(tmp_path / 'memory.json', security_registry=ConversationRegistry(tmp_path / 'registry.db'))
        assert not restarted.verify_independent_plain_chat('s', original)
    finally:
        effective_owner.reset(token)


def test_untrusted_and_source_dependent_history_not_laundered(tmp_path):
    engine, captured = setup_engine(tmp_path); memory = engine._memory
    token = effective_owner.set('a')
    try:
        memory.prepare_new_chat('s','a','Be helpful.')
        memory.add_user_message('s', 'first')
        memory.add_assistant_message('s', 'restricted synthetic answer', access_provenance={'version': 1, 'kind': 'plain_chat', 'sources': [{'document_id': 'revoked', 'chunk_id': 'c'}]})
        memory.add_assistant_message('s', 'forged', access_provenance={'version': 1, 'kind':'ephemeral_independent_chat','sources':[], 'mac':'bad'})
        immediate(engine.chat('s', 'Summarize the earlier answer'))
        text = [content for _, content in captured[-1]]
        assert 'restricted synthetic answer' not in text and 'forged' not in text
        # The new response depends only on the actual provider input, not withheld outputs.
        assert memory.verify_independent_plain_chat('s', memory.get_history('s')[-1])
        assert engine._independent_plain_input('s', [], [object()]) is None
    finally: effective_owner.reset(token)


def test_cancellation_closes_provider_iterator(tmp_path):
    engine, _ = setup_engine(tmp_path); memory = engine._memory
    closed = []
    class Provider:
        provider_name = 'fake'
        async def chat_stream(self, request):
            try:
                yield ChatChunk('a sufficiently long partial answer', False)
                yield ChatChunk('must not arrive', True)
            finally:
                closed.append(True)
    engine._router.get_provider_for_model = lambda _: (Provider(), 'fake')
    token = effective_owner.set('a')
    try:
        memory.prepare_new_chat('s','a','Be helpful.')
        async def cancel():
            stream = engine.chat_stream('s','hello')
            await stream.__anext__()
            await stream.aclose()
        immediate(cancel())
        assert closed == [True]
        assert memory.get_history('s')[-1].extra_fields['response_state_v1'] == 'interrupted'
    finally: effective_owner.reset(token)


def test_production_stream_route_and_disconnect(tmp_path):
    from backend.api.chat import chat
    from backend.schemas.chat import ChatRequest as APIRequest
    from backend.auth.request_context import request_tools_enabled
    tools_token = request_tools_enabled.set(None)
    engine, _ = setup_engine(tmp_path); memory = engine._memory
    user = User(id='a', username='a', password_hash='x', role='admin', clearance='L3', is_active=True, must_change_password=False, created_at='2026-01-01T00:00:00Z')
    token = effective_owner.set('a')
    try:
        memory.prepare_new_chat('stream', 'a', 'Be helpful.')
        async def exercise():
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(enforce_model_readiness=False)))
            response = await chat(APIRequest(session_id='stream',message='Explain pumps',stream=True,tools_enabled=False), request, engine=engine,model_router=engine._router,current_user=user)
            return [item async for item in response.body_iterator]
        events = immediate(exercise())
        assert any('delta' in event for event in events)
        assert sum('"type":"done"' in event for event in events) == 1
        assert memory.verify_independent_plain_chat('stream', memory.get_history('stream')[-1])
    finally:
        effective_owner.reset(token)
        request_tools_enabled.reset(tools_token)


def test_calculator_result_is_present_in_next_provider_input(tmp_path):
    engine, captured = setup_engine(tmp_path); memory = engine._memory
    token = effective_owner.set('a')
    try:
        memory.prepare_new_chat('s','a','Be helpful.')
        memory.add_user_message('s', 'What is 25 + 17?')
        memory.record_plain_response('s', memory.stream_identity('s'), '42',
                                     producer='calculator', calculation=True)
        immediate(engine.chat('s','Multiply that result by 2.'))
        assert ('assistant','42') in captured[-1]
        # A calculator proof does not establish independent plain-chat lineage.
        assert not memory.verify_independent_plain_chat('s', memory.get_history('s')[-1])
    finally: effective_owner.reset(token)
