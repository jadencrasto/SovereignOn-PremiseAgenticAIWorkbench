"""Offline restore validation for deployment-owned source metadata.

The expected checkpoint is retained independently by deployment operations.
Neither SQLite nor this process can attest to an undeclared database rollback.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
from time import monotonic
from uuid import uuid4

from starlette.applications import Starlette

from backend.agent.sqlite_transaction import immediate_transaction
from backend.multimodal.document_transport import _error


_TABLES = ('document_source_approvals', 'approved_document_sources')
_MAX_ROWS = 100_000
_MAX_SECONDS = 5
_MAX_VALUE_CHARS = 4096


@dataclass(frozen=True, slots=True)
class _RecoveryState:
    generation: str
    pending: bool


def _coordination_path(database: Path) -> Path:
    return database.with_name(database.name + '.source-restore.sqlite3')


def _read_state(connection: sqlite3.Connection) -> _RecoveryState:
    rows = connection.execute(
        'SELECT id, generation, pending FROM source_restore_state LIMIT 2',
    ).fetchall()
    if len(rows) != 1:
        raise _error('AUTHORIZATION_UNAVAILABLE')
    key, generation, pending = rows[0]
    if (key != 1 or type(generation) is not str or not re.fullmatch('[0-9a-f]{32}', generation)
            or type(pending) is not int or pending not in (0, 1)):
        raise _error('AUTHORIZATION_UNAVAILABLE')
    return _RecoveryState(generation, bool(pending))


def _read_shared_state(path: Path) -> _RecoveryState:
    try:
        with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', timeout=1.0, uri=True)) as connection:
            return _read_state(connection)
    except (OSError, sqlite3.Error):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None


def _initialize_shared_state(database: Path, path: Path, *, pending: bool) -> None:
    """A missing coordinator cannot bootstrap existing authority as ready."""
    if path.exists():
        return
    try:
        # Serialize first-time provisioning with catalog creation by another worker.
        with immediate_transaction(database, require_existing=True) as authority:
            if path.exists():
                return
            if not pending and authority.execute(
                'SELECT 1 FROM sqlite_schema WHERE name IN (?, ?) LIMIT 1', _TABLES,
            ).fetchone():
                raise _error('AUTHORIZATION_UNAVAILABLE')
            with immediate_transaction(path) as connection:
                connection.execute(
                    """CREATE TABLE source_restore_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        generation TEXT NOT NULL,
                        pending INTEGER NOT NULL CHECK (pending IN (0, 1))
                    )""",
                )
                connection.execute('INSERT INTO source_restore_state VALUES (1, ?, ?)', (uuid4().hex, int(pending)))
    except (OSError, sqlite3.Error):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None


def _declare_restore(database: Path, *, observed: _RecoveryState | None = None) -> _RecoveryState:
    path = _coordination_path(database)
    _initialize_shared_state(database, path, pending=True)
    state = _RecoveryState(uuid4().hex, True)
    try:
        with immediate_transaction(path, require_existing=True) as connection:
            current = _read_state(connection)
            if observed is not None and current != observed:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            connection.execute('UPDATE source_restore_state SET generation = ?, pending = 1 WHERE id = 1',
                               (state.generation,))
    except (OSError, sqlite3.Error):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None
    return state


def _configured_state(app: Starlette, database: Path) -> _RecoveryState:
    path = _coordination_path(database)
    declared = getattr(app.state.settings, 'document_source_restore_pending', False)
    _initialize_shared_state(database, path, pending=bool(declared))
    state = _read_shared_state(path)
    if (declared and not state.pending
            and getattr(app.state, 'document_source_validated_generation', None) != state.generation):
        # A startup flag must not replace a declaration or recovery committed
        # by another worker since this worker read the generation.
        return _declare_restore(database, observed=state)
    return state


def _database_path(app: Starlette) -> Path:
    try:
        return Path(app.state.settings.tasks_db_path).resolve(strict=True)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None


def _offline_database(app: Starlette) -> Path:
    if getattr(app.state, 'startup_complete', False):
        raise _error('AUTHORIZATION_UNAVAILABLE')
    return _database_path(app)


class SourceRestoreBinding:
    """Bind deployment authority to the current, non-quarantined recovery state."""

    def __init__(self, app: Starlette, database: Path):
        self._app = app
        self._configured_path = app.state.settings.tasks_db_path
        self._path = _coordination_path(database)
        self._state = _configured_state(app, database)
        if self._state.pending:
            raise _error('AUTHORIZATION_UNAVAILABLE')

    def check(self) -> None:
        if (self._app.state.settings.tasks_db_path != self._configured_path
                or (getattr(self._app.state.settings, 'document_source_restore_pending', False)
                    and getattr(self._app.state, 'document_source_validated_generation', None) != self._state.generation)
                or _read_shared_state(self._path) != self._state):
            raise _error('AUTHORIZATION_UNAVAILABLE')


def quarantine_source_authority(app: Starlette) -> None:
    """Commit shared quarantine and invalidate all pre-restore bindings."""
    _declare_restore(_database_path(app))


def _checkpoint(database: Path) -> str:
    deadline = monotonic() + _MAX_SECONDS
    digest = hashlib.sha256(b'sovereign-document-source-restore-v1\n')

    def add(values):
        if (monotonic() >= deadline or any(type(value) not in (str, int, type(None))
                or (isinstance(value, str) and len(value) > _MAX_VALUE_CHARS) for value in values)):
            raise ValueError('Invalid source authorization metadata')
        digest.update(json.dumps(values, ensure_ascii=True, separators=(',', ':')).encode('ascii') + b'\n')

    try:
        with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', timeout=1.0, uri=True)) as connection:
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 128 * 1024)
            connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            # Keep both tables in the same committed snapshot, including under WAL.
            connection.execute('BEGIN')
            for table in _TABLES:
                schema = connection.execute(
                    'SELECT type, name, sql FROM sqlite_schema WHERE tbl_name = ? ORDER BY type, name LIMIT 129',
                    (table,),
                ).fetchall()
                if (not 1 <= len(schema) <= 128
                        or not any(kind == 'table' and name == table and type(sql) is str for kind, name, sql in schema)):
                    raise ValueError('Missing source authorization metadata')
                add((table,))
                for definition in schema:
                    add(definition)
                rows = connection.execute(
                    f'SELECT * FROM {table} ORDER BY reference COLLATE BINARY LIMIT ?', (_MAX_ROWS + 1,),
                )
                for count, row in enumerate(rows, 1):
                    if count > _MAX_ROWS:
                        raise ValueError('Source authorization metadata limit')
                    add(row)
            if monotonic() >= deadline:
                raise ValueError('Source authorization metadata deadline')
    except (OSError, sqlite3.Error, ValueError):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None
    return digest.hexdigest()


def capture_source_checkpoint(app: Starlette) -> str:
    """Capture known-current metadata during coordinated offline maintenance."""
    database = _offline_database(app)
    binding = SourceRestoreBinding(app, database)
    digest = _checkpoint(database)
    binding.check()
    return digest


def validate_source_restore(app: Starlette, *, trusted_checkpoint: str | None) -> None:
    """Clear an existing shared quarantine using the independently retained checkpoint.

    Deployment must stop every worker before restoring or validating the file.
    This operation never declares a new restore.
    """
    database = _offline_database(app)
    state = _read_shared_state(_coordination_path(database))
    if (not state.pending or type(trusted_checkpoint) is not str
            or re.fullmatch('[0-9a-f]{64}', trusted_checkpoint) is None
            or not hmac.compare_digest(_checkpoint(database), trusted_checkpoint)):
        raise _error('AUTHORIZATION_UNAVAILABLE')
    ready_generation = uuid4().hex
    try:
        with immediate_transaction(_coordination_path(database), require_existing=True) as connection:
            if _read_state(connection) != state:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            connection.execute('UPDATE source_restore_state SET generation = ?, pending = 0 WHERE id = 1',
                               (ready_generation,))
    except (OSError, sqlite3.Error):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None
    app.state.document_source_validated_generation = ready_generation
