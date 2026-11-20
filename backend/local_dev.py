"""Composition and HTTP boundary for the explicit local text chat profile."""

import secrets
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.auth.models import ClearanceLevel, User, UserRole
from backend.auth.security import hash_password
from backend.config import PROJECT_ROOT, Settings
from backend.health.capabilities import loopback_tags_url


FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
_ATTACHMENT_ROUTES = {
    "/api/chat/multimodal", "/api/chat/text-preview", "/api/chat/text-file",
    "/api/chat/csv-preview", "/api/chat/csv-file", "/api/chat/json-preview",
    "/api/chat/text-attachments",
}
_CONTENT_POLICY = (
    "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'"
)


def validate_profile(cfg: Settings) -> None:
    if cfg.app_env.lower() not in {"development", "dev"} or cfg.document_source_restore_pending:
        raise ValueError("local_dev requires development and cannot run during restore.")
    if cfg.provenance_key_mode != "ephemeral":
        raise ValueError("local_dev only supports ephemeral provenance keys.")
    if cfg.backend_host not in _LOOPBACK_HOSTS or not 1 <= cfg.backend_port <= 65535:
        raise ValueError("local_dev requires a numeric loopback backend address and a valid port.")
    if not 1 <= cfg.model_timeout_seconds <= 300:
        raise ValueError("local_dev requires a model timeout between 1 and 300 seconds.")
    loopback_tags_url(cfg.ollama_base_url)


def runtime_settings(requested: Settings, root: Path, *, history_db: Path | None = None) -> Settings:
    # Resolve the local runtime from defaults and an explicit allowlist. In
    # particular, inherited database, log, approval, cookie and config paths
    # cannot flow into this composition.
    return Settings.model_construct(
        run_profile="local_dev",
        auth_enabled=True,
        auth_cookie_samesite="strict",
        cors_origins="",
        local_chat_only=True,
        backend_host=requested.backend_host,
        backend_port=requested.backend_port,
        ollama_base_url=loopback_tags_url(requested.ollama_base_url).removesuffix("/api/tags"),
        ollama_default_model=requested.ollama_default_model,
        model_timeout_seconds=requested.model_timeout_seconds,
        upload_dir=root / "uploads",
        log_dir=root / "logs",
        sandbox_dir=root / "sandbox",
        tasks_dir=root,
        tasks_db_path=history_db if history_db is not None else root / "local.sqlite3",
        conversation_memory_path=None if history_db is not None else root / "conversations.json",
        chroma_persist_dir=root / "unused-index",
    )


def new_viewer() -> tuple[User, str]:
    password = secrets.token_urlsafe(24)
    user = User(
        id=f"local_dev_{uuid4().hex}",
        username="local_dev",
        password_hash=hash_password(password),
        role=UserRole.VIEWER.value,
        clearance=ClearanceLevel.L1.value,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return user, password


def announce_viewer(password: str, mode: str) -> None:
    print(
        f"Local development · non-production · text chat only · history {mode}\n"
        f"Sign in as local_dev with this run's password: {password}",
        flush=True,
    )


def install_http_boundary(app, cfg: Settings, dist: Path) -> None:
    address = f"[{cfg.backend_host}]" if cfg.backend_host == "::1" else cfg.backend_host
    authority = address if cfg.backend_port == 80 else f"{address}:{cfg.backend_port}"

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        path = request.url.path.rstrip("/")
        origin = request.headers.get("origin")
        if (request.client is None or request.client.host not in _LOOPBACK_HOSTS
                or request.headers.get("host") != authority
                or (origin is not None and origin != f"http://{authority}")):
            return JSONResponse({"detail": "Local development requires same-origin loopback access."}, 403)
        if path in _ATTACHMENT_ROUTES or (path == "/api/chat" and request.method == "POST"
                and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"):
            return JSONResponse({"detail": "This upload route is unavailable in local development."}, 415)
        if path == "/api/documents" or path.startswith("/api/documents/"):
            return JSONResponse({"detail": "Protected document retrieval is unavailable in local development."}, 503)
        if path.split("/")[1:3] in (["api", "tools"], ["api", "tasks"], ["api", "artifacts"]):
            return JSONResponse({"detail": "This capability is disabled in local development."}, 403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CONTENT_POLICY
        if path == "/api/chat" or path.startswith("/api/chat/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/", StaticFiles(directory=dist, html=True, check_dir=False), name="local-frontend")
