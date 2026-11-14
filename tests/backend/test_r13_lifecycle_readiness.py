import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from backend.health import routes
from backend.health.capabilities import ModelObservation, parse_tag_digests
from test_a22_async_offline import run_offline
from test_r12_readiness_offline import state


def config(tmp_path):
    from backend.config import Settings
    return Settings(_env_file=None, app_env='development', auth_enabled=True, local_chat_only=True,
        backend_host='127.0.0.1', ollama_base_url='http://127.0.0.1:11434',
        upload_dir=tmp_path/'uploads', log_dir=tmp_path/'logs', sandbox_dir=tmp_path/'sandbox',
        chroma_persist_dir=tmp_path/'chroma', tasks_dir=tmp_path/'tasks',
        tasks_db_path=tmp_path/'tasks'/'tasks.db', conversation_memory_path=tmp_path/'memory.json')


def test_partial_startup_closes_acquired_resources_preserving_original(tmp_path, monkeypatch, caplog):
    import backend.main as main
    acquired = []
    class Router:
        default_model_id = 'ollama/a'
        def __init__(self, cfg): self.aclose = AsyncMock(side_effect=RuntimeError('sensitive router error')); acquired.append(self)
    class Embed:
        def __init__(self): self.aclose = AsyncMock(); acquired.append(self)
    monkeypatch.setattr(main, 'ModelRouter', Router)
    monkeypatch.setattr(main, '_UnavailableEmbeddingService', Embed)
    monkeypatch.setattr(main, 'initialize_admin_user_if_empty', lambda store: None)
    monkeypatch.setattr(main, 'VectorStore', lambda **kwargs: pytest.fail('Chroma initialized'))
    monkeypatch.setattr(main, 'EmbeddingService', lambda **kwargs: pytest.fail('embedding initialized'))
    def fail(cfg): raise ValueError('original startup error')
    monkeypatch.setattr(main, '_initialize_vector_store', fail)
    app = main.create_app(config(tmp_path))
    async def exercise():
        with pytest.raises(ValueError, match='original startup error'):
            async with main.lifespan(app): pytest.fail('startup unexpectedly succeeded')
        assert not app.state.startup_complete
        for obj in acquired: obj.aclose.assert_awaited_once()
        assert len(acquired) == 2
        assert 'Application resource cleanup failed: model router' in caplog.text
        assert 'sensitive router error' not in caplog.text
    run_offline(exercise())


def test_lifespan_owns_only_its_instances_and_closes_once(tmp_path, monkeypatch):
    import backend.main as main
    created = []
    original = main.ModelRouter
    class Router(original):
        def __init__(self, cfg):
            super().__init__(cfg); self.close_calls = 0; created.append(self)
        async def aclose(self):
            self.close_calls += 1
            await super().aclose()
    monkeypatch.setattr(main, 'ModelRouter', Router)
    monkeypatch.setattr(main, 'initialize_admin_user_if_empty', lambda store: None)
    async def exercise():
        first = main.create_app(config(tmp_path/'one')); second = main.create_app(config(tmp_path/'two'))
        async with main.lifespan(first):
            async with main.lifespan(second):
                assert first.state.startup_complete and second.state.startup_complete
            assert first.state.startup_complete and not second.state.startup_complete
            assert [x.close_calls for x in created] == [0, 1]
        assert [x.close_calls for x in created] == [1, 1]
        assert not first.state.startup_complete
    run_offline(exercise())


def test_graph_connections_are_closed_on_success_and_failure(tmp_path, monkeypatch):
    import backend.graph.store as graph
    connections = []
    class Tracked(sqlite3.Connection):
        def close(self):
            connections.append('closed')
            return super().close()
    original = sqlite3.connect
    def connect(*args, **kwargs): return original(*args, **kwargs, factory=Tracked)
    monkeypatch.setattr(graph.sqlite3, 'connect', connect)
    store = graph.KnowledgeGraphStore(tmp_path/'graph.db')
    before = len(connections)
    with pytest.raises(ValueError):
        with store._get_connection() as conn:
            conn.execute('SELECT 1')
            raise ValueError('injected')
    assert len(connections) == before + 1 and before >= 1


def test_inventory_digest_and_readiness_invalidation():
    digest = 'a' * 64; replacement = 'b' * 64
    payload = {'models': [{'name': 'a:latest', 'digest': digest}]}
    assert parse_tag_digests(payload) == (('a:latest', digest),)
    assert parse_tag_digests({'models': [{'name': 'a'}]}) == ()
    with pytest.raises(ValueError): parse_tag_digests({'models': [{'name': 'a', 'digest': 'fake'}]})
    with pytest.raises(ValueError): parse_tag_digests({'models': [{'name': 'a'}, {'name': 'a:latest'}]})
    s = state(); target = routes._tags_url(s); identity = routes._identity(s, 'ollama/a')
    s.model_observation = (target, time.monotonic(), ModelObservation('available', 'inventory_observed', ('a:latest',), (('a:latest', digest),)))
    s.inference_observation = (identity, time.monotonic(), True, digest)
    assert routes._report(s, 'ollama/a')['selected_chat_model']['state'] == 'available'
    s.model_observation = (target, time.monotonic(), ModelObservation('available', 'inventory_observed', ('a:latest',), (('a:latest', replacement),)))
    assert routes._report(s, 'ollama/a')['selected_chat_model']['reason'] == 'model_identity_changed'
    s.model_observation = (target, time.monotonic(), ModelObservation('available', 'inventory_observed', ('a:latest',)))
    report = routes._report(s, 'ollama/a')['selected_chat_model']
    assert report['state'] != 'available' and report['identity_evidence'] == 'unavailable'


def test_router_closes_all_cached_providers_even_when_one_fails():
    from backend.models.router import ModelRouter
    first = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError('private')))
    second = SimpleNamespace(aclose=AsyncMock())
    router = ModelRouter.__new__(ModelRouter); router._provider_cache = {'a': first, 'b': second}
    async def exercise():
        with pytest.raises(RuntimeError, match='cleanup failed'): await router.aclose()
        await router.aclose()
        first.aclose.assert_awaited_once(); second.aclose.assert_awaited_once()
    run_offline(exercise())


def test_readiness_then_provider_failure_is_stream_error(tmp_path):
    from test_a21_conversation_offline import setup_engine, make_app
    from backend.models.base import ChatChunk
    engine, _ = setup_engine(tmp_path)
    class Broken:
        provider_name = 'fake'
        async def chat_stream(self, request):
            raise RuntimeError('Model provider unavailable')
            yield ChatChunk('', False)
    engine._router.resolve_model = lambda model: ('ollama', 'a')
    engine._router.default_model_id = 'ollama/a'
    engine._router._config = {'models': {'ollama/a': {'capabilities': ['chat']}}}
    engine._router.configured_model_id = lambda cap: None
    engine._router.get_provider_for_model = lambda model: (Broken(), 'a')
    app = make_app(engine)
    app.state.settings.ollama_base_url = 'http://127.0.0.1:11434'
    app.state.settings.ollama_default_model = 'a'; app.state.settings.embedding_model = 'embed'
    app.state.startup_complete = True; app.state.enforce_model_readiness = True
    app.state.model_observation = (routes._tags_url(app.state), time.monotonic(), ModelObservation('available', 'inventory_observed', ('a:latest',)))
    app.state.inference_observation = (routes._identity(app.state, 'ollama/a'), time.monotonic(), True, None)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/chat', headers={'Authorization': 'Bearer a'},
                json={'session_id': 's', 'message': 'Hello', 'stream': True, 'tools_enabled': False, 'model': 'ollama/a'})
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert events[-1]['type'] == 'error'
            assert not any(event['type'] == 'done' for event in events)
    run_offline(exercise())


def test_shutdown_invalidates_inflight_inventory_result(monkeypatch):
    import backend.main as main
    async def exercise():
        s = state(); app = SimpleNamespace(state=s); started = asyncio.Event(); release = asyncio.Event()
        async def delayed(target):
            started.set(); await release.wait()
            return ModelObservation('available', 'inventory_observed', ('a:latest',))
        monkeypatch.setattr(routes, 'observe_ollama', delayed)
        request = SimpleNamespace(app=app)
        from fastapi import Response
        task = asyncio.create_task(routes.check_ollama(request, Response(), None, None))
        await started.wait()
        main._clear_readiness(app)
        s.startup_complete = True
        release.set(); await task
        assert s.model_observation is None and s.inference_observation is None
    run_offline(exercise())


def test_real_inventory_observer_keeps_reported_digest(monkeypatch):
    digest = '4' * 64
    class Reply:
        headers = {}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_raw(self, chunk_size):
            yield json.dumps({'models': [{'name': 'a:latest', 'digest': digest}]}).encode()
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Reply()
    monkeypatch.setattr(routes.httpx, 'AsyncClient', Client)
    observation = run_offline(routes.observe_ollama('http://127.0.0.1:11434/api/tags'))
    assert observation.models == ('a:latest',) and observation.digests == (('a:latest', digest),)


def test_failure_after_router_before_embedding_closes_router(tmp_path, monkeypatch):
    import backend.main as main
    from backend.agent.conversation_registry import ConversationRegistry
    created = []
    class Router:
        default_model_id = 'ollama/a'
        def __init__(self, cfg): self.aclose = AsyncMock(); created.append(self)
    monkeypatch.setattr(main, 'ModelRouter', Router)
    monkeypatch.setattr(main, 'initialize_admin_user_if_empty', lambda store: None)
    def fail(*args, **kwargs): raise RuntimeError('registry failed')
    monkeypatch.setattr(ConversationRegistry, '__init__', fail)
    async def exercise():
        with pytest.raises(RuntimeError, match='registry failed'):
            async with main.lifespan(main.create_app(config(tmp_path))): pass
        created[0].aclose.assert_awaited_once()
    run_offline(exercise())


def test_stream_start_rechecks_expired_readiness_before_engine(tmp_path):
    from test_a21_conversation_offline import setup_engine, make_app
    from backend.api.chat import chat
    from backend.schemas.chat import ChatRequest
    from backend.auth.models import User
    from backend.auth.request_context import effective_owner
    engine, captured = setup_engine(tmp_path)
    engine._router.resolve_model = lambda model: ('ollama', 'a')
    engine._router.default_model_id = 'ollama/a'
    engine._router._config = {'models': {'ollama/a': {'capabilities': ['chat']}}}
    engine._router.configured_model_id = lambda cap: None
    app = make_app(engine)
    app.state.settings.ollama_base_url = 'http://127.0.0.1:11434'
    app.state.settings.ollama_default_model = 'a'; app.state.settings.embedding_model = 'embed'
    app.state.startup_complete = True; app.state.enforce_model_readiness = True
    app.state.model_observation = (routes._tags_url(app.state), time.monotonic(), ModelObservation('available', 'inventory_observed', ('a:latest',)))
    identity = routes._identity(app.state, 'ollama/a')
    app.state.inference_observation = (identity, time.monotonic(), True, None)
    user = User(id='a', username='a', password_hash='x', role='admin', clearance='L3', is_active=True, must_change_password=False, created_at='2026-01-01T00:00:00Z')
    async def exercise():
        token = effective_owner.set('a')
        try:
            body = ChatRequest(session_id='s', message='Hello', stream=True, tools_enabled=False, model='ollama/a')
            response = await chat(body, SimpleNamespace(app=app), engine, engine._router, user)
            app.state.inference_observation = (identity, time.monotonic() - routes.INFERENCE_TTL, True, None)
            events = [part async for part in response.body_iterator]
            assert len(events) == 1 and 'MODEL_NOT_READY' in events[0]
            assert not captured
        finally:
            effective_owner.reset(token)
    run_offline(exercise())


def test_stream_pins_initial_default_model_if_default_changes(tmp_path):
    from test_a21_conversation_offline import setup_engine, make_app
    from backend.api.chat import chat
    from backend.schemas.chat import ChatRequest
    from backend.auth.models import User
    from backend.auth.request_context import effective_owner
    engine, _ = setup_engine(tmp_path); router = engine._router
    selected = []
    original = router.get_provider_for_model
    router.get_provider_for_model = lambda model: (selected.append(model) or original(model))
    router.default_model_id = 'ollama/a'
    router.resolve_model = lambda model: ('ollama', (model or router.default_model_id).removeprefix('ollama/'))
    router._config = {'models': {'ollama/a': {'capabilities': ['chat']}, 'ollama/b': {'capabilities': ['chat']}}}
    router.configured_model_id = lambda cap: None
    app = make_app(engine); app.state.settings.ollama_base_url = 'http://127.0.0.1:11434'
    app.state.settings.ollama_default_model = 'a'; app.state.settings.embedding_model = 'embed'
    app.state.startup_complete = True; app.state.enforce_model_readiness = True
    app.state.model_observation = (routes._tags_url(app.state), time.monotonic(), ModelObservation('available', 'inventory_observed', ('a:latest', 'b:latest')))
    app.state.inference_observation = (routes._identity(app.state, 'ollama/a'), time.monotonic(), True, None)
    user = User(id='a', username='a', password_hash='x', role='admin', clearance='L3', is_active=True, must_change_password=False, created_at='2026-01-01T00:00:00Z')
    async def exercise():
        token = effective_owner.set('a')
        try:
            response = await chat(ChatRequest(session_id='s', message='Hello', stream=True, tools_enabled=False), SimpleNamespace(app=app), engine, router, user)
            router.default_model_id = 'ollama/b'
            result = [part async for part in response.body_iterator]
            assert any('"done"' in part for part in result)
            assert selected == ['ollama/a']
        finally: effective_owner.reset(token)
    run_offline(exercise())
