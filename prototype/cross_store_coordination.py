"""Synthetic coordination fixture; never a production storage authority.

SQLite locks only cooperating clients of the same unchanged database. The file
observations below do not establish safe Windows ancestry, ACLs or freshness.
This module is not connected to application writers or authorization gates.
"""
from contextlib import contextmanager
import math
import os
from pathlib import Path
import sqlite3
import stat


class UnsupportedIdentity(RuntimeError):
    pass


def observe_file(path):
    """Compare an opened regular file with its current final path component.

    This observation is not safe against intermediate junctions, prior writable
    handles, malicious path races, inode reuse, or replacement after returning.
    """
    path = Path(path)
    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode) or getattr(before, 'st_file_attributes', 0) & 0x400:
        raise UnsupportedIdentity('reparse_or_symlink')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try:
        observed = os.fstat(fd)
        current = os.lstat(path)
        if (not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or
            not observed.st_ino or not observed.st_dev or
            stat.S_ISLNK(current.st_mode) or getattr(current, 'st_file_attributes', 0) & 0x400 or
            (before.st_dev, before.st_ino) != (observed.st_dev, observed.st_ino) or
            (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino)):
            raise UnsupportedIdentity('identity_unavailable_or_changed')
        return observed.st_dev, observed.st_ino
    finally:
        os.close(fd)


class SyntheticCoordinator:
    """Single synthetic global lock; does not protect any real store."""
    def __init__(self, database):
        self.database = Path(database)
        self._connection = None

    @contextmanager
    def exclusive(self, timeout=0.2):
        if self._connection is not None:
            raise RuntimeError('lock_order_violation')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 1:
            raise ValueError('invalid_timeout')
        # Must already exist. No automatic creation of a replacement database.
        if not self.database.is_file():
            raise UnsupportedIdentity('coordination_database_missing')
        conn = sqlite3.connect(self.database.resolve().as_uri() + '?mode=rw', uri=True, timeout=timeout, isolation_level=None)
        try:
            conn.execute('BEGIN IMMEDIATE')
            self._connection = conn
            yield self
        finally:
            self._connection = None
            try:
                if conn.in_transaction:
                    conn.rollback()
            finally:
                conn.close()

    def observe(self, path, expected):
        if self._connection is None:
            raise RuntimeError('global_lock_required')
        if (not isinstance(expected, tuple) or len(expected) != 2 or
            any(type(item) is not int or item <= 0 for item in expected)):
            raise UnsupportedIdentity('expected_identity_missing')
        if observe_file(path) != expected:
            raise UnsupportedIdentity('identity_changed')
        return 'OBSERVED_UNVERIFIED'


def production_coordination_available():
    """No caller may infer production security from this fixture."""
    return False
