"""Real health router mounted alone; no app lifespan or outbound client."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import sniffio
from fastapi import FastAPI

from backend.auth.dependencies import _get_synthetic_dev_admin
from backend.auth.request_context import ClearanceContextMiddleware, effective_owner
from backend.health import routes
from backend.health.capabilities import ModelObservation


def test_real_health_router_authentication_and_recovery(monkeypatch):
    app = FastAPI()
    app.add_middleware(ClearanceContextMiddleware)
    app.include_router(routes.router)
    app.state.settings = SimpleNamespace(app_env='development', auth_enabled=True,
        ollama_base_url='http://127.0.0.1:11434', ollama_default_model='qwen:7b',
        embedding_model='embed:latest')
    app.state.startup_complete = False
    app.state.session_manager = MagicMock()
    app.state.session_manager.validate_session.return_value = SimpleNamespace(user_id='u')
    app.state.auth_store = MagicMock()
    app.state.auth_store.get_user_by_id.return_value = _get_synthetic_dev_admin()
    fake = AsyncMock(return_value=ModelObservation('unavailable', 'ollama_check_failed'))
    monkeypatch.setattr(routes, 'observe_ollama', fake)
    async def exercise():
      async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/health/ready')).status_code == 503
        passive = await client.get('/api/health/capabilities')
        assert passive.status_code == 200 and passive.headers['cache-control'] == 'no-store'
        assert passive.json()['capabilities']['backend']['state'] == 'unavailable'
        assert (await client.post('/api/health/capabilities/ollama-check')).status_code == 401
        fake.assert_not_awaited()
        app.state.startup_complete = True
        assert (await client.get('/api/health/ready')).json()['ready'] is True
        response = await client.post('/api/health/capabilities/ollama-check', headers={'Authorization': 'Bearer fake'})
        assert response.status_code == 200
        assert response.json()['capabilities']['ollama']['state'] == 'unavailable'
        assert 'local_tools' not in response.json()
        for name in ('document_rag', 'approval_resume', 'artifact_access', 'code_execution'):
            assert response.json()['capabilities'][name]['state'] == 'blocked'
        fake.assert_awaited_once_with('http://127.0.0.1:11434/api/tags')
    # No I/O, sockets, event loop, threads or background tasks are allowed.
    # Fail if the ASGI pipeline ever actually suspends.
    token = sniffio.current_async_library_cvar.set('asyncio')
    prior_owner = effective_owner.get()
    coro = exercise()
    try:
        with pytest.raises(StopIteration):
            coro.send(None)
        assert effective_owner.get() == prior_owner
    finally:
        coro.close()
        sniffio.current_async_library_cvar.reset(token)
