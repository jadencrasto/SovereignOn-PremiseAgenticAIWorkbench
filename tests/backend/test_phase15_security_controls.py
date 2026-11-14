import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.auth.request_context import ClearanceContextMiddleware, effective_clearance, effective_owner, local_legacy_access
from backend.agent.engine import AgentEngine
from backend.agent.memory import ConversationMemory
from backend.config import Settings
from backend.tools.document_search import create_document_search, DocumentSearchInput
from backend.security.request_limits import MultipartBodyLimitMiddleware


@pytest.mark.asyncio
async def test_request_clearance_caps_agent_and_tool_retrieval():
    router = SimpleNamespace(default_model_id='test')
    engine = AgentEngine(Settings(), router, ConversationMemory(persist=False))
    engine._doc_service = MagicMock()
    engine._doc_service.has_documents.return_value = True
    engine._retrieve_context_inner = AsyncMock(return_value=[])
    engine._is_general_knowledge_query = lambda _: False
    engine._is_standalone_non_rag_task = lambda _: False
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(return_value=[])
    retriever.timeout = 1
    search = create_document_search(retriever)
    token = effective_clearance.set('viewer')
    try:
        await engine._retrieve_context('fixture', user_clearance='admin')
        engine._retrieve_context_inner.assert_awaited_once_with('fixture', user_clearance='viewer')
        await search(DocumentSearchInput(query='fixture'), user_role='admin')
        assert retriever.retrieve.await_args.kwargs['user_clearance'] == 'viewer'
    finally:
        effective_clearance.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('clearance,expected', [('viewer', {'public'}), ('admin', {'public', 'restricted'})])
async def test_synthetic_document_classifications(clearance, expected):
    from backend.rag.retriever import Retriever
    embedder = SimpleNamespace(embed=AsyncMock(return_value=[1.0]))
    store = SimpleNamespace(count=lambda: 2, query=lambda **kwargs: {
        'ids': [['p', 'r']], 'documents': [['public', 'restricted']],
        'metadatas': [[{'document_id': 'p', 'clearance': 'viewer'},
                       {'document_id': 'r', 'clearance': 'admin'}]],
        'distances': [[0.1, 0.1]],
    })
    retriever = Retriever(embedder, store)
    chunks = await retriever.retrieve('fixture', user_clearance=clearance)
    assert {chunk.text for chunk in chunks} == expected


@pytest.mark.asyncio
async def test_concurrent_request_contexts_do_not_share_clearance():
    async def app(scope, receive, send):
        effective_clearance.set(scope['test_clearance'])
        await asyncio.sleep(0)
        scope['observed'] = effective_clearance.get()
    middleware = ClearanceContextMiddleware(app)
    scopes = [{'test_clearance': 'viewer'}, {'test_clearance': 'admin'}]
    await asyncio.gather(*(middleware(scope, None, None) for scope in scopes))
    assert [scope['observed'] for scope in scopes] == ['viewer', 'admin']
    assert effective_clearance.get() is None


@pytest.mark.asyncio
async def test_local_role_simulation_ignores_client_clearance():
    from backend.auth.dependencies import get_current_user
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=Settings(
        auth_enabled=False, allow_dev_clearance_simulation=True))), cookies={}, method='GET',
        state=SimpleNamespace())
    user = await get_current_user(request, bearer=None, x_requested_with=None,
                                  x_user_role='viewer', x_clearance='L3')
    assert user.role == 'viewer'
    assert user.clearance == 'L1'


@pytest.mark.asyncio
@pytest.mark.parametrize('chunks,headers,expected', [
    ([b'ab', b'cd'], [], 200),
    ([b'ab', b'cde'], [], 413),
    ([b'ab'], [(b'content-length', b'5')], 413),
    ([b''], [], 200),
])
async def test_multipart_body_limit_before_parser(chunks, headers, expected):
    messages = [{'type': 'http.request', 'body': chunk, 'more_body': i < len(chunks)-1}
                for i, chunk in enumerate(chunks)]
    responses = []
    observed = []

    async def receive():
        return messages.pop(0) if messages else {'type': 'http.disconnect'}

    async def send(message):
        responses.append(message)

    async def app(scope, body_receive, app_send):
        while True:
            event = await body_receive()
            observed.append(event.get('body', b''))
            if not event.get('more_body'):
                break
        await app_send({'type': 'http.response.start', 'status': 200, 'headers': []})

    middleware = MultipartBodyLimitMiddleware(app, max_file_bytes=4, multipart_overhead=0)
    scope = {'type': 'http', 'method': 'POST', 'path': '/api/documents',
             'headers': [(b'content-type', b'multipart/form-data; boundary=x'), *headers]}
    await middleware(scope, receive, send)
    assert responses[0]['status'] == expected
    assert (b''.join(observed) == b''.join(chunks)) if expected == 200 else not observed


@pytest.mark.asyncio
async def test_interrupted_upload_never_reaches_parser():
    app = AsyncMock()
    receive = AsyncMock(side_effect=[{'type': 'http.request', 'body': b'a', 'more_body': True},
                                     {'type': 'http.disconnect'}])
    middleware = MultipartBodyLimitMiddleware(app, max_file_bytes=4, multipart_overhead=0)
    await middleware({'type': 'http', 'method': 'POST', 'path': '/api/documents',
                      'headers': [(b'content-type', b'multipart/form-data')]}, receive, AsyncMock())
    app.assert_not_awaited()


def test_conversation_ownership_restart_and_legacy(tmp_path):
    import json
    path = tmp_path / 'memory.json'
    path.write_text(json.dumps({'legacy': [{'role': 'user', 'content': 'kept'}]}), encoding='utf-8')
    memory = ConversationMemory(storage_file=path)
    owner_token = effective_owner.set('user_a')
    try:
        assert memory.list_sessions() == []
        with pytest.raises(PermissionError):
            memory.get_history('legacy')
        memory.add_user_message('mine', 'private')
    finally:
        effective_owner.reset(owner_token)
    owner_token = effective_owner.set('user_b')
    try:
        recovered = ConversationMemory(storage_file=path)
        assert recovered.list_sessions() == []
        for action in (lambda: recovered.get_history('mine'), lambda: recovered.add_user_message('mine', 'bad'),
                       lambda: recovered.delete_session('mine'), lambda: recovered.create_session(session_id='mine')):
            with pytest.raises(PermissionError):
                action()
    finally:
        effective_owner.reset(owner_token)
    owner_token = effective_owner.set('user_dev_admin')
    legacy_token = local_legacy_access.set(True)
    try:
        assert memory.get_history('legacy')[0].content == 'kept'
        assert ConversationMemory(storage_file=path).get_history('legacy')[0].content == 'kept'
    finally:
        local_legacy_access.reset(legacy_token)
        effective_owner.reset(owner_token)


def test_concurrent_memory_instances_preserve_writes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / 'memory.json'
    stores = [ConversationMemory(storage_file=path), ConversationMemory(storage_file=path)]
    def write(i):
        stores[i % 2].add_user_message('s', str(i))
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write, range(20)))
    assert {message.content for message in ConversationMemory(storage_file=path).get_history('s')} == set(map(str, range(20)))


def test_concurrent_processes_preserve_writes(tmp_path):
    import subprocess
    import sys
    path = tmp_path / 'memory.json'
    code = ('import sys; from backend.agent.memory import ConversationMemory; '
            'm=ConversationMemory(storage_file=sys.argv[1]); '
            '[m.add_user_message("s", sys.argv[2]+str(i)) for i in range(5)]')
    processes = [subprocess.Popen([sys.executable, '-c', code, str(path), prefix]) for prefix in ('a', 'b')]
    assert all(proc.wait(timeout=30) == 0 for proc in processes)
    assert {item.content for item in ConversationMemory(storage_file=path).get_history('s')} == {
        f'{prefix}{i}' for prefix in ('a', 'b') for i in range(5)
    }


@pytest.mark.asyncio
async def test_http_conversation_isolation(tmp_path):
    import httpx
    from fastapi import FastAPI
    from backend.api.chat import router

    memory = ConversationMemory(storage_file=tmp_path / 'memory.json')
    class Engine:
        _memory = memory
        async def chat(self, session_id, message, model_id, user_clearance='viewer'):
            self._memory.create_session(session_id=session_id)
            self._memory.add_user_message(session_id, message)
            return 'ok', []

    users = {name: SimpleNamespace(id=name, username=name, role='admin', clearance='L3', is_active=True)
             for name in ('a', 'b')}
    app = FastAPI()
    app.state.settings = Settings(auth_enabled=True)
    app.state.engine = Engine()
    app.state.model_router = SimpleNamespace(resolve_model=lambda _: ('local', 'test'))
    app.state.session_manager = SimpleNamespace(validate_session=lambda token: SimpleNamespace(user_id=token) if token in users else None)
    app.state.auth_store = SimpleNamespace(get_user_by_id=lambda user_id: users.get(user_id))
    app.add_middleware(ClearanceContextMiddleware)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers_a = {'Authorization': 'Bearer a'}
        headers_b = {'Authorization': 'Bearer b'}
        body = {'session_id': 'unpredictable-fixture', 'message': 'hello', 'stream': False}
        assert (await client.post('/api/chat', json=body, headers=headers_a)).status_code == 200
        assert (await client.post('/api/chat', json=body, headers=headers_b)).status_code == 404
        assert (await client.get('/api/chat/sessions', headers=headers_b)).json()['sessions'] == []
        assert (await client.delete('/api/chat/sessions/unpredictable-fixture', headers=headers_b)).status_code == 404
        assert (await client.get('/api/chat/sessions', headers=headers_a)).json()['sessions'] == ['unpredictable-fixture']


def test_backend_launcher_uses_validated_binding(monkeypatch):
    import backend.__main__ as launcher
    config = SimpleNamespace(backend_host='127.0.0.1', backend_port=8123)
    validator = MagicMock()
    run = MagicMock()
    monkeypatch.setattr(launcher, 'settings', config)
    monkeypatch.setattr(launcher, 'ConfigValidator', lambda cfg: validator)
    monkeypatch.setattr(launcher.uvicorn, 'run', run)
    monkeypatch.setattr('sys.argv', ['backend'])
    launcher.main()
    validator.enforce_or_exit.assert_called_once()
    run.assert_called_once_with('backend.main:app', host='127.0.0.1', port=8123, reload=False)


def test_chat_task_actions_require_matching_owner():
    from backend.api.chat import _task_belongs_to_user
    user = SimpleNamespace(id='a', username='alice', role='admin')
    assert _task_belongs_to_user(SimpleNamespace(user_id='a'), user)
    assert not _task_belongs_to_user(SimpleNamespace(user_id='b'), user)
    assert not _task_belongs_to_user(SimpleNamespace(user_id=None), user)
