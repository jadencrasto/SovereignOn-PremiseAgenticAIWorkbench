"""Offline restore validation for deployment-owned source metadata.

The expected checkpoint is retained independently by deployment operations.
Neither SQLite nor this process can attest to an undeclared database rollback.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
from time import monotonic
from uuid import uuid4

from starlette.applications import Starlette

from backend.agent.sqlite_transaction import immediate_transaction, read_only_connection
from backend.multimodal.document_transport import _error


_TABLES = ('document_source_approvals', 'approved_document_sources')
_MAX_ROWS = 100_000
_MAX_SECONDS = 5
_MAX_VALUE_CHARS = 4096
_MAX_WORKERS = 1024


@dataclass(frozen=True, slots=True)
class _RecoveryState:
    generation: str
    pending: bool


@dataclass(frozen=True, slots=True)
class MaintenanceObservation:
    active: bool
    registered_workers: int | None
    awaiting_shutdown: int | None


@dataclass(frozen=True, slots=True)
class _WorkerRegistration:
    database: Path
    worker_id: str
    pid: int
    startup_generation: str
    owner_nonce: str = field(repr=False)
    app: Starlette = field(repr=False, compare=False)
    loop: asyncio.AbstractEventLoop = field(repr=False, compare=False)


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


def _read_shared_state(path: Path, *, require_drained: bool = False) -> _RecoveryState:
    try:
        with read_only_connection(path) as connection:
            connection.execute('BEGIN')
            state = _read_state(connection)
            if require_drained:
                _require_drained_for_validation(connection, state)
            else:
                _check_barrier(connection, state)
            return state
    except (OSError, sqlite3.Error):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None


def _initialize_shared_state(database: Path, path: Path, *, pending: bool, provision_database: bool = False) -> None:
    """A missing coordinator cannot bootstrap existing authority as ready."""
    if path.exists():
        return
    try:
        # Serialize first-time provisioning with catalog creation by another worker.
        with immediate_transaction(database, require_existing=not provision_database) as authority:
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
            if _maintenance_generation(connection) is not None or (observed is not None and current != observed):
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


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute('SELECT 1 FROM sqlite_schema WHERE type = ? AND name = ?', ('table', name)).fetchone() is not None


def _maintenance_generation(connection: sqlite3.Connection) -> str | None:
    catalog = connection.execute('''SELECT name, type FROM sqlite_schema
        WHERE name IN ('source_recovery_barrier', 'source_recovery_workers') ORDER BY name''').fetchall()
    if not catalog:
        return None  # Coordinators created before R8.65 have no maintenance declaration.
    if catalog != [('source_recovery_barrier', 'table'), ('source_recovery_workers', 'table')]:
        raise _error('AUTHORIZATION_UNAVAILABLE')
    rows = connection.execute('SELECT id, generation FROM source_recovery_barrier LIMIT 2').fetchall()
    if not rows:
        return None
    if len(rows) != 1 or rows[0][0] != 1 or not isinstance(rows[0][1], str) or not re.fullmatch('[0-9a-f]{32}', rows[0][1]):
        raise _error('AUTHORIZATION_UNAVAILABLE')
    return rows[0][1]


def _check_barrier(connection: sqlite3.Connection, state: _RecoveryState) -> str | None:
    generation = _maintenance_generation(connection)
    if generation is not None and (not state.pending or generation != state.generation):
        raise _error('AUTHORIZATION_UNAVAILABLE')
    return generation


def _require_drained_for_validation(connection: sqlite3.Connection, state: _RecoveryState) -> None:
    if _check_barrier(connection, state) is not None or (
        _has_table(connection, 'source_recovery_workers')
        and connection.execute('SELECT 1 FROM source_recovery_workers LIMIT 1').fetchone() is not None
    ):
        raise _error('AUTHORIZATION_UNAVAILABLE')


def _ensure_maintenance_tables(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS source_recovery_barrier (
        id INTEGER PRIMARY KEY CHECK (id = 1), generation TEXT NOT NULL
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS source_recovery_workers (
        worker_id TEXT PRIMARY KEY NOT NULL, pid INTEGER NOT NULL, startup_generation TEXT NOT NULL,
        nonce_hash TEXT NOT NULL, stopped_generation TEXT
    )''')


def _nonce_hash(worker: _WorkerRegistration) -> str:
    return hashlib.sha256(worker.owner_nonce.encode('ascii')).hexdigest()


def register_recovery_worker(app: Starlette) -> _WorkerRegistration:
    """Admit the owning lifespan before it opens the application database."""
    if getattr(app.state, 'source_recovery_registration', None) is not None:
        raise _error('AUTHORIZATION_UNAVAILABLE')
    loop = asyncio.get_running_loop()
    database = Path(app.state.settings.tasks_db_path).resolve()
    path = _coordination_path(database)
    declared = getattr(app.state.settings, 'document_source_restore_pending', False)
    _initialize_shared_state(database, path, pending=bool(declared), provision_database=True)
    try:
        with immediate_transaction(path, require_existing=True) as connection:
            state = _read_state(connection)
            if _check_barrier(connection, state) is not None:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            _ensure_maintenance_tables(connection)
            if connection.execute('SELECT COUNT(*) FROM source_recovery_workers').fetchone()[0] >= _MAX_WORKERS:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            worker = _WorkerRegistration(database, uuid4().hex, os.getpid(), state.generation, uuid4().hex, app, loop)
            connection.execute('INSERT INTO source_recovery_workers VALUES (?, ?, ?, ?, NULL)', (
                worker.worker_id, worker.pid, worker.startup_generation, _nonce_hash(worker),
            ))
    except (OSError, sqlite3.Error):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None
    app.state.source_recovery_registration = worker
    app.state.source_recovery_documents_installed = False
    return worker


def acknowledge_recovery_worker_shutdown(app: Starlette, worker: _WorkerRegistration) -> None:
    """Record an owning lifespan's completed shutdown. Absence or expiry proves nothing."""
    from backend.agent.deployment_documents import inspect_document_worker

    if (worker.app is not app or worker.loop is not asyncio.get_running_loop()
            or getattr(app.state, 'source_recovery_registration', None) is not worker
            or worker.pid != os.getpid() or getattr(app.state, 'startup_complete', None) is not False
            or getattr(app.state, 'deployment_documents', None) is not None):
        raise _error('AUTHORIZATION_UNAVAILABLE')
    if app.state.source_recovery_documents_installed or getattr(app.state, 'document_recovery_worker', None) is not None:
        observed = inspect_document_worker(app)
        if (observed.worker_id != worker.worker_id or observed.phase != 'stopped'
                or observed.observed_connections != 0 or observed.sqlite_activity != 'unverified'):
            raise _error('AUTHORIZATION_UNAVAILABLE')
    path = _coordination_path(worker.database)
    try:
        with immediate_transaction(path, require_existing=True) as connection:
            generation = _check_barrier(connection, _read_state(connection))
            owned = (worker.worker_id, worker.pid, worker.startup_generation, _nonce_hash(worker))
            match = 'worker_id = ? AND pid = ? AND startup_generation = ? AND nonce_hash = ? AND stopped_generation IS NULL'
            if generation is None:
                cursor = connection.execute(f'DELETE FROM source_recovery_workers WHERE {match}', owned)
            else:
                cursor = connection.execute(
                    f'UPDATE source_recovery_workers SET stopped_generation = ? WHERE {match}', (generation, *owned),
                )
            if cursor.rowcount != 1:
                raise _error('AUTHORIZATION_UNAVAILABLE')
    except (OSError, sqlite3.Error):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None
    app.state.source_recovery_registration = None


def inspect_recovery_maintenance(database: Path, state: _RecoveryState) -> MaintenanceObservation:
    """Read a generation-bound snapshot. An empty roster grants no authority."""
    try:
        with read_only_connection(_coordination_path(database)) as connection:
            connection.execute('BEGIN')
            if _read_state(connection) != state:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            generation = _check_barrier(connection, state)
            if not _has_table(connection, 'source_recovery_workers'):
                if generation is not None:
                    raise _error('AUTHORIZATION_UNAVAILABLE')
                return MaintenanceObservation(False, None, None)
            total, awaiting = connection.execute('''SELECT COUNT(*), COUNT(CASE
                WHEN stopped_generation IS NULL OR stopped_generation IS NOT ? THEN 1 END)
                FROM source_recovery_workers''', (generation,)).fetchone()
            if total > _MAX_WORKERS:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            return MaintenanceObservation(generation is not None, total, awaiting)
    except (OSError, sqlite3.Error):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None


def set_recovery_maintenance(database: Path, expected_generation: str, *, active: bool) -> None:
    """Operator-only barrier transition. Quarantine can never be cleared here."""
    try:
        with immediate_transaction(_coordination_path(database), require_existing=True) as connection:
            state = _read_state(connection)
            generation = _check_barrier(connection, state)
            if not state.pending or state.generation != expected_generation or (generation is not None) == active:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            _ensure_maintenance_tables(connection)
            if active:
                connection.execute('INSERT INTO source_recovery_barrier VALUES (1, ?)', (state.generation,))
            else:
                connection.execute('DELETE FROM source_recovery_workers WHERE stopped_generation = ?', (generation,))
                connection.execute('DELETE FROM source_recovery_barrier WHERE id = 1')
                # A cancelled window must never share its generation with a later
                # window or with a validator that started before preparation.
                connection.execute('UPDATE source_restore_state SET generation = ? WHERE id = 1', (uuid4().hex,))
    except (OSError, sqlite3.Error):
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


def _checkpoint_connection(connection: sqlite3.Connection, deadline: float) -> str:
    """Hash the catalogs using the caller's pinned SQLite snapshot."""
    digest = hashlib.sha256(b'sovereign-document-source-restore-v1\n')

    def add(values):
        if (monotonic() >= deadline or any(type(value) not in (str, int, type(None))
                or (isinstance(value, str) and len(value) > _MAX_VALUE_CHARS) for value in values)):
            raise ValueError('Invalid source authorization metadata')
        digest.update(json.dumps(values, ensure_ascii=True, separators=(',', ':')).encode('ascii') + b'\n')

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
    return digest.hexdigest()


def _checkpoint(database: Path) -> str:
    deadline = monotonic() + _MAX_SECONDS
    try:
        with read_only_connection(database) as connection:
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 128 * 1024)
            connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            # Keep both tables in the same committed snapshot, including under WAL.
            connection.execute('BEGIN')
            return _checkpoint_connection(connection, deadline)
    except (OSError, sqlite3.Error, ValueError):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None


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
    state = _read_shared_state(_coordination_path(database), require_drained=True)
    if (not state.pending or type(trusted_checkpoint) is not str
            or re.fullmatch('[0-9a-f]{64}', trusted_checkpoint) is None
            or not hmac.compare_digest(_checkpoint(database), trusted_checkpoint)):
        raise _error('AUTHORIZATION_UNAVAILABLE')
    ready_generation = uuid4().hex
    try:
        with immediate_transaction(_coordination_path(database), require_existing=True) as connection:
            if _read_state(connection) != state:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            _require_drained_for_validation(connection, state)
            connection.execute('UPDATE source_restore_state SET generation = ?, pending = 0 WHERE id = 1',
                               (ready_generation,))
    except (OSError, sqlite3.Error):
        raise _error('AUTHORIZATION_UNAVAILABLE') from None
    app.state.document_source_validated_generation = ready_generation
