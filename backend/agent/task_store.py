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
from typing import Any, Dict, List, Optional

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
    completed_at     TEXT
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


class TaskStore:
    """
    Local SQLite persistence layer for agent tasks and approvals.

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

    def save_task(self, task: Dict[str, Any]) -> None:
        """Insert or replace a task record."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO tasks
                       (task_id, session_id, user_request, plan_json,
                        current_step_idx, status, result, error,
                        created_at, updated_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    ),
                )
                conn.commit()
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
    ) -> List[Dict[str, Any]]:
        """List tasks, optionally filtered by status."""
        with self._lock:
            conn = self._connect()
            try:
                if status:
                    rows = conn.execute(
                        "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                        (status, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                        (limit,),
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
                cursor = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Approvals — CRUD
    # ------------------------------------------------------------------

    def save_approval(self, approval: Dict[str, Any]) -> None:
        """Insert or replace an approval record."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO approvals
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

    def update_approval_status(
        self,
        approval_id: str,
        status: str,
        resolved_at: Optional[str] = None,
    ) -> None:
        """Update an approval's status."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE approvals SET status = ?, resolved_at = ? WHERE approval_id = ?",
                    (status, resolved_at, approval_id),
                )
                conn.commit()
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
