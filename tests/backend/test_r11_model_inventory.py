from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.api.models import list_models
from backend.models.router import ModelRouter
from test_a21_conversation_offline import immediate


def test_model_inventory_normalizes_latest_and_keeps_provider_identity():
    router = ModelRouter.__new__(ModelRouter)
    router._config = {'models': {
        'ollama/embed': {'capabilities': ['embedding']},
        'ollama/chat:7b': {'capabilities': ['chat']},
        'other/chat:7b': {'capabilities': ['chat']},
    }}
    router.list_available_models = AsyncMock(return_value={'ollama': ['embed:latest', 'chat:7b']})
    models = immediate(router.list_models_with_capabilities())
    assert [m['installed'] for m in models] == [True, True, False]


def test_models_route_uses_one_inventory_snapshot():
    router = ModelRouter.__new__(ModelRouter)
    router._settings = SimpleNamespace(ollama_default_model='chat')
    router._config = {'models': {'ollama/chat': {'capabilities': ['chat']}}}
    router.list_available_models = AsyncMock(return_value={'ollama': ['chat:latest']})
    response = immediate(list_models(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(model_router=router))), None))
    assert response['providers'] == {'ollama': ['chat:latest']}
    assert response['models'][0]['installed'] is True
    assert router.list_available_models.await_count == 1


def test_production_rejects_auth_development_override():
    from backend.config import Settings
    from backend.utils.config_validation import ConfigValidator
    cfg = Settings(_env_file=None, app_env='production', auth_enabled=True, auth_dev_override=True)
    _, checks = ConfigValidator(cfg).validate()
    assert next(item for item in checks if item['rule'] == 'prod_auth_dev_override')['status'] == 'FAIL'


def test_preload_does_not_claim_success_on_http_failure(monkeypatch):
    import httpx
    from backend.api.models import preload_model

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return httpx.Response(500, request=httpx.Request('POST', 'http://test.invalid'))

    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    request = SimpleNamespace(json=AsyncMock(return_value={'model': 'chat'}))
    result = immediate(preload_model(request, None))
    assert result['status'] == 'warning'
    assert result['error_code'] == 'MODEL_PRELOAD_FAILED'
