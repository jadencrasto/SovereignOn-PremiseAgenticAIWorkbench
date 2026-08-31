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


@router.get("/scan", summary="Scan and discover installed local models on host")
async def scan_local_models(request: Request):
    """
    Actively scan local Ollama service and host disk for installed models,
    capabilities, quantization formats, and readiness status.
    """
    import httpx
    from backend.config import settings

    model_router = request.app.state.model_router
    ollama_url = settings.ollama_base_url.rstrip("/")
    
    discovered_models = []
    service_online = False
    error_msg = None

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                service_online = True
                data = resp.json()
                raw_models = data.get("models", [])
                for m in raw_models:
                    name = m.get("name", "")
                    details = m.get("details", {})
                    size_bytes = m.get("size", 0)
                    size_gb = round(size_bytes / (1024 ** 3), 2)
                    
                    caps = []
                    name_lower = name.lower()
                    if "embed" in name_lower or "nomic" in name_lower:
                        caps.append("embedding")
                    elif "llava" in name_lower or "vision" in name_lower or "vl" in name_lower:
                        caps.append("vision")
                        caps.append("chat")
                    else:
                        caps.append("chat")
                        caps.append("reasoning")
                    
                    discovered_models.append({
                        "name": name,
                        "id": f"ollama/{name}",
                        "size_gb": size_gb,
                        "parameter_size": details.get("parameter_size", "unknown"),
                        "quantization_level": details.get("quantization_level", "unknown"),
                        "format": details.get("format", "gguf"),
                        "family": details.get("family", "unknown"),
                        "modified_at": m.get("modified_at", ""),
                        "capabilities": caps,
                    })
    except Exception as exc:
        error_msg = str(exc)

    # Dynamic detection based on discovered model capabilities
    reasoning_models = [m["name"] for m in discovered_models if "chat" in m["capabilities"] or "reasoning" in m["capabilities"]]
    vision_models = [m["name"] for m in discovered_models if "vision" in m["capabilities"]]
    embedding_models = [m["name"] for m in discovered_models if "embedding" in m["capabilities"]]

    has_reasoning = len(reasoning_models) > 0
    has_vision = len(vision_models) > 0
    has_embedding = len(embedding_models) > 0

    return {
        "status": "online" if service_online else "offline",
        "service_url": ollama_url,
        "models_count": len(discovered_models),
        "models": discovered_models,
        "error": error_msg,
        "readiness": {
            "reasoning_model_ready": has_reasoning,
            "reasoning_model_name": reasoning_models[0] if reasoning_models else None,
            "vision_model_ready": has_vision,
            "vision_model_name": vision_models[0] if vision_models else None,
            "embedding_model_ready": has_embedding,
            "embedding_model_name": embedding_models[0] if embedding_models else None,
            "all_ready": has_reasoning,
        },
        "default_model": reasoning_models[0] if reasoning_models else model_router.default_model_id,
    }



@router.post("/preload", summary="Pre-warm a model in VRAM to eliminate cold-start lag")
async def preload_model(request: Request):
    """
    Preload model weights into VRAM so subsequent chat interactions respond instantly.
    """
    import httpx
    from backend.config import settings

    try:
        body = await request.json()
        model_name = body.get("model", "")
        if not model_name:
            return {"status": "error", "message": "No model specified"}
        
        clean_model = model_name.split("/")[-1]
        ollama_url = settings.ollama_base_url.rstrip("/")

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Trigger generate with empty prompt and 30m keep_alive to load weights
            await client.post(
                f"{ollama_url}/api/generate",
                json={"model": clean_model, "prompt": "", "keep_alive": "30m"},
            )
        return {"status": "ok", "model": clean_model, "warmed": True}
    except Exception as exc:
        logger.warning("Failed to preload model: %s", exc)
        return {"status": "warning", "error": str(exc)}


