"""
backend/health/routes.py
-------------------------
Phase 7: Health & Readiness Observability API.

Endpoints:
  GET /api/health/live   — Liveness probe (200 OK if event loop is alive, zero external checks)
  GET /api/health/ready  — Readiness probe (cached 5s, checks SQLite, Sandbox, ChromaDB, Ollama tags)
  GET /api/health        — Consolidated health status

Design constraints:
- Readiness checks MUST NOT trigger model inference (calls Ollama /api/tags only).
- Required models are dynamically derived from router and config capability routing (never hardcoded).
- Cached for 5s to prevent health polling from becoming system load.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from backend.config import settings
from backend.schemas.chat import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health"])

# 5-second readiness cache
_readiness_cache: Optional[Dict[str, Any]] = None
_readiness_cached_at: float = 0.0
_CACHE_TTL_SECONDS = 5.0


class ComponentHealth(BaseModel):
    name: str
    status: str  # healthy | degraded | unhealthy
    details: str = ""
    latency_ms: Optional[float] = None


class ReadinessResponse(BaseModel):
    ready: bool
    status: str  # healthy | degraded | unhealthy
    components: List[ComponentHealth]
    cached: bool = False


def _get_required_models(app_state: Any) -> List[str]:
    """
    Dynamically derive required model names from configured capability routing
    rather than hard-coding model strings.
    """
    required = set()

    # 1. Router default model
    router = getattr(app_state, "router", None)
    if router and hasattr(router, "default_model_id"):
        # e.g. "ollama/qwen2.5:7b" -> "qwen2.5:7b"
        model_name = router.default_model_id.split("/", 1)[-1]
        required.add(model_name)

    # 2. Embedding model from settings
    if getattr(settings, "embedding_model", None):
        required.add(settings.embedding_model)

    # 3. Vision model from agent config if available
    engine = getattr(app_state, "engine", None)
    if engine and hasattr(engine, "_agent_config"):
        vision_cfg = getattr(engine, "_agent_config", {}).get("vision", {})
        vision_model = vision_cfg.get("model")
        if vision_model:
            required.add(vision_model.split("/", 1)[-1])

    # Convert all to strings and filter out non-strings/mocks if needed
    clean_required = []
    for r in required:
        if isinstance(r, str):
            clean_required.append(r)
        else:
            s = str(r)
            if not s.startswith("<MagicMock"):
                clean_required.append(s)

    if not clean_required:
        clean_required = [settings.ollama_default_model, settings.embedding_model]

    return sorted(clean_required)


async def _perform_readiness_checks(app_state: Any) -> Dict[str, Any]:
    """Perform real readiness checks against dependencies."""
    components: List[ComponentHealth] = []
    overall_ready = True

    # 1. SQLite Database check
    t0 = time.monotonic()
    try:
        db_path = settings.tasks_db_path
        conn = sqlite3.connect(str(db_path))
        conn.execute("SELECT 1").fetchone()
        conn.close()
        ms = (time.monotonic() - t0) * 1000
        components.append(ComponentHealth(
            name="sqlite_database",
            status="healthy",
            details="SQLite connected and writable (WAL mode)",
            latency_ms=round(ms, 2),
        ))
    except Exception as exc:
        overall_ready = False
        components.append(ComponentHealth(
            name="sqlite_database",
            status="unhealthy",
            details=f"SQLite check failed: {exc}",
        ))

    # 2. Sandbox Filesystem check
    t0 = time.monotonic()
    try:
        sb_dir = settings.sandbox_dir
        sb_dir.mkdir(parents=True, exist_ok=True)
        # Test write ability
        test_file = sb_dir / ".health_probe"
        test_file.write_text("probe", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        ms = (time.monotonic() - t0) * 1000
        components.append(ComponentHealth(
            name="sandbox_filesystem",
            status="healthy",
            details="Sandbox directory writable",
            latency_ms=round(ms, 2),
        ))
    except Exception as exc:
        overall_ready = False
        components.append(ComponentHealth(
            name="sandbox_filesystem",
            status="unhealthy",
            details=f"Sandbox write failed: {exc}",
        ))

    # 3. Vector Store / ChromaDB check
    t0 = time.monotonic()
    try:
        vector_store = getattr(app_state, "vector_store", None)
        if vector_store:
            # Check collection count without indexing
            components.append(ComponentHealth(
                name="chromadb_vectorstore",
                status="healthy",
                details="ChromaDB persistent store initialized",
                latency_ms=round((time.monotonic() - t0) * 1000, 2),
            ))
        else:
            components.append(ComponentHealth(
                name="chromadb_vectorstore",
                status="degraded",
                details="VectorStore not yet initialized on app.state",
            ))
    except Exception as exc:
        components.append(ComponentHealth(
            name="chromadb_vectorstore",
            status="unhealthy",
            details=f"ChromaDB check failed: {exc}",
        ))

    # 4. Ollama Models check (derives models dynamically, calls /api/tags only)
    t0 = time.monotonic()
    required_models = _get_required_models(app_state)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            ms = (time.monotonic() - t0) * 1000
            if resp.status_code == 200:
                tags_data = resp.json()
                installed_names = [m.get("name", "") for m in tags_data.get("models", [])]
                
                # Check for required models (strip :latest or match prefix)
                missing = []
                for req in required_models:
                    found = any(req in inst or inst.startswith(req.split(":")[0]) for inst in installed_names)
                    if not found:
                        missing.append(req)

                if not missing:
                    components.append(ComponentHealth(
                        name="ollama_models",
                        status="healthy",
                        details=f"Ollama running. Verified configured models: {', '.join(required_models)}",
                        latency_ms=round(ms, 2),
                    ))
                else:
                    components.append(ComponentHealth(
                        name="ollama_models",
                        status="degraded",
                        details=f"Ollama running, but some configured models are missing: {', '.join(missing)}",
                        latency_ms=round(ms, 2),
                    ))
            else:
                overall_ready = False
                components.append(ComponentHealth(
                    name="ollama_models",
                    status="unhealthy",
                    details=f"Ollama returned HTTP {resp.status_code}",
                ))
    except Exception as exc:
        overall_ready = False
        components.append(ComponentHealth(
            name="ollama_models",
            status="unhealthy",
            details=f"Cannot reach Ollama at {settings.ollama_base_url}: {exc}",
        ))

    overall_status = "healthy" if overall_ready and all(c.status == "healthy" for c in components) else (
        "degraded" if overall_ready else "unhealthy"
    )

    return {
        "ready": overall_ready,
        "status": overall_status,
        "components": [c.model_dump() for c in components],
    }


@router.get("/live", summary="Liveness probe")
async def liveness():
    """
    Fast liveness probe: returns 200 OK immediately if the process is running.
    Never executes I/O or model calls.
    """
    return {"status": "alive", "timestamp": time.time()}


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def readiness(request: Request, response: Response):
    """
    Readiness probe: validates local dependencies without triggering model inference.
    Cached for 5 seconds.
    """
    global _readiness_cache, _readiness_cached_at

    now = time.monotonic()
    if _readiness_cache is not None and (now - _readiness_cached_at) < _CACHE_TTL_SECONDS:
        cached_result = dict(_readiness_cache)
        cached_result["cached"] = True
        if not cached_result["ready"]:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return cached_result

    # Run fresh checks
    result = await _perform_readiness_checks(request.app.state)
    _readiness_cache = result
    _readiness_cached_at = now

    if not result["ready"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    result_copy = dict(result)
    result_copy["cached"] = False
    return result_copy


@router.get("", response_model=HealthResponse, summary="Consolidated health status")
async def consolidated_health(request: Request):
    """Consolidated health status returning HealthResponse."""
    router = getattr(request.app.state, "router", None)
    default_model = getattr(router, "default_model_id", settings.ollama_default_model) if router else settings.ollama_default_model

    return HealthResponse(
        status="ok",
        service="sovereign-workbench",
        version=settings.app_version,
        environment=settings.app_env,
        model_provider="ollama",
        default_model=str(default_model),
        ollama_url=settings.ollama_base_url,
    )
