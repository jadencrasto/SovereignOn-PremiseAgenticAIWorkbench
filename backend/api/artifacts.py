"""
backend/api/artifacts.py
-------------------------
Artifact manager & download API for generated reports (XLSX, CSV, Markdown, TXT).

Endpoints:
  GET /api/artifacts             — List generated files in data/sandbox/
  GET /api/artifacts/{filename}  — Download artifact file with proper MIME type
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from backend.config import settings
from backend.tools.safety import validate_path_within

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

_MIME_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".json": "application/json",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".pdf": "application/pdf",
}


@router.get("", summary="List generated artifacts")
async def list_artifacts():
    """Returns metadata for all files currently in the sandbox output directory."""
    sb_dir = settings.sandbox_dir
    sb_dir.mkdir(parents=True, exist_ok=True)

    artifacts: List[Dict[str, Any]] = []
    for entry in sorted(sb_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if entry.is_file() and not entry.name.startswith("."):
            stat = entry.stat()
            file_bytes = entry.read_bytes()
            sha = hashlib.sha256(file_bytes).hexdigest()
            suffix = entry.suffix.lower()
            artifacts.append({
                "filename": entry.name,
                "path": f"data/sandbox/{entry.name}",
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
                "format": suffix.lstrip("."),
                "mime_type": _MIME_TYPES.get(suffix, "application/octet-stream"),
                "sha256_hash": sha,
            })

    return {"artifacts": artifacts, "count": len(artifacts)}


@router.get("/{filename}", summary="Download an artifact file")
async def download_artifact(filename: str):
    """Download a generated artifact from data/sandbox/."""
    sb_dir = settings.sandbox_dir
    try:
        target_path = validate_path_within(filename, sb_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact '{filename}' not found.")

    suffix = target_path.suffix.lower()
    media_type = _MIME_TYPES.get(suffix, "application/octet-stream")

    return FileResponse(
        path=target_path,
        media_type=media_type,
        filename=target_path.name,
    )
