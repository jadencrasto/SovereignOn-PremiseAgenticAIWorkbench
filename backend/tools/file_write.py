"""
backend/tools/file_write.py
-----------------------------
File write tool — creates output artifacts in data/sandbox/.

Security: filenames are sanitized and files use exclusive opaque names.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.safety import sanitize_filename
from backend.tools.artifact_storage import create_artifact_file

logger = logging.getLogger(__name__)

# Maximum write size: 1 MB
_MAX_WRITE_BYTES = 1 * 1024 * 1024


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

class FileWriteInput(BaseModel):
    """Input schema for the file_write tool."""
    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Name for the output file, e.g. 'analysis_results.txt'.",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Text content to write to the file.",
    )
    overwrite: bool = Field(
        default=False,
        description="Whether to overwrite the file if it already exists.",
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _validate_content_quality(content: str) -> None:
    """Validate that written content is non-empty and not a generic placeholder."""
    import re
    stripped = content.strip()
    if not stripped:
        raise ValueError("File content cannot be empty or whitespace-only.")

    lower = stripped.lower()
    # Reject standalone placeholder tokens
    if lower in {"text", "summary", "placeholder", "content", "todo", "test", "none", "null", "undefined"}:
        raise ValueError(f"File content '{stripped}' is a placeholder token, not valid content.")

    # Reject common placeholder template patterns
    if re.match(r"^(text\s*\n*)?summary of [a-z0-9_\-\s]+ found in documents\.?$", stripped, re.IGNORECASE):
        raise ValueError(f"File content is an unfilled placeholder template: '{stripped}'")

    if re.match(r"^\[(?:insert|placeholder|enter|todo)\b.*\]$", stripped, re.IGNORECASE):
        raise ValueError(f"File content contains an unfilled template marker: '{stripped}'")


def create_file_write(sandbox_dir: Path) -> callable:
    """
    Create the file_write execute function.

    Args:
        sandbox_dir: Absolute path to the sandbox directory (settings.sandbox_dir).
    """

    async def execute_file_write(args: FileWriteInput) -> dict:
        """Create an exclusive output file in the sandbox directory."""
        # Validate content quality
        _validate_content_quality(args.content)

        # Validate content size
        content_bytes = args.content.encode("utf-8")
        if len(content_bytes) > _MAX_WRITE_BYTES:
            raise ValueError(
                f"Content too large ({len(content_bytes)} bytes). "
                f"Maximum: {_MAX_WRITE_BYTES} bytes."
            )

        # Sanitize filename
        safe_name = sanitize_filename(args.filename)

        display_name = safe_name
        artifact_id, safe_name, stream = create_artifact_file(sandbox_dir, display_name)
        target = sandbox_dir.resolve() / safe_name
        with stream:
            stream.write(content_bytes)
            stream.flush()
            import os
            os.fsync(stream.fileno())

        rel_path = f"data/sandbox/{safe_name}"

        # Post-write validation
        if not target.exists():
            raise RuntimeError(f"Target file {rel_path} was not created on filesystem.")
        if target.stat().st_size == 0:
            raise RuntimeError(f"Target file {rel_path} is empty on filesystem.")

        logger.info(
            "file_write | path=%s size=%d", rel_path, len(content_bytes)
        )

        return {
            "created_path": rel_path,
            "filename": safe_name,
            "artifact_id": artifact_id,
            "display_name": display_name,
            "size_bytes": len(content_bytes),
        }

    return execute_file_write
