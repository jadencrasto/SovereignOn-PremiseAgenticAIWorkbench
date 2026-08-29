"""
backend/api/models.py
---------------------
Models API route.

Endpoints:
  GET /api/models         — List all available models from configured providers
  GET /api/models/default — Return the configured default model
  GET /api/models/capabilities — List models with Phase 5 capability metadata

Phase 5: /api/models now also returns capability info in the response,
and a new /api/models/capabilities endpoint exposes the full enriched list.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", summary="List available models from all providers")
async def list_models(request: Request):
    """
    Query each configured provider and return available model names.

    Phase 5: also returns capability_routing and models_with_capabilities.
    """
    model_router = request.app.state.model_router
    available = await model_router.list_available_models()

    # Phase 5: Try to enrich with capabilities. Falls back gracefully if the
    # router doesn't support it (e.g., in legacy test mocks).
    models_with_caps = []
    try:
        models_with_caps = await model_router.list_models_with_capabilities()
    except Exception:
        pass

    # Build capability routing from config if available
    routing_map = {}
    try:
        routing_map = {
            "chat": model_router._config.get("capability_routing", {}).get("chat", ""),
            "vision": model_router._config.get("capability_routing", {}).get("vision", ""),
            "embedding": model_router._config.get("capability_routing", {}).get("embedding", ""),
        }
    except Exception:
        pass

    return {
        "providers": available,
        "default": model_router.default_model_id,
        # Phase 5 additions (empty when not supported by provider):
        "models": models_with_caps,
        "capability_routing": routing_map,
    }


@router.get("/default", summary="Return the default model identifier")
async def get_default_model(request: Request):
    model_router = request.app.state.model_router
    return {"default_model": model_router.default_model_id}


@router.get("/capabilities", summary="List models with capability metadata (Phase 5)")
async def list_models_with_capabilities(request: Request):
    """
    Return all configured models with their capability metadata.

    Useful for the frontend Models view to show which models support
    chat vs. vision vs. embedding.
    """
    model_router = request.app.state.model_router
    models = await model_router.list_models_with_capabilities()
    return {
        "models": models,
        "capability_routing": model_router._config.get("capability_routing", {}),
    }
