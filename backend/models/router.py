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

Phase 5 additions:
    resolve_chat_model()      → (provider, "qwen2.5:7b")
    resolve_vision_model()    → (provider, "llava:7b")
    resolve_embedding_model() → (provider, "nomic-embed-text")
    get_model_capabilities(model_id) → ["chat", "vision"]
    list_models_with_capabilities()  → enriched model list for API
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml


from backend.config import Settings
from backend.models.base import BaseModelProvider
from backend.models.hardware import HardwareManager, ModelAllocationDecision, SystemTelemetry
from backend.models.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)


class ModelRouter:
    """
    Resolves 'provider/model' identifiers to provider adapters with
    resource-aware adaptive placement & proactive VRAM eviction.
    """

    def __init__(self, settings: Settings, hardware_mgr: Optional[HardwareManager] = None) -> None:
        self._settings = settings
        self._config = self._load_models_yaml(settings.config_dir / "models.yaml")
        self._provider_cache: Dict[str, BaseModelProvider] = {}
        self._hardware_mgr = hardware_mgr or HardwareManager()
        self._active_loaded_models: List[str] = []
        self._last_decision: Optional[ModelAllocationDecision] = None


    # ------------------------------------------------------------------
    # Public API — existing (Phase 1–4, unchanged)
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
    # Public API — Phase 5: Capability-based routing
    # ------------------------------------------------------------------

    def get_model_capabilities(self, model_id: str) -> List[str]:
        """
        Return the capabilities list for a given model ID.

        Args:
            model_id: Full 'provider/model' string, e.g. 'ollama/llava:7b'

        Returns:
            List of capability strings, e.g. ['chat', 'vision'].
            Returns [] if model is not in config.
        """
        models_config = self._config.get("models", {})
        model_conf = models_config.get(model_id, {})
        return list(model_conf.get("capabilities", []))

    def resolve_by_capability(self, capability: str) -> Tuple[BaseModelProvider, str]:
        """
        Resolve to (provider, model_name) for the given capability.

        Reads from models.yaml capability_routing section.

        Args:
            capability: 'chat' | 'vision' | 'embedding'

        Returns:
            (provider_adapter, model_name)

        Raises:
            ValueError: If no model is configured for the capability.
        """
        routing = self._config.get("capability_routing", {})
        model_id = routing.get(capability)
        if not model_id:
            # Fall back: scan models config for first model with this capability
            models_config = self._config.get("models", {})
            for mid, mconf in models_config.items():
                if capability in mconf.get("capabilities", []):
                    model_id = mid
                    break

        if not model_id:
            raise ValueError(
                f"No model configured for capability '{capability}'. "
                f"Check config/models.yaml capability_routing section."
            )

        return self.get_provider_for_model(model_id)

    def resolve_chat_model(self) -> Tuple[BaseModelProvider, str]:
        """Return (provider, model_name) for text chat (qwen2.5:7b)."""
        return self.resolve_by_capability("chat")

    def resolve_vision_model(self) -> Tuple[BaseModelProvider, str]:
        """Return (provider, model_name) for vision (llava:7b)."""
        return self.resolve_by_capability("vision")

    def resolve_embedding_model(self) -> Tuple[BaseModelProvider, str]:
        """Return (provider, model_name) for embeddings (nomic-embed-text)."""
        return self.resolve_by_capability("embedding")

    async def list_models_with_capabilities(self) -> List[Dict]:
        """
        Return a list of model dicts enriched with capabilities.

        Used by GET /api/models to expose capability metadata to the frontend.

        Returns:
            [
                {
                    "id": "ollama/qwen2.5:7b",
                    "name": "qwen2.5:7b",
                    "provider": "ollama",
                    "capabilities": ["chat"],
                    "installed": True,
                },
                ...
            ]
        """
        # Get actually-installed models from live Ollama
        available_raw = await self.list_available_models()
        installed_names: set = set()
        for provider_name, model_list in available_raw.items():
            for m in model_list:
                installed_names.add(f"{provider_name}/{m}")
                # Also add without provider prefix for matching
                installed_names.add(m)

        models_config = self._config.get("models", {})
        result = []

        for model_id, mconf in models_config.items():
            provider_name, model_name = self.resolve_model(model_id)
            is_installed = (
                model_id in installed_names
                or model_name in installed_names
            )
            result.append({
                "id": model_id,
                "name": model_name,
                "provider": provider_name,
                "capabilities": list(mconf.get("capabilities", [])),
                "description": mconf.get("description", ""),
                "installed": is_installed,
            })


        return result


    # ------------------------------------------------------------------
    # Resource-aware Hardware & Eviction Management
    # ------------------------------------------------------------------

    def get_hardware_telemetry(self) -> SystemTelemetry:
        """Return live CPU, RAM, and GPU telemetry snapshot."""
        return self._hardware_mgr.get_system_telemetry()

    def get_last_allocation_decision(self) -> Optional[ModelAllocationDecision]:
        """Return the most recent model allocation decision."""
        return self._last_decision

    async def prepare_model_for_task(self, target_model_id: Optional[str] = None) -> ModelAllocationDecision:
        """
        Evaluate hardware requirements, evict incompatible resident models from VRAM
        (especially LLM vs VLM on 4 GB RTX 3050), and set active model.
        """
        provider_name, model_name = self.resolve_model(target_model_id)
        decision = self._hardware_mgr.evaluate_model_allocation(
            target_model=model_name,
            active_loaded_models=self._active_loaded_models,
        )
        self._last_decision = decision

        # Execute required evictions proactively
        if decision.evictions_required and provider_name == "ollama":
            try:
                provider = self.get_provider("ollama")
                for evict_target in decision.evictions_required:
                    if hasattr(provider, "unload_model"):
                        logger.info("Evicting resident model '%s' from VRAM", evict_target)
                        await provider.unload_model(evict_target)
                        if evict_target in self._active_loaded_models:
                            self._active_loaded_models.remove(evict_target)
            except Exception as exc:
                logger.warning("Model eviction error: %s", exc)

        if model_name not in self._active_loaded_models:
            self._active_loaded_models = [model_name]

        return decision

    async def evict_model(self, model_id: str) -> bool:
        """Explicitly unload a model from VRAM."""
        provider_name, model_name = self.resolve_model(model_id)
        if provider_name == "ollama":
            provider = self.get_provider("ollama")
            if hasattr(provider, "unload_model"):
                success = await provider.unload_model(model_name)
                if model_name in self._active_loaded_models:
                    self._active_loaded_models.remove(model_name)
                return success
        return False


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
