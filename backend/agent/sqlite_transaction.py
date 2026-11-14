"""Small SQLite transaction primitive; this module does not authorize work.

Callers must enforce identity, ownership, provenance and approval binding.
Do not use this helper to enable task execution without those checks.
"""

from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def immediate_transaction(db_path: Path, *, timeout_seconds: float = 1.0, require_existing: bool = False) -> Iterator[sqlite3.Connection]:
    """Open one bounded-wait write transaction and roll back on failure.

    Use a dedicated connection so a caller cannot accidentally commit an
    existing transaction. SQLite remains the inter-process writer lock. This
    helper does not serialize JSON conversation changes or validate a caller.
    """
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 5):
        raise ValueError("timeout_seconds must be finite and between 0 and 5")
    target = f'{Path(db_path).resolve().as_uri()}?mode=rw' if require_existing else str(db_path)
    connection = sqlite3.connect(target, timeout=timeout_seconds, isolation_level=None, uri=require_existing)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
