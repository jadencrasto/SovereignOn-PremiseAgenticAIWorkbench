"""
backend/tools/file_write.py
-----------------------------
File write tool — creates output artifacts in data/sandbox/.

Security:
- NEVER writes outside data/sandbox/
- Filename sanitization
- Path traversal prevention
- Max content size 1 MB
- No overwrite — generates unique filename on conflict
- Mutating: requires_confirmation = True
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.safety import atomic_write_file, sanitize_filename, validate_path_within

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

def create_file_write(sandbox_dir: Path) -> callable:
    """
    Create the file_write execute function.

    Args:
        sandbox_dir: Absolute path to the sandbox directory (settings.sandbox_dir).
    """

    async def execute_file_write(args: FileWriteInput) -> dict:
        """Create an output file atomically in the sandbox directory."""
        # Validate content size
        content_bytes = args.content.encode("utf-8")
        if len(content_bytes) > _MAX_WRITE_BYTES:
            raise ValueError(
                f"Content too large ({len(content_bytes)} bytes). "
                f"Maximum: {_MAX_WRITE_BYTES} bytes."
            )

        # Sanitize filename
        safe_name = sanitize_filename(args.filename)

        # Validate path stays strictly inside sandbox_dir
        target = validate_path_within(safe_name, sandbox_dir)

        # If file exists and overwrite is False, generate unique filename
        if target.exists() and not args.overwrite:
            stem = Path(safe_name).stem
            suffix = Path(safe_name).suffix
            unique_id = uuid.uuid4().hex[:8]
            safe_name = f"{stem}_{unique_id}{suffix}"
            target = validate_path_within(safe_name, sandbox_dir)

        # Write file atomically
        atomic_write_file(target, args.content, encoding="utf-8", overwrite=True)

        rel_path = f"data/sandbox/{safe_name}"
        logger.info(
            "file_write | path=%s size=%d", rel_path, len(content_bytes)
        )

        return {
            "created_path": rel_path,
            "filename": safe_name,
            "size_bytes": len(content_bytes),
        }

    return execute_file_write
