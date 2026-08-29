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
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

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
    chunk_size: int = Field(default=500)
    chunk_overlap: int = Field(default=50)

    # ------------------------------------------------------------------
    # Tool execution  (used in Phase 3)
    # ------------------------------------------------------------------
    sandbox_dir: Path = Field(default=PROJECT_ROOT / "data" / "sandbox")
    code_exec_timeout: int = Field(default=30)
    max_file_size_mb: int = Field(default=50)

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
    # Phase 7: Local Authentication & RBAC
    # ------------------------------------------------------------------
    auth_enabled: bool = Field(default=True)
    auth_idle_timeout_seconds: int = Field(default=28800)       # 8 hours
    auth_max_session_seconds: int = Field(default=86400)         # 24 hours
    auth_lockout_attempts: int = Field(default=5)
    auth_lockout_window_seconds: int = Field(default=900)        # 15 minutes

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
