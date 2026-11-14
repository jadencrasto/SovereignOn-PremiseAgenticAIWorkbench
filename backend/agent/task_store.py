"""
backend/agent/task_store.py
----------------------------
Phase 6: SQLite persistence for tasks AND approvals.

Database: data/tasks/tasks.db

Both tasks and approvals are persisted so they survive backend restarts.
Uses parameterized SQL exclusively — no string interpolation.

NEVER stores: image base64, API keys, secrets, credentials.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_CREATE_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id          TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    user_request     TEXT NOT NULL,
    plan_json        TEXT,
    current_step_idx INTEGER DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'pending',
    result           TEXT,
    error            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    completed_at     TEXT,
    user_id          TEXT,
    user_role        TEXT,
    clearance        TEXT
)
"""

_CREATE_APPROVALS_TABLE = """
CREATE TABLE IF NOT EXISTS approvals (
    approval_id      TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    tool_name        TEXT NOT NULL,
    arguments_hash   TEXT NOT NULL,
    risk_level       TEXT NOT NULL DEFAULT 'low',
    reason           TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    resolved_at      TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
)
"""

_CREATE_TASK_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS task_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          TEXT NOT NULL,
    step_id          TEXT,
    event_type       TEXT NOT NULL,
    tool_name        TEXT,
    risk_level       TEXT,
    duration_ms      REAL,
    success          INTEGER,
    result_summary   TEXT,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
)
"""

_CREATE_TASK_PROVENANCE_TABLE = """
CREATE TABLE IF NOT EXISTS task_security_provenance (
    task_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    owner_id TEXT,
    conversation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    verification_state TEXT NOT NULL CHECK (verification_state = 'unverified'),
    dependency_state TEXT NOT NULL CHECK (dependency_state = 'unknown'),
    dependencies_json TEXT CHECK (dependencies_json IS NULL),
    effective_classification TEXT CHECK (effective_classification IS NULL)
)
"""

_CREATE_ARTIFACTS_TABLE = """
CREATE TABLE IF NOT EXISTS artifacts (
    filename         TEXT PRIMARY KEY,
    path             TEXT NOT NULL,
    task_id          TEXT,
    user_id          TEXT,
    user_role        TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    size_bytes       INTEGER DEFAULT 0,
    sha256_hash      TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
)
"""


class TaskStore:
    """
    Local SQLite persistence layer for agent tasks, approvals, and artifacts.

    Thread-safe via a threading lock.  Database and directory are
    created automatically on first use.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """Create the database file and tables if they don't exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            conn = sqlite3.connect(str(self._db_path))
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute(_CREATE_TASKS_TABLE)
                conn.execute(_CREATE_APPROVALS_TABLE)
                conn.execute(_CREATE_TASK_EVENTS_TABLE)
                conn.execute(_CREATE_TASK_PROVENANCE_TABLE)
                conn.execute(_CREATE_ARTIFACTS_TABLE)
                artifact_columns = {row[1] for row in conn.execute('PRAGMA table_info(artifacts)')}
                for column in ('artifact_id', 'display_name'):
                    if column not in artifact_columns:
                        conn.execute(f'ALTER TABLE artifacts ADD COLUMN {column} TEXT')
                conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_artifacts_opaque_id ON artifacts(artifact_id) WHERE artifact_id IS NOT NULL')
                conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_user ON artifacts(user_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id)")
                for col in ("user_id", "user_role", "clearance"):
                    try:
                        conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
                    except sqlite3.OperationalError:
                        pass
                conn.commit()
                logger.info("TaskStore initialised | db=%s", self._db_path)
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # Tasks — CRUD
    # ------------------------------------------------------------------

    def save_task(self, task: Dict[str, Any], *, initialize_security: bool = False) -> None:
        """Insert or replace a task record."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute('BEGIN IMMEDIATE')
                existing = conn.execute(
                    'SELECT session_id, user_id FROM tasks WHERE task_id = ?', (task['task_id'],)
                ).fetchone()
                if initialize_security and existing:
                    raise sqlite3.IntegrityError('Task ID already exists')
                if existing:
                    protected = conn.execute(
                        'SELECT 1 FROM task_security_provenance WHERE task_id = ?', (task['task_id'],)
                    ).fetchone()
                    if protected and (existing['session_id'] != task['session_id'] or existing['user_id'] != task.get('user_id')):
                        raise ValueError('Task owner and conversation cannot be reassigned')
                conn.execute(
                    """INSERT OR REPLACE INTO tasks
                       (task_id, session_id, user_request, plan_json,
                        current_step_idx, status, result, error,
                        created_at, updated_at, completed_at,
                        user_id, user_role, clearance)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task["task_id"],
                        task["session_id"],
                        task["user_request"],
                        task.get("plan_json"),
                        task.get("current_step_idx", 0),
                        task["status"],
                        task.get("result"),
                        task.get("error"),
                        task.get("created_at", now),
                        task.get("updated_at", now),
                        task.get("completed_at"),
                        task.get("user_id"),
                        task.get("user_role"),
                        task.get("clearance"),
                    ),
                )
                if initialize_security:
                    from backend.auth.request_context import effective_owner
                    from backend.security.task_access import TASK_PROVENANCE_SCHEMA_VERSION, TASK_PROVENANCE_UNVERIFIED
                    bound_owner = effective_owner.get()
                    # Preserve the attribution only when it matches the trusted
                    # request context; background/unbound calls stay unknown.
                    owner = bound_owner if bound_owner and bound_owner == task.get('user_id') else None
                    conn.execute(
                        """INSERT INTO task_security_provenance
                           (task_id, schema_version, owner_id, conversation_id, created_at,
                            verification_state, dependency_state, dependencies_json, effective_classification)
                           VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
                        (task['task_id'], TASK_PROVENANCE_SCHEMA_VERSION, owner, task['session_id'],
                         task.get('created_at', now), TASK_PROVENANCE_UNVERIFIED, 'unknown'),
                    )
                conn.commit()
            finally:
                conn.close()

    def get_task_security_provenance(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Read the server-owned quarantine envelope; absence means legacy/unknown."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute('SELECT * FROM task_security_provenance WHERE task_id = ?', (task_id,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a task by ID."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def list_tasks(
        self,
        limit: int = 50,
        status: Optional[str] = None,
        user_id: Optional[Union[str, List[str]]] = None,
    ) -> List[Dict[str, Any]]:
        """List tasks, optionally filtered by status and/or user_id."""
        with self._lock:
            conn = self._connect()
            try:
                clauses = []
                params: list = []
                if status:
                    clauses.append("status = ?")
                    params.append(status)
                if user_id is not None:
                    if isinstance(user_id, (list, tuple, set)):
                        clean_ids = [str(u) for u in user_id if u]
                        if clean_ids:
                            placeholders = ", ".join("?" for _ in clean_ids)
                            clauses.append(f"user_id IN ({placeholders})")
                            params.extend(clean_ids)
                        else:
                            clauses.append("1=0")
                    else:
                        clauses.append("user_id = ?")
                        params.append(str(user_id))

                where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                params.append(limit)
                rows = conn.execute(
                    f"SELECT * FROM tasks {where_sql} ORDER BY created_at DESC LIMIT ?",
                    tuple(params),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def update_task_status(
        self,
        task_id: str,
        status: str,
        current_step_idx: Optional[int] = None,
        result: Optional[str] = None,
        error: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        """Update task status and optionally other fields."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                fields = ["status = ?", "updated_at = ?"]
                values: list = [status, now]

                if current_step_idx is not None:
                    fields.append("current_step_idx = ?")
                    values.append(current_step_idx)
                if result is not None:
                    fields.append("result = ?")
                    values.append(result)
                if error is not None:
                    fields.append("error = ?")
                    values.append(error)
                if completed_at is not None:
                    fields.append("completed_at = ?")
                    values.append(completed_at)

                values.append(task_id)
                conn.execute(
                    f"UPDATE tasks SET {', '.join(fields)} WHERE task_id = ?",
                    values,
                )
                conn.commit()
            finally:
                conn.close()

    def update_task_plan(self, task_id: str, plan_json: str) -> None:
        """Update the plan JSON for a task."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE tasks SET plan_json = ?, updated_at = ? WHERE task_id = ?",
                    (plan_json, now, task_id),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_task(self, task_id: str) -> bool:
        """Delete a task and its related records."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM approvals WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM task_security_provenance WHERE task_id = ?", (task_id,))
                cursor = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Approvals — CRUD
    # ------------------------------------------------------------------

    def save_approval(self, approval: Dict[str, Any]) -> None:
        """Insert an approval once; an existing identifier cannot be reset."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO approvals
                       (approval_id, task_id, step_id, tool_name,
                        arguments_hash, risk_level, reason, status,
                        created_at, expires_at, resolved_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        approval["approval_id"],
                        approval["task_id"],
                        approval["step_id"],
                        approval["tool_name"],
                        approval["arguments_hash"],
                        approval.get("risk_level", "low"),
                        approval.get("reason"),
                        approval.get("status", "pending"),
                        approval["created_at"],
                        approval["expires_at"],
                        approval.get("resolved_at"),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve an approval by ID."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM approvals WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def get_pending_approval_for_task(
        self, task_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get the pending approval for a task (if any)."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM approvals WHERE task_id = ? AND status = 'pending' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def list_approvals_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        """List all approvals for a task."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at",
                    (task_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_approved_approvals_for_task(
        self, task_id: str
    ) -> List[Dict[str, Any]]:
        """Retrieve any approved approvals for a specific task."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM approvals WHERE task_id = ? AND status = 'approved' "
                    "ORDER BY created_at DESC",
                    (task_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def update_approval_status(
        self,
        approval_id: str,
        status: str,
        resolved_at: Optional[str] = None,
    ) -> None:
        """Invalidate a live approval without resurrecting a terminal state."""
        if status not in ('rejected', 'expired'):
            raise ValueError('Only approval invalidation is supported')
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE approvals SET status = ?, resolved_at = ? WHERE approval_id = ? AND status IN ('pending', 'approved')",
                    (status, resolved_at, approval_id),
                )
                conn.commit()
            finally:
                conn.close()

    def update_approval_status_atomic(
        self,
        approval_id: str,
        expected_status: str,
        new_status: str,
        resolved_at: Optional[str] = None,
    ) -> bool:
        """Atomically update an approval's status only if current status matches expected_status."""
        if (expected_status, new_status) not in (('pending', 'approved'), ('pending', 'rejected')):
            raise ValueError('Unsupported approval transition')
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "UPDATE approvals SET status = ?, resolved_at = ? WHERE approval_id = ? AND status = ?",
                    (new_status, resolved_at, approval_id, expected_status),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def consume_approved_approval(self, approval_id: str) -> bool:
        """Consume once under a bounded SQLite write transaction.

        This coordinates writers of this database only; it does not authenticate
        the database path, establish anti-rollback freshness, or authorize work.
        """
        with self._lock:
            # Existing database only: a missing database must not be recreated.
            uri = self._db_path.absolute().as_uri() + '?mode=rw'
            conn = sqlite3.connect(uri, uri=True, timeout=1.0, isolation_level=None)
            try:
                conn.execute('PRAGMA foreign_keys=ON')
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute(
                    "SELECT expires_at FROM approvals WHERE approval_id=? AND status='approved'",
                    (approval_id,),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return False
                try:
                    expires = datetime.fromisoformat(row[0])
                    if expires.tzinfo is None or expires.utcoffset() is None:
                        raise ValueError('Timezone required')
                    if datetime.now(timezone.utc) >= expires:
                        conn.rollback()
                        return False
                except (TypeError, ValueError, OverflowError):
                    conn.rollback()
                    return False
                cursor = conn.execute(
                    "UPDATE approvals SET status = 'consumed' "
                    "WHERE approval_id = ? AND status = 'approved' AND expires_at = ?",
                    (approval_id, row[0]),
                )
                conn.commit()
                return cursor.rowcount == 1
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Task events (audit log)
    # ------------------------------------------------------------------

    def save_event(self, event: Dict[str, Any]) -> None:
        """Insert an audit event."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO task_events
                       (task_id, step_id, event_type, tool_name,
                        risk_level, duration_ms, success, result_summary, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event["task_id"],
                        event.get("step_id"),
                        event["event_type"],
                        event.get("tool_name"),
                        event.get("risk_level"),
                        event.get("duration_ms"),
                        1 if event.get("success") else 0 if event.get("success") is not None else None,
                        event.get("result_summary", "")[:500],
                        event.get("created_at", now),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def list_events_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        """List audit events for a task."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM task_events WHERE task_id = ? ORDER BY created_at",
                    (task_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Artifact persistence & ownership (Step 16)
    # ------------------------------------------------------------------

    def save_artifact(
        self,
        filename: str,
        path: str,
        task_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_role: Optional[str] = None,
        size_bytes: int = 0,
        sha256_hash: Optional[str] = None,
        artifact_id: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register a new artifact without replacing existing metadata."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO artifacts (filename, path, task_id, user_id, user_role, created_at, updated_at, size_bytes, sha256_hash, artifact_id, display_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (filename, path, task_id, user_id, user_role, now, now, size_bytes, sha256_hash, artifact_id, display_name),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError('Artifact registration conflicts with an existing record') from exc
            finally:
                conn.close()

        return {
            "filename": filename,
            "path": path,
            "task_id": task_id,
            "user_id": user_id,
            "user_role": user_role,
            "size_bytes": size_bytes,
            "sha256_hash": sha256_hash,
            "artifact_id": artifact_id,
            "display_name": display_name,
        }

    def get_artifact(self, filename: str) -> Optional[Dict[str, Any]]:
        """Retrieve artifact ownership record by filename."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM artifacts WHERE filename = ?", (filename,)
                ).fetchone()
                if row is None:
                    return None
                return dict(row)
            finally:
                conn.close()

    def list_artifacts(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all artifact records, optionally filtered by user_id."""
        with self._lock:
            conn = self._connect()
            try:
                if user_id:
                    rows = conn.execute(
                        "SELECT * FROM artifacts WHERE user_id = ? ORDER BY created_at DESC",
                        (user_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM artifacts ORDER BY created_at DESC"
                    ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
