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

# Windows reserved device names (case-insensitive)
_RESERVED_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def validate_path_within(path: str | Path, root: Path) -> Path:
    """
    Validate that *path* resolves to a location strictly inside *root*.

    Hardening (Phase 7):
        - Rejects null bytes
        - Rejects UNC network paths (\\\\server\\share)
        - Rejects drive-letter absolute paths (C:\\...)
        - Rejects Windows reserved device names (CON, PRN, NUL, COM1-9, etc.)
        - Rejects directory traversal (..)
        - Rejects symlinks across the entire path hierarchy
        - Post-join resolution and boundary verification

    Args:
        path: The candidate path.
        root: The allowed root directory.

    Returns:
        The resolved absolute Path guaranteed to be inside root.

    Raises:
        ValueError: If the path is unsafe, invalid, or escapes the root.
    """
    path_str = str(path).strip()

    # Reject null bytes
    if "\x00" in path_str:
        raise ValueError("Path contains null bytes.")

    # Reject UNC paths
    if path_str.startswith(r"\\") or path_str.startswith("//"):
        raise ValueError(f"UNC network paths are not allowed: '{path_str}'")

    # Reject Windows drive letters
    if re.match(r"^[a-zA-Z]:", path_str):
        raise ValueError(f"Absolute drive-letter paths are not allowed: '{path_str}'")

    candidate = Path(path_str)

    # Reject absolute paths
    if candidate.is_absolute():
        raise ValueError(
            f"Absolute paths are not allowed: '{path_str}'. "
            "Provide a relative path within the workspace."
        )

    # Reject explicit traversal components and reserved device names
    parts = candidate.parts
    for part in parts:
        if part == "..":
            raise ValueError(f"Path traversal ('..') is not allowed: '{path_str}'.")

        # Check for reserved device names
        stem = part.upper().split(".")[0]
        if stem in _RESERVED_DEVICE_NAMES:
            raise ValueError(
                f"Reserved device name '{part}' is not allowed in paths."
            )

    root_resolved = root.resolve()
    resolved = (root / candidate).resolve()

    # Ensure resolved path is strictly within root
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise ValueError(
            f"Path '{path_str}' resolves outside the allowed directory."
        )

    # Symlink escape prevention: check path and all existing parents
    curr = resolved
    while curr != root_resolved and curr != curr.parent:
        if curr.is_symlink():
            raise ValueError(f"Symlinks are not allowed in sandbox paths: '{curr}'")
        curr = curr.parent

    return resolved


def atomic_write_file(
    target_path: Path,
    content: str,
    encoding: str = "utf-8",
    overwrite: bool = False,
) -> int:
    """
    Atomically write content to target_path using a temporary file and os.replace().

    Args:
        target_path: Destination file path.
        content: String content to write.
        encoding: File encoding (default utf-8).
        overwrite: If False, raises ValueError if target already exists.

    Returns:
        Number of characters written.

    Raises:
        ValueError: If file exists and overwrite is False.
    """
    import os
    import tempfile

    if target_path.exists() and not overwrite:
        raise ValueError(
            f"File '{target_path.name}' already exists. Set 'overwrite: true' to replace it."
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in the same directory to guarantee atomic rename
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(target_path.parent),
        encoding=encoding,
        delete=False,
    )
    temp_path = Path(temp_file.name)
    try:
        temp_file.write(content)
        temp_file.flush()
        temp_file.close()

        # Atomic replacement
        os.replace(temp_path, target_path)
        return len(content)
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


# Characters that are dangerous or illegal in filenames across OSes
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_FILENAME_LEN = 255


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
