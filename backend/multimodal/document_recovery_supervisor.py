"""Coordinator-backed admission for a trusted, locally composed process owner.

The boundary must own the process handles and disable autonomous starts. No
production boundary, process launcher, or remote evidence transport lives here.
"""
from contextlib import ExitStack, contextmanager
import asyncio
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import sqlite3
from time import monotonic, sleep
from typing import Protocol
from uuid import uuid4

from backend.agent.sqlite_transaction import immediate_transaction, read_only_connection
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal.document_transport import _error
from backend.security.provenance_windows_native import WindowsKeyStoreNative


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    created: int


@dataclass(frozen=True, slots=True)
class WorkerAdmission:
    database: Path
    nonce: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class OwnedWorker:
    identity: ProcessIdentity
    running: bool


@dataclass(frozen=True, slots=True)
class SupervisorSnapshot:
    database: Path
    owner: ProcessIdentity
    ownership_complete: bool
    autonomous_restart_disabled: bool
    workers: tuple[OwnedWorker, ...]


class OwnedProcessBoundary(Protocol):
    """Local trusted adapter for a fixed backend worker, using owned handles.

    Report every owned process, including descendants and retained exit facts.
    Launch returns after creation, without waiting for application admission.
    Shutdown is graceful and bounded. A PID lookup cannot replace an owned handle.
    """

    def observe(self) -> SupervisorSnapshot: ...
    def launch(self, admission: WorkerAdmission, *, timeout_seconds: float) -> ProcessIdentity: ...
    def shutdown(self, identity: ProcessIdentity, *, timeout_seconds: float) -> None: ...


def _unavailable():
    return _error('AUTHORIZATION_UNAVAILABLE')


def _process_identity() -> ProcessIdentity:
    try:
        return ProcessIdentity(os.getpid(), WindowsKeyStoreNative().current_process_creation_time())
    except (AttributeError, OSError, RuntimeError, ValueError):
        raise _unavailable() from None


def _valid_identity(identity: ProcessIdentity) -> bool:
    return (type(identity) is ProcessIdentity and type(identity.pid) is int and 0 < identity.pid <= 0xFFFFFFFF
            and type(identity.created) is int and 0 < identity.created <= 0x7FFF_FFFF_FFFF_FFFF)


def _live_owner(identity: ProcessIdentity) -> bool:
    try:
        return WindowsKeyStoreNative().live_process_matches(identity.pid, identity.created)
    except (AttributeError, OSError, RuntimeError, ValueError):
        raise _unavailable() from None


def _authorize_operator(database: Path) -> str:
    from backend.multimodal import document_source_maintenance as cli

    api = WindowsKeyStoreNative()
    owner = cli._local_operator(api)
    cli._safe_path(database)
    if not database.is_file():
        raise _unavailable()
    with ExitStack() as handles:
        cli._guard_location(handles, api, recovery._coordination_path(database), owner, purpose='control')
    return owner


@contextmanager
def _write(database: Path):
    try:
        with immediate_transaction(recovery._coordination_path(database), require_existing=True) as connection:
            yield connection
    except (OSError, sqlite3.Error):
        raise _unavailable() from None


def _owner(connection):
    names = connection.execute('''SELECT name, type FROM sqlite_schema
        WHERE name IN ('source_recovery_supervisor', 'source_recovery_supervised_workers') ORDER BY name''').fetchall()
    if not names:
        return None
    if names != [('source_recovery_supervised_workers', 'table'), ('source_recovery_supervisor', 'table')]:
        raise _unavailable()
    rows = connection.execute('''SELECT id, token_hash, pid, created, locked_generation
        FROM source_recovery_supervisor LIMIT 2''').fetchall()
    if len(rows) != 1:
        raise _unavailable()
    key, token_hash, pid, created, generation = rows[0]
    if (key != 1 or type(token_hash) is not str or not re.fullmatch('[0-9a-f]{64}', token_hash)
            or not _valid_identity(ProcessIdentity(pid, created))
            or (generation is not None and (type(generation) is not str or not re.fullmatch('[0-9a-f]{32}', generation)))):
        raise _unavailable()
    return token_hash, pid, created, generation


def _hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode('ascii')).hexdigest()


def _workers(connection):
    rows = connection.execute('''SELECT pid, created, worker_id, drained
        FROM source_recovery_supervised_workers LIMIT ?''', (recovery._MAX_WORKERS + 1,)).fetchall()
    if len(rows) > recovery._MAX_WORKERS:
        raise _unavailable()
    return rows


def _unowned_registrations(connection) -> bool:
    return connection.execute('''SELECT 1 FROM source_recovery_workers AS worker WHERE NOT EXISTS (
        SELECT 1 FROM source_recovery_supervised_workers AS owned WHERE owned.worker_id = worker.worker_id
    ) LIMIT 1''').fetchone() is not None


class DeploymentSupervisor:
    """An exclusive local coordination identity, retained on uncertain exit."""

    def __init__(self, database: Path, boundary: OwnedProcessBoundary):
        _authorize_operator(database)
        self._database = database.resolve(strict=True)
        self._boundary = boundary
        self._identity = _process_identity()
        self._owner_nonce = uuid4().hex
        if not _valid_identity(self._identity) or self._observe():
            raise _unavailable()
        with _write(self._database) as connection:
            state = recovery._read_state(connection)
            if state.pending or recovery._check_barrier(connection, state) is not None or _owner(connection) is not None:
                raise _unavailable()
            recovery._ensure_maintenance_tables(connection)
            if connection.execute('SELECT 1 FROM source_recovery_workers LIMIT 1').fetchone() is not None:
                raise _unavailable()
            connection.execute('''CREATE TABLE source_recovery_supervisor (
                id INTEGER PRIMARY KEY CHECK (id = 1), token_hash TEXT NOT NULL,
                pid INTEGER NOT NULL, created INTEGER NOT NULL, locked_generation TEXT
            )''')
            connection.execute('''CREATE TABLE source_recovery_supervised_workers (
                token_hash TEXT PRIMARY KEY NOT NULL, generation TEXT NOT NULL,
                pid INTEGER, created INTEGER, worker_id TEXT UNIQUE, drained INTEGER NOT NULL DEFAULT 0,
                ready INTEGER NOT NULL DEFAULT 0 CHECK (ready IN (0, 1)),
                UNIQUE (pid, created)
            )''')
            connection.execute('INSERT INTO source_recovery_supervisor VALUES (1, ?, ?, ?, NULL)',
                               (_hash(self._owner_nonce), self._identity.pid, self._identity.created))

    def _observe(self) -> dict[ProcessIdentity, bool]:
        try:
            view = self._boundary.observe()
        except Exception:
            raise _unavailable() from None
        if (type(view) is not SupervisorSnapshot or view.database != self._database or view.owner != self._identity
                or _process_identity() != self._identity or view.ownership_complete is not True
                or view.autonomous_restart_disabled is not True or type(view.workers) is not tuple
                or len(view.workers) > recovery._MAX_WORKERS):
            raise _unavailable()
        result = {}
        for worker in view.workers:
            if (type(worker) is not OwnedWorker or not _valid_identity(worker.identity)
                    or type(worker.running) is not bool or worker.identity == self._identity or worker.identity in result):
                raise _unavailable()
            result[worker.identity] = worker.running
        return result

    def _window(self, connection, generation: str | None = None):
        state = recovery._read_state(connection)
        barrier = recovery._check_barrier(connection, state)
        owner = (_hash(self._owner_nonce), self._identity.pid, self._identity.created, generation)
        if (_owner(connection) != owner or _process_identity() != self._identity or barrier != generation
                or (generation is None and state.pending) or _unowned_registrations(connection)):
            raise _unavailable()
        return state

    def _census(self, rows):
        view = self._observe()
        if set(view) != {ProcessIdentity(pid, created) for pid, created, _, _ in rows}:
            raise _unavailable()  # Includes interrupted launch, PID reuse and any unexpected descendant.
        return view

    def start_worker(self) -> ProcessIdentity:
        admission = WorkerAdmission(self._database, uuid4().hex)
        token_hash = _hash(admission.nonce)
        with _write(self._database) as connection:
            state = self._window(connection)
            rows = _workers(connection)
            if len(rows) >= recovery._MAX_WORKERS:
                raise _unavailable()
            self._census(rows)
            connection.execute('INSERT INTO source_recovery_supervised_workers (token_hash, generation) VALUES (?, ?)',
                               (token_hash, state.generation))
        # Preserve the committed reservation if launch or this process fails. The
        # bounded creation handoff shares the same writer lock as operator prepare.
        with _write(self._database) as connection:
            if self._window(connection) != state:
                raise _unavailable()
            if connection.execute('''SELECT 1 FROM source_recovery_supervised_workers
                WHERE token_hash = ? AND generation = ? AND pid IS NULL''', (token_hash, state.generation)).fetchone() is None:
                raise _unavailable()
            self._census([row for row in _workers(connection) if row[0] is not None])
            deadline = monotonic() + 5
            try:
                identity = self._boundary.launch(admission, timeout_seconds=5)
            except Exception:
                raise _unavailable() from None
            if (monotonic() >= deadline or not _valid_identity(identity) or identity == self._identity
                    or self._observe().get(identity) is not True):
                raise _unavailable()
            connection.execute('UPDATE source_recovery_supervised_workers SET pid = ?, created = ? WHERE token_hash = ?',
                               (identity.pid, identity.created, token_hash))
            self._census(_workers(connection))
        return identity

    def wait_for_worker_ready(self, identity: ProcessIdentity, *, timeout_seconds: int = 15) -> None:
        """Observe listener readiness from the admitted, still-owned process."""
        if not _valid_identity(identity) or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise _unavailable()
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            try:
                with read_only_connection(recovery._coordination_path(self._database)) as connection:
                    connection.execute('BEGIN')
                    state = self._window(connection)
                    worker = connection.execute('''SELECT owned.ready FROM source_recovery_supervised_workers AS owned
                        JOIN source_recovery_workers AS roster ON roster.worker_id = owned.worker_id
                        WHERE owned.pid = ? AND owned.created = ? AND owned.generation = ? AND owned.drained = 0
                        AND roster.pid = owned.pid AND roster.startup_generation = owned.generation
                        AND roster.stopped_generation IS NULL''', (identity.pid, identity.created, state.generation)).fetchone()
                    if self._census(_workers(connection)).get(identity) is not True:
                        raise _unavailable()
            except (OSError, sqlite3.Error):
                raise _unavailable() from None
            remaining = deadline - monotonic()
            if worker == (1,) and remaining > 0:
                return
            if remaining > 0:
                sleep(min(0.05, remaining))
        raise _unavailable()

    def drain(self, generation: str, *, timeout_seconds: int = 15) -> None:
        _authorize_operator(self._database)
        if (type(generation) is not str or not re.fullmatch('[0-9a-f]{32}', generation)
                or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30):
            raise _unavailable()
        deadline = monotonic() + timeout_seconds
        with _write(self._database) as connection:
            self._window(connection, generation)
            rows = _workers(connection)
            view = self._census(rows)
        for identity, running in view.items():
            if running and self._census(rows)[identity]:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise _unavailable()
                try:
                    self._boundary.shutdown(identity, timeout_seconds=remaining)
                except Exception:
                    raise _unavailable() from None
        with _write(self._database) as connection:
            self._window(connection, generation)
            self._require_stopped(connection, generation)
            if monotonic() >= deadline:
                raise _unavailable()

    def _require_stopped(self, connection, generation: str) -> None:
        rows = _workers(connection)
        if (any(self._census(rows).values()) or any(worker is None or drained != 1 for _, _, worker, drained in rows)
                or connection.execute('SELECT 1 FROM source_recovery_workers WHERE stopped_generation IS NOT ? LIMIT 1',
                                      (generation,)).fetchone() is not None):
            raise _unavailable()

    def release_maintenance(self, generation: str) -> None:
        """Abort the prepared window, retaining quarantine and rotating generation."""
        _authorize_operator(self._database)
        if type(generation) is not str or not re.fullmatch('[0-9a-f]{32}', generation):
            raise _unavailable()
        with _write(self._database) as connection:
            self._window(connection, generation)
            self._require_stopped(connection, generation)
            connection.execute('DROP TABLE source_recovery_supervised_workers')
            connection.execute('DROP TABLE source_recovery_supervisor')
            recovery._cancel_maintenance(connection, generation)


def claim_worker(connection, worker, state) -> None:
    """Called from lifespan admission under the existing coordinator writer lock."""
    owner = _owner(connection)
    admission = getattr(worker.app.state, 'source_supervisor_admission', None)
    if owner is None and admission is None:
        return
    if (owner is None or owner[3] is not None or state.pending or type(admission) is not WorkerAdmission
            or admission.database != worker.database or type(admission.nonce) is not str
            or len(admission.nonce) != 32 or any(char not in '0123456789abcdef' for char in admission.nonce)):
        raise _unavailable()
    identity = _process_identity()
    if (not _valid_identity(identity) or identity.pid != worker.pid
            or _live_owner(ProcessIdentity(owner[1], owner[2])) is not True):
        raise _unavailable()
    updated = connection.execute('''UPDATE source_recovery_supervised_workers SET worker_id = ?
        WHERE token_hash = ? AND generation = ? AND pid = ? AND created = ? AND worker_id IS NULL''',
        (worker.worker_id, _hash(admission.nonce), state.generation, identity.pid, identity.created),
    )
    if updated.rowcount != 1:
        raise _unavailable()


def record_worker_shutdown(connection, worker) -> None:
    if _owner(connection) is None:
        return
    updated = connection.execute('UPDATE source_recovery_supervised_workers SET drained = 1, ready = 0 WHERE worker_id = ?',
                                 (worker.worker_id,))
    if updated.rowcount != 1:
        raise _unavailable()


def verify_worker_startup(app, *, publish: bool = False) -> None:
    """Recheck completed app startup, then publish only after its listener binds."""
    from backend.agent.deployment_documents import get_deployment_documents

    registration = getattr(app.state, 'source_recovery_registration', None)
    if (type(registration) is not recovery._WorkerRegistration or not registration.supervised
            or registration.app is not app or registration.loop is not asyncio.get_running_loop()
            or recovery._database_path(app) != registration.database
            or getattr(app.state, 'startup_complete', None) is not True):
        raise _unavailable()
    get_deployment_documents(app)
    identity = _process_identity()
    if not _valid_identity(identity) or identity.pid != registration.pid:
        raise _unavailable()
    with _write(registration.database) as connection:
        state = recovery._read_state(connection)
        owner = _owner(connection)
        if (state.pending or recovery._check_barrier(connection, state) is not None
                or state.generation != registration.startup_generation or owner is None or owner[3] is not None
                or _live_owner(ProcessIdentity(owner[1], owner[2])) is not True):
            raise _unavailable()
        claim = connection.execute('''SELECT owned.ready FROM source_recovery_supervised_workers AS owned
            JOIN source_recovery_workers AS roster ON roster.worker_id = owned.worker_id
            WHERE owned.worker_id = ? AND owned.pid = ? AND owned.created = ? AND owned.generation = ?
            AND owned.drained = 0 AND roster.pid = owned.pid AND roster.startup_generation = owned.generation
            AND roster.nonce_hash = ? AND roster.stopped_generation IS NULL''', (
                registration.worker_id, identity.pid, identity.created, state.generation,
                recovery._nonce_hash(registration),
            )).fetchone()
        if claim != (0,):
            raise _unavailable()
        if publish:
            connection.execute('UPDATE source_recovery_supervised_workers SET ready = 1 WHERE worker_id = ?',
                               (registration.worker_id,))


def prepare_window(connection, generation: str, *, active: bool) -> None:
    owner = _owner(connection)
    if owner is None:
        return
    if not active or owner[3] is not None:
        raise _unavailable()  # Only a live owner with fresh stop evidence can release a supervised window.
    connection.execute('UPDATE source_recovery_supervisor SET locked_generation = ? WHERE id = 1', (generation,))


def supervisor_diagnostics(connection, generation: str | None) -> tuple[bool, int | None]:
    """Persisted counts are diagnostic, with no live process or restart attestation."""
    owner = _owner(connection)
    if owner is None:
        return False, None
    if owner[3] != generation:
        raise _unavailable()
    return True, len(_workers(connection))
