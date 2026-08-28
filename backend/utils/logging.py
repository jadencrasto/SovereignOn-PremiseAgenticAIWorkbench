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

import logging
import logging.handlers
import sys
from pathlib import Path


_CONFIGURED = False


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
        # Minimal structured format for production log aggregators
        fmt = '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
        console.setFormatter(logging.Formatter(fmt))
    root.addHandler(console)

    # ------------------------------------------------------------------
    # Rotating file handler  →  data/logs/app.log
    # ------------------------------------------------------------------
    log_file = log_dir / "app.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    file_handler.setFormatter(logging.Formatter(file_fmt))
    root.addHandler(file_handler)

    # Quieten noisy third-party loggers
    for noisy in ("httpx", "httpcore", "uvicorn.access", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.  Call setup_logging() once at startup first."""
    return logging.getLogger(name)
