"""
backend/config.py
-----------------
Application configuration using pydantic-settings.
All settings are read from environment variables and/or a .env file.
No machine-specific absolute paths are hardcoded here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator

# ---------------------------------------------------------------------------
# Project root — one level up from this file (backend/config.py → project/)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """
    Central settings object.  Populated from environment variables and the
    .env file found at the project root.  All path settings default to
    sub-directories of the project root so the project remains portable.
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # General
    # ------------------------------------------------------------------
    app_name: str = Field(default="SovereignAIWorkbench")
    app_version: str = Field(default="0.1.0-internal")
    app_env: str = Field(default="development")
    environment: Optional[str] = Field(default=None, description="Alias for app_env")
    dev_mode: Optional[bool] = Field(
        default=None,
        description="Optional dev mode flag. Contradiction with production fails closed.",
    )
    enable_docs_in_prod: bool = Field(
        default=False,
        description="Expose /docs, /redoc, /openapi.json in production when True.",
    )
    log_level: str = Field(default="INFO")

    # ------------------------------------------------------------------
    # Backend server
    # ------------------------------------------------------------------
    backend_host: str = Field(default="0.0.0.0")
    backend_port: int = Field(default=8000)

    # ------------------------------------------------------------------
    # CORS  — comma-separated list of allowed origins
    # ------------------------------------------------------------------
    cors_origins: str = Field(default="http://localhost:5173")

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ------------------------------------------------------------------
    # Ollama (local model provider)
    # ------------------------------------------------------------------
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_default_model: str = Field(default="qwen2.5:7b")

    # ------------------------------------------------------------------
    # RAG / Embeddings  (used in Phase 2; included here for completeness)
    # ------------------------------------------------------------------
    embedding_provider: str = Field(default="ollama")
    embedding_model: str = Field(default="nomic-embed-text")
    chroma_persist_dir: Path = Field(default=PROJECT_ROOT / "data" / "chromadb")
    # NOTE: Changing these values only affects NEWLY ingested documents.
    # Existing indexed documents in data/chromadb/ retain their original chunk sizes.
    # For optimal retrieval quality, re-ingest documents after changing these values.
    chunk_size: int = Field(default=800)
    chunk_overlap: int = Field(default=100)

    # ------------------------------------------------------------------
    # Tool execution  (used in Phase 3)
    # ------------------------------------------------------------------
    sandbox_dir: Path = Field(default=PROJECT_ROOT / "data" / "sandbox")
    code_exec_timeout: int = Field(default=30)
    max_file_size_mb: int = Field(default=50)

    # ------------------------------------------------------------------
    # Phase B Step 8: Code Execution Isolation
    # ------------------------------------------------------------------
    code_exec_isolation: str = Field(
        default="subprocess",
        description="Isolation backend: 'subprocess' (hardened local subprocess) or 'docker' (container isolation)."
    )
    code_exec_docker_image: str = Field(
        default="sovereign-code-sandbox:latest",
        description="Pre-existing local Docker image name. Never automatically pulled."
    )
    code_exec_docker_memory_mb: int = Field(default=512, ge=64, le=4096)
    code_exec_docker_cpus: float = Field(default=1.0, ge=0.1, le=4.0)
    code_exec_docker_pids_limit: int = Field(default=64, ge=16, le=512)

    # ------------------------------------------------------------------
    # Data paths
    # ------------------------------------------------------------------
    upload_dir: Path = Field(default=PROJECT_ROOT / "data" / "uploads")
    log_dir: Path = Field(default=PROJECT_ROOT / "data" / "logs")

    # ------------------------------------------------------------------
    # Phase 6 & 7: Task persistence & Timeouts
    # ------------------------------------------------------------------
    tasks_dir: Path = Field(default=PROJECT_ROOT / "data" / "tasks")
    tasks_db_path: Path = Field(default=PROJECT_ROOT / "data" / "tasks" / "tasks.db")
    approval_timeout_seconds: int = Field(default=300)
    max_plan_steps: int = Field(default=10)
    tool_timeout_seconds: int = Field(default=30)
    model_timeout_seconds: int = Field(default=120)

    # ------------------------------------------------------------------
    # Phase 7: Local Authentication & RBAC (default False for dev, enforced True in prod)
    # ------------------------------------------------------------------
    auth_enabled: bool = Field(default=False)
    auth_idle_timeout_seconds: int = Field(default=28800)       # 8 hours
    auth_max_session_seconds: int = Field(default=86400)         # 24 hours
    auth_lockout_attempts: int = Field(default=5)
    auth_lockout_window_seconds: int = Field(default=900)        # 15 minutes
    auth_cookie_secure: bool = Field(default=False)
    auth_cookie_samesite: str = Field(default="lax")
    auth_cookie_domain: Optional[str] = Field(default=None)

    # ------------------------------------------------------------------
    # Phase 7: Audit Logging
    # ------------------------------------------------------------------
    audit_retention_days: int = Field(default=180)
    audit_max_rows: int = Field(default=50000)

    # ------------------------------------------------------------------
    # Agent config paths
    # ------------------------------------------------------------------
    agents_dir: Path = Field(default=PROJECT_ROOT / "agents")
    config_dir: Path = Field(default=PROJECT_ROOT / "config")

    @model_validator(mode="after")
    def sync_and_validate_env(self) -> "Settings":
        if self.environment is not None and (not self.app_env or self.app_env == "development"):
            self.app_env = self.environment
        elif self.environment is not None:
            env_prod = self.environment.lower() in ("production", "prod")
            app_prod = self.app_env.lower() in ("production", "prod")
            if env_prod != app_prod:
                # Contradiction: fail closed to production
                self.app_env = "production"
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")

    @property
    def is_development(self) -> bool:
        return not self.is_production

    def ensure_dirs(self) -> None:
        """Create all runtime directories that must exist before startup."""
        for d in [self.upload_dir, self.log_dir, self.sandbox_dir, self.chroma_persist_dir, self.tasks_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def get_log_level(self) -> int:
        return getattr(logging, self.log_level.upper(), logging.INFO)


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere
# ---------------------------------------------------------------------------
settings = Settings()
