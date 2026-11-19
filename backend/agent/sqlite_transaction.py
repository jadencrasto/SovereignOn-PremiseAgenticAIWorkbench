"""Small SQLite transaction primitive; this module does not authorize work.

Callers must enforce identity, ownership, provenance and approval binding.
Do not use this helper to enable task execution without those checks.
"""

from __future__ import annotations

import math
import sqlite3
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class SQLiteActivity:
    open_connections: int = 0
    close_failed: bool = False


_sqlite_activity: ContextVar[SQLiteActivity | None] = ContextVar('sqlite_activity', default=None)


@contextmanager
def observe_sqlite_activity(activity: SQLiteActivity) -> Iterator[None]:
    token = _sqlite_activity.set(activity)
    try:
        yield
    finally:
        _sqlite_activity.reset(token)


@contextmanager
def _managed_connection(target: str, **options) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(target, **options)
    activity = _sqlite_activity.get()
    if activity is not None:
        activity.open_connections += 1
    try:
        yield connection
    finally:
        try:
            connection.close()
        except BaseException:
            if activity is not None:
                activity.close_failed = True
            raise
        finally:
            if activity is not None:
                activity.open_connections -= 1


def read_only_connection(path: Path) -> AbstractContextManager[sqlite3.Connection]:
    return _managed_connection(f'{path.as_uri()}?mode=ro', timeout=1.0, uri=True)


@contextmanager
def immediate_transaction(db_path: Path, *, timeout_seconds: float = 1.0, require_existing: bool = False,
                          full_sync: bool = False) -> Iterator[sqlite3.Connection]:
    """Open one bounded-wait write transaction and roll back on failure.

    Use a dedicated connection so a caller cannot accidentally commit an
    existing transaction. SQLite remains the inter-process writer lock. This
    helper does not serialize JSON conversation changes or validate a caller.
    """
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 5):
        raise ValueError("timeout_seconds must be finite and between 0 and 5")
    target = f'{Path(db_path).resolve().as_uri()}?mode=rw' if require_existing else str(db_path)
    with _managed_connection(target, timeout=timeout_seconds, isolation_level=None, uri=require_existing) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        if full_sync:
            connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
