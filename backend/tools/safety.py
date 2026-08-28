"""
backend/tools/safety.py
-----------------------
Central security functions for tool filesystem operations.

All file-based tools MUST use these functions to validate paths,
filenames, and file sizes before any I/O.

Handles Windows paths correctly (backslash normalization, drive letters).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Characters that are dangerous or illegal in filenames across OSes
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_FILENAME_LEN = 255


def validate_path_within(path: str | Path, root: Path) -> Path:
    """
    Validate that *path* resolves to a location inside *root*.

    Args:
        path: The candidate path (may be relative or absolute).
        root: The allowed root directory (must exist or be creatable).

    Returns:
        The resolved absolute Path guaranteed to be inside root.

    Raises:
        ValueError: If the path escapes the root or is otherwise unsafe.
    """
    path_str = str(path)

    # Reject null bytes
    if "\x00" in path_str:
        raise ValueError("Path contains null bytes.")

    # Reject absolute paths supplied by the model
    candidate = Path(path_str)
    if candidate.is_absolute():
        raise ValueError(
            f"Absolute paths are not allowed: '{path_str}'. "
            "Provide a relative path within the workspace."
        )

    # Reject explicit traversal components
    parts = candidate.parts
    if ".." in parts:
        raise ValueError(
            f"Path traversal ('..') is not allowed: '{path_str}'."
        )

    # Resolve against root
    resolved = (root / candidate).resolve()
    root_resolved = root.resolve()

    # Ensure the resolved path is within root
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise ValueError(
            f"Path '{path_str}' resolves outside the allowed directory."
        )

    return resolved


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename for safe storage.

    - Strips directory components.
    - Removes unsafe characters.
    - Enforces length limit.
    - Rejects empty results.

    Returns:
        A safe filename string.

    Raises:
        ValueError: If the filename is empty, too long, or entirely unsafe.
    """
    if not filename:
        raise ValueError("Filename is empty.")

    if "\x00" in filename:
        raise ValueError("Filename contains null bytes.")

    # Take only the final path component
    name = Path(filename).name

    if not name:
        raise ValueError("Filename is empty after stripping directories.")

    # Remove unsafe characters
    safe_name = _UNSAFE_FILENAME_RE.sub("_", name)

    # Collapse multiple underscores
    safe_name = re.sub(r"_+", "_", safe_name).strip("_")

    if not safe_name:
        raise ValueError(f"Filename '{filename}' contains only unsafe characters.")

    if len(safe_name) > _MAX_FILENAME_LEN:
        # Truncate but preserve extension
        stem = Path(safe_name).stem[:_MAX_FILENAME_LEN - 10]
        suffix = Path(safe_name).suffix
        safe_name = stem + suffix

    return safe_name


def check_file_size(path: Path, max_bytes: int) -> None:
    """
    Check that a file does not exceed a size limit.

    Args:
        path: Path to the file (must exist).
        max_bytes: Maximum allowed size in bytes.

    Raises:
        ValueError: If the file exceeds the limit or does not exist.
    """
    if not path.exists():
        raise ValueError(f"File does not exist: '{path.name}'")

    if not path.is_file():
        raise ValueError(f"Path is not a file: '{path.name}'")

    size = path.stat().st_size
    if size > max_bytes:
        size_mb = size / (1024 * 1024)
        limit_mb = max_bytes / (1024 * 1024)
        raise ValueError(
            f"File '{path.name}' is too large ({size_mb:.1f} MB). "
            f"Maximum allowed: {limit_mb:.1f} MB."
        )
