"""
backend/tools/file_read.py
---------------------------
File read tool — reads text file content from the controlled workspace.

Scope: data/uploads/ only.
Security: path traversal, max read size, allowed extensions only.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.safety import validate_path_within, check_file_size

import re
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum read size: 1 MB for plain text, 50 MB for rich documents (.pdf, .docx)
_MAX_READ_BYTES = 1 * 1024 * 1024
_MAX_DOC_READ_BYTES = 50 * 1024 * 1024

# Allowed text and document extensions for direct reading
_ALLOWED_READ_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".log", ".ini", ".cfg", ".toml", ".xml", ".html",
    ".py", ".js", ".ts", ".sh", ".bat", ".ps1",
    ".pdf", ".docx",
}


def _normalize_filename(name: str) -> str:
    """Normalize filename for robust matching across spaces, underscores, casing, and doc prefixes."""
    # Strip doc hash prefix if present (e.g. doc_77b8a806ffba4e08_file.txt -> file.txt)
    clean = re.sub(r"^doc_[a-f0-9]{8,32}_", "", name, flags=re.IGNORECASE)
    # Replace underscores, hyphens, and multiple spaces with a single space, lowercase
    norm = re.sub(r"[\s_\-]+", " ", clean).strip().lower()
    return norm


def _resolve_candidate_file(raw_path: str, upload_dir: Path) -> Path:
    """
    Attempt to resolve raw_path directly, and if not found, perform normalized
    matching against existing files in upload_dir.
    Handles:
      - spaces vs underscores (e.g. 'Company_Safety_Training_Record.txt' vs 'Company Safety Training Record.txt')
      - case differences
      - uploaded doc_{id}_ prefixes on disk
      - missing file extension if unambiguous

    Does NOT silently substitute arbitrary or unrelated files.
    """
    # 1. Direct path validation first (ensures path safety: traversal/null-bytes reject immediately)
    resolved = validate_path_within(raw_path, upload_dir)
    if resolved.exists() and resolved.is_file():
        return resolved

    target_name = Path(raw_path).name
    target_norm = _normalize_filename(target_name)
    target_stem_norm = _normalize_filename(Path(raw_path).stem)
    target_ext = Path(raw_path).suffix.lower()

    if not upload_dir.exists():
        return resolved

    # Scan upload_dir for matching files
    candidates = []
    for item in upload_dir.iterdir():
        if not item.is_file():
            continue
        c_norm = _normalize_filename(item.name)
        c_stem_norm = _normalize_filename(item.stem)
        c_ext = item.suffix.lower()

        # Priority 1: Exact normalized name match (e.g. spaces/underscores/case/prefix match)
        if c_norm == target_norm:
            candidates.append((1, item))
        # Priority 2: Stem match with matching extension
        elif c_stem_norm == target_stem_norm and target_ext and c_ext == target_ext:
            candidates.append((2, item))
        # Priority 3: Stem match when target had no extension
        elif c_stem_norm == target_stem_norm and not target_ext and c_ext in _ALLOWED_READ_EXTENSIONS:
            candidates.append((3, item))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        best = candidates[0][1]
        logger.info("file_read | resolved '%s' -> '%s'", raw_path, best.name)
        return best

    return resolved


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

class FileReadInput(BaseModel):
    """Input schema for the file_read tool."""
    relative_path: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Relative path to a file inside the uploads workspace, e.g. 'report.txt'.",
    )
    filename: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Alternative filename parameter.",
    )
    path: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Alternative path parameter.",
    )
    file_path: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Alternative file_path parameter.",
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_file_read(upload_dir: Path) -> callable:
    """
    Create the file_read execute function.

    Args:
        upload_dir: Absolute path to the uploads directory (settings.upload_dir).
    """

    async def execute_file_read(args: FileReadInput) -> dict:
        """Read a text file from the controlled workspace."""
        raw_path = args.relative_path or args.filename or args.path or args.file_path
        if not raw_path:
            raise ValueError("file_read requires 'relative_path' or 'filename'")

        # Validate & resolve path with normalization fallback
        resolved = _resolve_candidate_file(raw_path, upload_dir)

        # Check existence
        if not resolved.exists():
            raise ValueError(f"File not found: '{raw_path}'")

        if not resolved.is_file():
            raise ValueError(f"'{raw_path}' is not a file.")

        # Check extension
        ext = resolved.suffix.lower()
        if ext not in _ALLOWED_READ_EXTENSIONS:
            raise ValueError(
                f"File type '{ext}' is not supported for direct reading. "
                f"Supported: {sorted(_ALLOWED_READ_EXTENSIONS)}."
            )

        # Check size: allow 50 MB for rich documents (.pdf, .docx), 1 MB for text files
        max_limit = _MAX_DOC_READ_BYTES if ext in {".pdf", ".docx"} else _MAX_READ_BYTES
        check_file_size(resolved, max_limit)

        # Read content
        try:
            if ext in {".pdf", ".docx"}:
                from backend.rag.ingest import DocumentParser
                doc = DocumentParser.parse(resolved.name, resolved.read_bytes())
                content = doc.text
            else:
                content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(f"Could not read file: {exc}")

        rel_path = str(resolved.resolve().relative_to(upload_dir.resolve())).replace("\\", "/")
        clean_filename = re.sub(r"^doc_[a-f0-9]{8,32}_", "", resolved.name)
        logger.info("file_read | path=%s size=%d", rel_path, len(content))

        return {
            "filename": clean_filename,
            "relative_path": rel_path,
            "size_bytes": len(content.encode("utf-8")),
            "extension": ext,
            "content": content,
        }

    return execute_file_read
