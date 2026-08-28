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

logger = logging.getLogger(__name__)

# Maximum read size: 1 MB
_MAX_READ_BYTES = 1 * 1024 * 1024

# Allowed text extensions for direct reading
_ALLOWED_READ_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".log", ".ini", ".cfg", ".toml", ".xml", ".html",
    ".py", ".js", ".ts", ".sh", ".bat", ".ps1",
}


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

class FileReadInput(BaseModel):
    """Input schema for the file_read tool."""
    relative_path: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Relative path to a file inside the uploads workspace, e.g. 'report.txt'.",
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
        # Validate path
        resolved = validate_path_within(args.relative_path, upload_dir)

        # Check existence
        if not resolved.exists():
            raise ValueError(f"File not found: '{args.relative_path}'")

        if not resolved.is_file():
            raise ValueError(f"'{args.relative_path}' is not a file.")

        # Check extension
        ext = resolved.suffix.lower()
        if ext not in _ALLOWED_READ_EXTENSIONS:
            raise ValueError(
                f"File type '{ext}' is not supported for direct reading. "
                f"Supported: {sorted(_ALLOWED_READ_EXTENSIONS)}. "
                f"For PDF/DOCX files, use the document_search tool instead."
            )

        # Check size
        check_file_size(resolved, _MAX_READ_BYTES)

        # Read content
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(f"Could not read file: {exc}")

        rel_path = str(resolved.relative_to(upload_dir.resolve())).replace("\\", "/")
        logger.info("file_read | path=%s size=%d", rel_path, len(content))

        return {
            "filename": resolved.name,
            "relative_path": rel_path,
            "size_bytes": len(content.encode("utf-8")),
            "extension": ext,
            "content": content,
        }

    return execute_file_read
