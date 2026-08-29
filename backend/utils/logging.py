"""
backend/utils/logging.py
------------------------
Application logging setup.

Uses stdlib logging with a structured JSON-friendly formatter when
running in production, and a human-readable formatter in development.

Log files are written to data/logs/.
Secrets / API keys are NEVER logged.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Request ID context variable for request tracing
request_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id_ctx", default=None)

_CONFIGURED = False


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records into structured JSON including contextual request_id."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        req_id = request_id_ctx.get()
        if req_id:
            log_obj["request_id"] = req_id

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


def setup_logging(log_level: int, log_dir: Path, app_env: str = "development") -> None:
    """Configure root logger. Safe to call multiple times (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(log_level)

    # ------------------------------------------------------------------
    # Console handler
    # ------------------------------------------------------------------
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    if app_env == "development":
        fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        console.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
    else:
        console.setFormatter(StructuredJsonFormatter())
    root.addHandler(console)

    # ------------------------------------------------------------------
    # Rotating file handler  →  data/logs/app.log (always JSON formatted)
    # ------------------------------------------------------------------
    log_file = log_dir / "app.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(StructuredJsonFormatter())
    root.addHandler(file_handler)

    # Quieten noisy third-party loggers
    for noisy in ("httpx", "httpcore", "uvicorn.access", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.  Call setup_logging() once at startup first."""
    return logging.getLogger(name)
