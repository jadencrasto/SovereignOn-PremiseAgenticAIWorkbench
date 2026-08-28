"""
backend/tools/file_list.py
---------------------------
File listing tool — lists files inside the controlled workspace.

Scope: data/uploads/ only.
Security: path traversal prevention, no absolute paths, no escape.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.tools.safety import validate_path_within

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

class FileListInput(BaseModel):
    """Input schema for the file_list tool."""
    directory: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Optional relative subdirectory inside the uploads workspace. "
                    "Leave empty to list the root uploads directory.",
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_file_list(upload_dir: Path) -> callable:
    """
    Create the file_list execute function.

    Args:
        upload_dir: Absolute path to the uploads directory (settings.upload_dir).
    """

    async def execute_file_list(args: FileListInput) -> List[dict]:
        """List files in the controlled workspace directory."""
        # Determine target directory
        if args.directory:
            target = validate_path_within(args.directory, upload_dir)
        else:
            target = upload_dir.resolve()

        if not target.exists():
            return []

        if not target.is_dir():
            raise ValueError(f"'{args.directory}' is not a directory.")

        results = []
        for item in sorted(target.iterdir()):
            if item.is_file():
                try:
                    rel = item.relative_to(upload_dir.resolve())
                    results.append({
                        "filename": item.name,
                        "relative_path": str(rel).replace("\\", "/"),
                        "size_bytes": item.stat().st_size,
                        "extension": item.suffix.lower(),
                    })
                except (ValueError, OSError):
                    # Skip files that fail (e.g. permission issues)
                    continue

        logger.info("file_list | dir=%s files=%d", target, len(results))
        return results

    return execute_file_list
