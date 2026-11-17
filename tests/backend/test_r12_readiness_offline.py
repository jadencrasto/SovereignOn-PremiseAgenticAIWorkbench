import asyncio
import base64
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Response

from backend.health import routes
from backend.health.capabilities import ModelObservation
from test_a22_async_offline import run_offline


def state():
    router = SimpleNamespace(default_model_id='ollama/a', _config={'models': {
        'ollama/a': {'capabilities': ['chat']}, 'ollama/b:7b': {'capabilities': ['chat']},
        'ollama/embed': {'capabilities': ['embedding']}}},
        configured_model_id=lambda capability: None)
    s = SimpleNamespace(settings=SimpleNamespace(app_env='development', ollama_base_url='http://127.0.0.1:11434',
        ollama_default_model='a', embedding_model='embed'), startup_complete=True, model_router=router,
        planner=object(), conversation_persistence_initialized=True)
    s.model_observation = (routes._tags_url(s), time.monotonic(), ModelObservation('available', 'inventory_observed', ('a:latest', 'b:7b', 'embed:latest')))
    return s


def test_inference_identity_expiry_and_endpoint():
    s = state()
    assert routes._report(s, 'ollama/a')['selected_chat_model']['reason'] == 'inference_not_checked'
    identity = routes._identity(s, 'ollama/a:latest')
    s.inference_observation = (identity, time.monotonic(), True)
    assert routes._report(s, 'ollama/a')['capabilities']['chat']['state'] == 'available'
    assert routes._report(s, 'ollama/b:7b')['capabilities']['chat']['state'] == 'unknown'
    assert routes._report(s, 'ollama/embed')['selected_chat_model']['reason'] == 'unsupported_chat_model'
    s.settings.ollama_base_url = 'http://127.0.0.1:11435'
    assert routes._report(s, 'ollama/a')['capabilities']['chat']['state'] != 'available'
    s.settings.ollama_base_url = 'http://127.0.0.1:11434'
    s.inference_observation = (identity, time.monotonic() - routes.INFERENCE_TTL, True)
    assert routes._report(s, 'ollama/a')['selected_chat_model']['reason'] == 'inference_check_expired'
    s.inference_observation = (identity, time.monotonic(), True)
    s.model_router._config['models']['ollama/a']['capabilities'] = ['embedding']
    assert routes._report(s, 'ollama/a')['capabilities']['chat']['state'] != 'available'


def test_check_success_failure_cancellation_and_concurrency(monkeypatch):
    async def exercise():
        s = state(); request = SimpleNamespace(app=SimpleNamespace(state=s))
        async def check(name='ollama/a'):
            return await routes.check_inference(routes.InferenceCheck(model=name), request, Response(), None)
        monkeypatch.setattr(routes, 'probe_inference', AsyncMock(return_value=True))
        assert (await check())['selected_chat_model']['state'] == 'available'
        monkeypatch.setattr(routes, 'probe_inference', AsyncMock(return_value=False))
        assert (await check())['selected_chat_model']['reason'] == 'inference_check_failed'
        monkeypatch.setattr(routes, 'probe_inference', AsyncMock(return_value=True))
        assert (await check())['selected_chat_model']['reason'] == 'inference_verified'
        started = asyncio.Event()
        async def pending(*args):
            started.set(); await asyncio.sleep(10)
        monkeypatch.setattr(routes, 'probe_inference', pending)
        task = asyncio.create_task(check()); await started.wait()
        assert routes._report(s, 'ollama/a')['selected_chat_model']['reason'] == 'inference_check_in_progress'
        duplicate = Response()
        await routes.check_inference(routes.InferenceCheck(model='ollama/b:7b'), request, duplicate, None)
        assert duplicate.status_code == 409
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert s.inference_observation is None and s.inference_check_in_progress is None
    run_offline(exercise())


@pytest.mark.parametrize('header_delay, body_delay, vision, status, expected', [
    pytest.param(17.42, 0, False, 200, True, id='cold-text'),
    pytest.param(12.45, 0, True, 200, True, id='cold-vision'),
    pytest.param(1.22, 0, False, 200, True, id='warm-text'),
    pytest.param(0, 0, False, None, False, id='connection-refused'),
    pytest.param(0, 0, False, 404, False, id='missing-model'),
    pytest.param(31, 0, False, 200, False, id='missing-response-headers'),
    pytest.param(17, 14, False, 200, False, id='cumulative-deadline'),
])
def test_probe_httpx_timeouts_with_simulated_load(monkeypatch, header_delay, body_delay, vision, status, expected):
    client_type = httpx.AsyncClient
    model = 'vision:latest' if vision else 'a:latest'
    requests = []

    async def exercise():
        loop = asyncio.get_running_loop()
        elapsed = 0.0

        def advance(seconds):
            nonlocal elapsed
            assert seconds is not None
            elapsed += seconds
            return []

        # The timer-only loop advances through scheduled delays without wall-clock waits.
        monkeypatch.setattr(loop, 'time', lambda: elapsed)
        monkeypatch.setattr(loop._selector, 'select', advance)

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(body_delay)
                yield json.dumps({
                    'model': model, 'done': True, 'done_reason': 'stop',
                    'message': {'role': 'assistant', 'content': 'blue' if vision else 'OK'},
                }).encode()

        async def handle(request):
            requests.append(request)
            assert str(request.url) == 'http://127.0.0.1:11434/api/chat'
            assert request.method == 'POST'
            assert json.loads(request.content)['keep_alive'] == 0
            if status is None:
                raise httpx.ConnectError('Connection refused', request=request)
            read_timeout = request.extensions['timeout']['read']
            await asyncio.sleep(min(header_delay, read_timeout))
            if header_delay > read_timeout:
                raise httpx.ReadTimeout('No response headers', request=request)
            return httpx.Response(status, stream=Body())

        def client(**kwargs):
            assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
            return client_type(transport=httpx.MockTransport(handle), **kwargs)

        monkeypatch.setattr(routes.httpx, 'AsyncClient', client)
        monkeypatch.setattr(routes.secrets, 'choice', lambda _: 'blue')
        assert await routes.probe_inference('http://127.0.0.1:11434/api/tags', model, vision=vision) is expected
        assert elapsed == pytest.approx(min(header_delay + body_delay, routes.INFERENCE_TIMEOUT))

    run_offline(exercise())
    request, = requests
    assert request.extensions['timeout'] == httpx.Timeout(float(routes.INFERENCE_TIMEOUT)).as_dict()


@pytest.mark.parametrize('payload, expected', [
    ({'model': 'a:latest', 'done': True, 'message': {'role': 'assistant', 'content': 'OK'}}, True),
    ({'model': 'a', 'done': False, 'message': {'role': 'assistant', 'content': 'OK'}}, False),
    ({'model': 'other', 'done': True, 'message': {'role': 'assistant', 'content': 'OK'}}, False),
    ({'model': 'a', 'done': True, 'done_reason': 'length', 'message': {'role': 'assistant', 'content': 'OK'}}, False),
    ('not json', False), ('x' * (routes.MAX_INFERENCE_BYTES + 1), False),
])
def test_bounded_probe_response_and_cleanup(monkeypatch, payload, expected):
    closed = []
    class Reply:
        headers = {}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): closed.append('response')
        def raise_for_status(self): pass
        async def aiter_raw(self, chunk_size):
            yield (json.dumps(payload) if isinstance(payload, dict) else payload).encode()
    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {'trust_env': False, 'follow_redirects': False,
                              'timeout': httpx.Timeout(float(routes.INFERENCE_TIMEOUT))}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): closed.append('client')
        def stream(self, *args, **kwargs): return Reply()
    monkeypatch.setattr(routes.httpx, 'AsyncClient', Client)
    assert run_offline(routes.probe_inference('http://127.0.0.1:11434/api/tags', 'a:latest')) is expected
    assert closed == ['response', 'client']


def test_probe_timeout_and_http_failure_close_resources(monkeypatch):
    closed = []
    class Reply:
        headers = {}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): closed.append('response')
        def raise_for_status(self): pass
        async def aiter_raw(self, chunk_size):
            await asyncio.sleep(1)
            yield b''
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): closed.append('client')
        def stream(self, *args, **kwargs): return Reply()
    monkeypatch.setattr(routes.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(routes, 'INFERENCE_TIMEOUT', .02)
    assert run_offline(routes.probe_inference('http://127.0.0.1:11434/api/tags', 'a')) is False
    assert closed == ['response', 'client']
    def fail(self):
        req = httpx.Request('POST', 'http://test.invalid')
        raise httpx.HTTPStatusError('failure', request=req, response=httpx.Response(500, request=req))
    monkeypatch.setattr(Reply, 'raise_for_status', fail)
    assert run_offline(routes.probe_inference('http://127.0.0.1:11434/api/tags', 'a')) is False


@pytest.mark.parametrize('answer, model, done, expected', [
    ('blue.', 'vision:latest', True, True),
    ('OK', 'vision:latest', True, False),
    ('blue', 'other:latest', True, False),
    ('blue', 'vision:latest', False, False),
])
def test_vision_probe_sends_synthetic_image_and_checks_response(monkeypatch, answer, model, done, expected):
    from PIL import Image

    client_type = httpx.AsyncClient

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps({'model': model, 'done': done,
                              'message': {'role': 'assistant', 'content': answer}}).encode()

    def handle(request):
        assert str(request.url) == 'http://127.0.0.1:11434/api/chat'
        payload = json.loads(request.content)
        assert payload['model'] == 'vision:latest' and payload['stream'] is False
        prompt, = payload['messages']
        assert prompt['role'] == 'user' and 'blue' not in prompt['content']
        encoded, = prompt['images']
        with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as image:
            assert image.format == 'PNG' and image.getpixel((0, 0)) == (0, 0, 255)
        return httpx.Response(200, stream=Body())

    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return client_type(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(routes.secrets, 'choice', lambda _: 'blue')
    monkeypatch.setattr(routes.httpx, 'AsyncClient', client)
    assert run_offline(routes.probe_inference('http://127.0.0.1:11434/api/tags', 'vision:latest', vision=True)) is expected

def test_chat_only_lifespan_avoids_vector_initialization(tmp_path, monkeypatch, caplog):
    import backend.main as main
    from backend.auth.models import User
    from backend.auth.security import hash_password
    from backend.config import Settings
    from backend.rag.retriever import RetrievalBlocked
    cfg = Settings(_env_file=None, app_env='development', auth_enabled=True, auth_dev_override=False,
        local_chat_only=True, backend_host='127.0.0.1', ollama_base_url='http://127.0.0.1:11434',
        backend_port=8001, cors_origins='http://127.0.0.1:5174', auth_cookie_secure=False,
        auth_cookie_samesite='lax', auth_cookie_domain=None,
        upload_dir=tmp_path/'uploads', log_dir=tmp_path/'logs', sandbox_dir=tmp_path/'sandbox',
        chroma_persist_dir=tmp_path/'chroma', tasks_dir=tmp_path/'tasks',
        tasks_db_path=tmp_path/'tasks'/'tasks.db', conversation_memory_path=tmp_path/'memory.json')
    monkeypatch.setattr(main, 'VectorStore', lambda **kwargs: pytest.fail('vector store initialized'))
    monkeypatch.setattr(main, 'EmbeddingService', lambda **kwargs: pytest.fail('embedding service initialized'))
    monkeypatch.setattr(main, 'initialize_admin_user_if_empty', lambda store: None)
    async def exercise():
        app = main.create_app(cfg)
        async with main.lifespan(app):
            assert app.state.startup_complete and app.state.auth_store and app.state.engine
            assert app.state.vector_store.backend_status == 'unavailable'
            assert not cfg.chroma_persist_dir.exists()
            assert app.state.doc_service.has_documents() is False
            with pytest.raises(RuntimeError, match='Verified persistent index unavailable'):
                await app.state.doc_service.ingest_document('x.txt', b'content')
            with pytest.raises(RetrievalBlocked):
                await app.state.doc_service.retrieve('secret')
            app.state.auth_store.create_user(User(id='operator-fixture', username='r85-operator',
                password_hash=hash_password('synthetic-password'), role='operator', clearance='L2',
                created_at='2026-11-16T00:00:00+00:00'))
            model = app.state.model_router.default_model_id
            name = model.removeprefix('ollama/')
            monkeypatch.setattr(routes, 'observe_ollama', AsyncMock(return_value=ModelObservation(
                'available', 'inventory_observed', (name,))))
            monkeypatch.setattr(routes, 'probe_inference', AsyncMock(return_value=True))
            capability = '/api/health/capabilities'
            browser_headers = {'Origin': 'http://127.0.0.1:5174', 'X-Requested-With': 'XMLHttpRequest'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://127.0.0.1:5174') as browser:
                assert (await browser.get('/api/health/ready')).json()['ready'] is True
                assert (await browser.post(f'{capability}/inference-check', json={'model': model},
                                           headers=browser_headers)).status_code == 401
                login = await browser.post('/api/auth/login', headers=browser_headers,
                                           json={'username': 'r85-operator', 'password': 'synthetic-password'})
                assert login.status_code == 200 and 'HttpOnly' in login.headers['Set-Cookie']
                assert login.headers['Access-Control-Allow-Origin'] == browser_headers['Origin']
                assert login.headers['Access-Control-Allow-Credentials'] == 'true'
                assert (await browser.post(f'{capability}/ollama-check')).status_code == 403
                inventory = await browser.post(f'{capability}/ollama-check', headers=browser_headers,
                                               params={'model': model})
                assert inventory.status_code == 200
                assert inventory.json()['selected_chat_model']['state'] == 'unknown'
                checked = await browser.post(f'{capability}/inference-check', json={'model': model},
                                             headers=browser_headers)
                assert checked.status_code == 200
                report = checked.json()
                assert report['selected_chat_model']['state'] == 'available'
                for key in ('document_rag', 'artifact_access', 'code_execution'):
                    assert report['capabilities'][key]['state'] == 'blocked'
                assert (await browser.post('/api/auth/logout', headers=browser_headers)).status_code == 200
                assert (await browser.get('/api/auth/me')).status_code == 401
        assert app.state.startup_complete is False
    run_offline(exercise())
    assert 'Application startup complete | configured bind=127.0.0.1:8001' in caplog.messages


@pytest.mark.parametrize('overrides', [
    {'auth_enabled': False}, {'app_env': 'production'}, {'app_env': 'protected'},
    {'auth_dev_override': True}, {'backend_host': '0.0.0.0'}, {'ollama_base_url': 'http://example.com:11434'},
])
def test_chat_only_profile_rejects_unsafe_configuration(tmp_path, overrides):
    from backend.config import Settings
    from backend.utils.config_validation import ConfigValidator
    kwargs = dict(_env_file=None, local_chat_only=True, app_env='development', auth_enabled=True,
        backend_host='127.0.0.1', ollama_base_url='http://127.0.0.1:11434',
        upload_dir=tmp_path/'uploads', log_dir=tmp_path/'logs', sandbox_dir=tmp_path/'sandbox',
        chroma_persist_dir=tmp_path/'chroma', tasks_dir=tmp_path/'tasks')
    kwargs.update(overrides)
    _, checks = ConfigValidator(Settings(**kwargs)).validate()
    assert next(item for item in checks if item['rule'] == 'local_chat_only_security')['status'] == 'FAIL'


def test_real_routes_require_authenticated_model_specific_readiness(tmp_path, monkeypatch):
    from test_a21_conversation_offline import setup_engine, make_app
    engine, captured = setup_engine(tmp_path)
    engine._router.resolve_model = lambda model: ('ollama', (model or 'ollama/a').removeprefix('ollama/'))
    engine._router.default_model_id = 'ollama/a'
    engine._router._config = {'models': {'ollama/a': {'capabilities': ['chat']}, 'ollama/b': {'capabilities': ['chat']}}}
    engine._router.configured_model_id = lambda cap: None
    app = make_app(engine); app.include_router(routes.router)
    app.state.settings.ollama_base_url = 'http://127.0.0.1:11434'
    app.state.settings.ollama_default_model = 'a'; app.state.settings.embedding_model = 'embed'
    app.state.startup_complete = True; app.state.enforce_model_readiness = True
    app.state.model_observation = (routes._tags_url(app.state), time.monotonic(),
        ModelObservation('available', 'inventory_observed', ('a:latest', 'b:latest')))
    monkeypatch.setattr(routes, 'probe_inference', AsyncMock(return_value=True))
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            body = {'session_id': 's', 'message': 'Explain pumps', 'stream': False, 'tools_enabled': False, 'model': 'ollama/a'}
            assert (await client.post('/api/chat', headers={'Authorization': 'Bearer a'}, json=body)).status_code == 503
            assert (await client.post('/api/health/capabilities/inference-check', json={'model': 'ollama/a'})).status_code == 401
            result = await client.post('/api/health/capabilities/inference-check', headers={'Authorization': 'Bearer a'}, json={'model': 'ollama/a'})
            assert result.status_code == 200 and result.json()['selected_chat_model']['state'] == 'available'
            assert (await client.post('/api/chat', headers={'Authorization': 'Bearer a'}, json=body)).status_code == 200
            body['model'] = 'ollama/b'
            assert (await client.post('/api/chat', headers={'Authorization': 'Bearer a'}, json=body)).status_code == 503
            body['model'] = 'ollama/a'
            assert (await client.post('/api/chat', headers={'Authorization': 'Bearer b'}, json=body)).status_code == 404
            assert len(captured) == 1
    run_offline(exercise())


def test_late_probe_after_inventory_or_configuration_change_cannot_publish(monkeypatch):
    async def exercise(change):
        s = state(); started = asyncio.Event(); release = asyncio.Event()
        async def delayed(*args):
            started.set(); await release.wait(); return True
        monkeypatch.setattr(routes, 'probe_inference', delayed)
        request = SimpleNamespace(app=SimpleNamespace(state=s))
        task = asyncio.create_task(routes.check_inference(routes.InferenceCheck(model='ollama/a'), request, Response(), None))
        await started.wait()
        if change == 'inventory':
            s.model_observation_generation = 1
        else:
            s.settings.ollama_base_url = 'http://127.0.0.1:11435'
        release.set(); await task
        assert s.inference_observation is None
        assert routes._report(s, 'ollama/a')['capabilities']['chat']['state'] != 'available'
    run_offline(exercise('inventory'))
    run_offline(exercise('endpoint'))


def test_verified_readiness_allows_production_numeric_stream_and_owner_check(tmp_path, monkeypatch):
    from test_a21_conversation_offline import make_app
    from test_a31_planning_offline import setup_workflow
    engine = setup_workflow(tmp_path, '[]')
    engine._router.resolve_model = lambda model: ('ollama', (model or 'ollama/a').removeprefix('ollama/'))
    engine._router.default_model_id = 'ollama/a'
    engine._router._config = {'models': {'ollama/a': {'capabilities': ['chat']}}}
    engine._router.configured_model_id = lambda capability: None
    app = make_app(engine); app.include_router(routes.router)
    app.state.settings.ollama_base_url = 'http://127.0.0.1:11434'
    app.state.settings.ollama_default_model = 'a'; app.state.settings.embedding_model = 'embed'
    app.state.startup_complete = True; app.state.enforce_model_readiness = True
    app.state.model_observation = (routes._tags_url(app.state), time.monotonic(),
        ModelObservation('available', 'inventory_observed', ('a:latest',)))
    monkeypatch.setattr(routes, 'probe_inference', AsyncMock(return_value=True))
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            headers = {'Authorization': 'Bearer a'}
            assert (await client.post('/api/health/capabilities/inference-check', headers=headers, json={'model': 'ollama/a'})).status_code == 200
            for text, expected in [('What is 25 + 17?', 'Result: 42'), ('Multiply that result by 3.', 'Result: 126'), ('Add 5 to that result.', 'Result: 131')]:
                result = await client.post('/api/chat', headers=headers, json={'session_id': 's', 'message': text, 'stream': True, 'tools_enabled': True, 'model': 'ollama/a'})
                assert result.status_code == 200 and expected in result.text and 'task_completed' in result.text
            result = await client.get('/api/chat/sessions/s', headers={'Authorization': 'Bearer b'})
            assert result.status_code == 404
    run_offline(exercise())
