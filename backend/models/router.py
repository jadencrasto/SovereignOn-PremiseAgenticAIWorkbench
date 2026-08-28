"""
backend/models/router.py
------------------------
Model router — resolves a 'provider/model' string to a concrete provider
adapter and returns it to the agent engine.

The router reads from config/models.yaml and the application settings.
It is the only place in the codebase that knows which concrete provider
classes exist.  The agent engine only ever receives a BaseModelProvider.

Usage:
    router = ModelRouter(settings)
    provider = router.get_provider("ollama")
    model    = router.resolve_model("ollama/qwen2.5:7b")  # → "qwen2.5:7b"
    default  = router.default_model_id               # → "ollama/qwen2.5:7b"
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml

from backend.config import Settings
from backend.models.base import BaseModelProvider
from backend.models.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)


class ModelRouter:
    """
    Resolves 'provider/model' identifiers to provider adapters.

    Provider adapters are created lazily and cached for reuse.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._config = self._load_models_yaml(settings.config_dir / "models.yaml")
        self._provider_cache: Dict[str, BaseModelProvider] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def default_model_id(self) -> str:
        """
        Full 'provider/model' identifier for the default model.
        Reads from models.yaml first, falls back to settings.
        """
        return self._config.get("default_model", f"ollama/{self._settings.ollama_default_model}")

    def resolve_model(self, model_id: Optional[str] = None) -> Tuple[str, str]:
        """
        Parse a 'provider/model' string into (provider_name, model_name).

        If model_id is None, the configured default is used.
        If model_id has no '/', 'ollama' is assumed as the provider.

        Returns:
            (provider_name, model_name)  e.g. ("ollama", "qwen2.5:7b")
        """
        target = model_id or self.default_model_id
        if "/" in target:
            provider_name, model_name = target.split("/", 1)
        else:
            # bare model name — assume Ollama
            provider_name, model_name = "ollama", target
        return provider_name, model_name

    def get_provider(self, provider_name: str) -> BaseModelProvider:
        """
        Return a (cached) provider adapter for the given provider name.

        Raises ValueError if the provider is not supported.
        """
        name = provider_name.lower()
        if name not in self._provider_cache:
            self._provider_cache[name] = self._create_provider(name)
        return self._provider_cache[name]

    def get_provider_for_model(self, model_id: Optional[str] = None) -> Tuple[BaseModelProvider, str]:
        """
        Convenience: resolve a model ID to (provider, model_name).

        Returns:
            (provider_adapter, model_name)
        """
        provider_name, model_name = self.resolve_model(model_id)
        provider = self.get_provider(provider_name)
        return provider, model_name

    async def list_available_models(self) -> Dict[str, list]:
        """Query each configured provider for its available models."""
        results: Dict[str, list] = {}
        for provider_name in self._get_configured_providers():
            try:
                provider = self.get_provider(provider_name)
                models = await provider.list_models()
                results[provider_name] = models
            except Exception as exc:
                logger.warning("Could not list models for %s: %s", provider_name, exc)
                results[provider_name] = []
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_provider(self, name: str) -> BaseModelProvider:
        """Instantiate a provider adapter by name."""
        if name == "ollama":
            logger.info("Creating OllamaProvider at %s", self._settings.ollama_base_url)
            return OllamaProvider(base_url=self._settings.ollama_base_url)

        # Future providers will be added here without touching agent code:
        # elif name == "openai":
        #     return OpenAIProvider(...)
        # elif name == "openrouter":
        #     return OpenRouterProvider(...)

        raise ValueError(
            f"Unknown model provider: '{name}'. "
            f"Supported providers: ollama"
        )

    def _get_configured_providers(self) -> list:
        """Return provider names listed in models.yaml."""
        return list(self._config.get("providers", {}).keys())

    @staticmethod
    def _load_models_yaml(path: Path) -> dict:
        """Load config/models.yaml, returning {} on any error."""
        try:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning("models.yaml not found at %s — using defaults", path)
            return {}
        except Exception as exc:
            logger.error("Failed to load models.yaml: %s", exc)
            return {}
