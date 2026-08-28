"""
tests/backend/test_router.py
----------------------------
Tests for the ModelRouter.
"""

import pytest
from backend.config import Settings
from backend.models.router import ModelRouter
from backend.models.base import BaseModelProvider


def make_router() -> ModelRouter:
    s = Settings()
    return ModelRouter(s)


def test_router_creates():
    router = make_router()
    assert router is not None


def test_default_model_id_format():
    router = make_router()
    default = router.default_model_id
    assert "/" in default, f"Expected 'provider/model' format, got: {default}"


def test_resolve_model_full_id():
    router = make_router()
    provider_name, model_name = router.resolve_model("ollama/qwen2.5:7b")
    assert provider_name == "ollama"
    assert model_name == "qwen2.5:7b"


def test_resolve_model_bare_name():
    router = make_router()
    provider_name, model_name = router.resolve_model("mistral")
    assert provider_name == "ollama"
    assert model_name == "mistral"


def test_resolve_model_none_uses_default():
    router = make_router()
    provider_name, model_name = router.resolve_model(None)
    assert "/" in f"{provider_name}/{model_name}"


def test_get_provider_ollama():
    router = make_router()
    provider = router.get_provider("ollama")
    assert isinstance(provider, BaseModelProvider)
    assert provider.provider_name == "ollama"


def test_get_provider_unknown_raises():
    router = make_router()
    with pytest.raises(ValueError, match="Unknown model provider"):
        router.get_provider("nonexistent_provider")


def test_provider_is_cached():
    router = make_router()
    p1 = router.get_provider("ollama")
    p2 = router.get_provider("ollama")
    assert p1 is p2
