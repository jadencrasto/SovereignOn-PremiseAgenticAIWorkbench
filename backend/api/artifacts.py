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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from backend.auth.dependencies import require_permission
from backend.auth.models import Permission, User
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
async def list_artifacts(
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
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


MAX_PREVIEW_BYTES = 500 * 1024  # 500 KB limit for preview payload


@router.get("/{filename}/preview", summary="Preview an artifact file content")
async def preview_artifact(
    filename: str,
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    """
    Read-only preview endpoint with security hardening:
    - Path traversal prevention
    - Sandbox isolation
    - Content-size limits (500KB cap)
    - Safe parsing with error boundary (422 on corrupt file)
    - No execution of file content
    """
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename format.")

    sb_dir = settings.sandbox_dir
    try:
        target_path = validate_path_within(filename, sb_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact '{filename}' not found.")

    suffix = target_path.suffix.lower()
    stat = target_path.stat()
    file_size = stat.st_size

    # Handle text/code/markdown/json/csv formats
    if suffix in (".txt", ".md", ".json", ".csv"):
        try:
            raw_bytes = target_path.read_bytes()
            truncated = len(raw_bytes) > MAX_PREVIEW_BYTES
            display_bytes = raw_bytes[:MAX_PREVIEW_BYTES]
            text = display_bytes.decode("utf-8", errors="replace")
            return {
                "type": suffix.lstrip("."),
                "filename": target_path.name,
                "content": text,
                "truncated": truncated,
                "metadata": {"size_bytes": file_size, "encoding": "utf-8"}
            }
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Failed to read text file: {str(exc)}")

    # Handle DOCX
    elif suffix == ".docx":
        try:
            from docx import Document
            doc = Document(target_path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            tables = []
            for t in doc.tables:
                table_data = []
                for row in t.rows:
                    table_data.append([cell.text.strip() for cell in row.cells])
                if table_data:
                    tables.append({
                        "headers": table_data[0],
                        "rows": table_data[1:] if len(table_data) > 1 else []
                    })

            total_text_len = sum(len(p) for p in paragraphs) + sum(
                sum(len(str(c)) for c in r) for tbl in tables for r in tbl["rows"]
            )
            truncated = total_text_len > MAX_PREVIEW_BYTES

            return {
                "type": "docx",
                "filename": target_path.name,
                "content": {
                    "paragraphs": paragraphs[:100] if truncated else paragraphs,
                    "tables": tables[:10] if truncated else tables,
                },
                "truncated": truncated,
                "metadata": {
                    "size_bytes": file_size,
                    "paragraph_count": len(paragraphs),
                    "table_count": len(tables),
                }
            }
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Failed to parse DOCX document: {str(exc)}")

    # Handle XLSX
    elif suffix in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(target_path, data_only=True, read_only=True)
            sheet = wb.active
            if not sheet:
                raise ValueError("Spreadsheet has no active sheets.")

            all_rows = []
            for row in sheet.iter_rows(values_only=True):
                if any(v is not None and str(v).strip() != "" for v in row):
                    all_rows.append([str(v) if v is not None else "" for v in row])

            wb.close()

            headers = []
            rows = []
            if all_rows:
                headers = all_rows[0]
                rows = all_rows[1:]

            truncated = len(rows) > 500
            display_rows = rows[:500] if truncated else rows

            return {
                "type": "xlsx",
                "filename": target_path.name,
                "content": {
                    "sheet_name": sheet.title if hasattr(sheet, "title") else "Sheet1",
                    "headers": headers,
                    "rows": display_rows,
                    "row_count": len(rows),
                },
                "truncated": truncated,
                "metadata": {
                    "size_bytes": file_size,
                    "total_rows": len(rows),
                    "columns": len(headers),
                }
            }
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Failed to parse XLSX workbook: {str(exc)}")

    # Handle PDF
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(target_path)
            extracted_pages = []
            total_chars = 0
            truncated = False
            for page_idx, page in enumerate(reader.pages):
                txt = page.extract_text() or ""
                total_chars += len(txt)
                if total_chars > MAX_PREVIEW_BYTES:
                    extracted_pages.append(txt[:MAX_PREVIEW_BYTES - (total_chars - len(txt))])
                    truncated = True
                    break
                extracted_pages.append(txt)

            return {
                "type": "pdf",
                "filename": target_path.name,
                "content": "\n\n--- Page Break ---\n\n".join(extracted_pages),
                "truncated": truncated,
                "metadata": {
                    "size_bytes": file_size,
                    "page_count": len(reader.pages),
                }
            }
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Failed to parse PDF document: {str(exc)}")

    else:
        return {
            "type": "unsupported",
            "filename": target_path.name,
            "content": None,
            "truncated": False,
            "metadata": {
                "size_bytes": file_size,
                "message": "Preview not available for this file format."
            }
        }


@router.get("/{filename}", summary="Download an artifact file")
async def download_artifact(
    filename: str,
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
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
