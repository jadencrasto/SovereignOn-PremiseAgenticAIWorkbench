"""Exclusive, opaque artifact file creation; does not authorize access."""
import os
import uuid
import re
from pathlib import Path


def create_artifact_file(sandbox_dir: Path, display_name: str):
    """Return (id, physical filename, binary stream) for a new exclusive file.

    The storage root must be a trusted directory not writable by untrusted OS
    users. On write failure the caller leaves the unique file quarantined for
    manual reconciliation; this helper never unlinks another writer's file.
    """
    root = Path(sandbox_dir)
    if root.is_symlink():
        raise ValueError('Artifact storage root cannot be a symlink')
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    artifact_id = uuid.uuid4().hex
    suffix = Path(display_name).suffix.lower()
    if len(suffix) > 16 or any(c not in '.abcdefghijklmnopqrstuvwxyz0123456789' for c in suffix):
        raise ValueError('Invalid artifact extension')
    filename = artifact_id + suffix
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(str(root / filename), flags, 0o600)
    return artifact_id, filename, os.fdopen(fd, 'wb')


def identify_orphan_candidates(sandbox_dir: Path, registered_filenames):
    """Report opaque-name candidates only; never delete or establish ownership.

    A concurrent write or incomplete metadata snapshot can produce false
    positives. Legacy/non-opaque files and symlinks are deliberately ignored.
    """
    root = Path(sandbox_dir)
    if root.is_symlink():
        raise ValueError('Artifact storage root cannot be a symlink')
    if not root.is_dir():
        return []
    known = set(registered_filenames)
    pattern = re.compile(r'^[0-9a-f]{32}(?:\.[a-z0-9]{1,15})?$')
    return sorted(item.name for item in root.iterdir()
                  if pattern.fullmatch(item.name) and item.name not in known
                  and not item.is_symlink() and item.is_file())
