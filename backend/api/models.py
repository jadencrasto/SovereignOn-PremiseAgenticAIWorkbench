"""
backend/api/models.py
---------------------
Models API route.

Endpoints:
  GET /api/models         — List all available models from configured providers
  GET /api/models/default — Return the configured default model
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", summary="List available models from all providers")
async def list_models(request: Request):
    """Query each configured provider and return available model names."""
    model_router = request.app.state.model_router
    available = await model_router.list_available_models()
    return {
        "providers": available,
        "default": model_router.default_model_id,
    }


@router.get("/default", summary="Return the default model identifier")
async def get_default_model(request: Request):
    model_router = request.app.state.model_router
    return {"default_model": model_router.default_model_id}
