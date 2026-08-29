"""
backend/audit/logger.py
------------------------
Phase 7: Centralized Audit Logging System.

Single write path for all security, authentication, tool execution, and task events.
Persists to SQLite table `audit_events` in WAL mode with indexing.

Security Properties:
- Automatic metadata sanitization (redacts passwords, tokens, base64 payloads, document contents).
- Value truncation at max length (500 chars).
- Retention pruning by age (retention_days) and volume (max_rows).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DENYLIST_KEYS = {
    "password", "pass", "pwd", "token", "raw_token", "password_hash", "api_key",
    "authorization", "image_base64", "base64", "secret", "content",
    "body", "file_content", "private_key",
}

_CREATE_AUDIT_TABLE = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id       TEXT PRIMARY KEY,
    timestamp      TEXT NOT NULL,
    session_id     TEXT,
    user_id        TEXT,
    role           TEXT,
    event_type     TEXT NOT NULL,
    action         TEXT,
    resource       TEXT,
    tool           TEXT,
    task_id        TEXT,
    step_id        TEXT,
    success        INTEGER NOT NULL,
    duration_ms    REAL,
    metadata_json  TEXT,
    failure_reason TEXT,
    request_id     TEXT
);
"""

_CREATE_AUDIT_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_events(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_task_id ON audit_events(task_id);
CREATE INDEX IF NOT EXISTS idx_audit_tool ON audit_events(tool);
CREATE INDEX IF NOT EXISTS idx_audit_success ON audit_events(success);
"""


def sanitize_metadata(data: Optional[Dict[str, Any]], max_len: int = 500) -> Dict[str, Any]:
    """
    Sanitize metadata dictionary before audit persistence:
    - Redacts keys matching the denylist.
    - Truncates long string values beyond max_len.
    - Handles nested dicts / lists recursively.
    """
    if not data:
        return {}

    sanitized: Dict[str, Any] = {}
    for key, value in data.items():
        key_lower = str(key).lower()
        if any(deny in key_lower for deny in _DENYLIST_KEYS):
            sanitized[key] = "[REDACTED]"
            continue

        if isinstance(value, str):
            if len(value) > max_len:
                sanitized[key] = value[:max_len] + f"... [truncated {len(value)} chars]"
            else:
                sanitized[key] = value
        elif isinstance(value, dict):
            sanitized[key] = sanitize_metadata(value, max_len)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_metadata(item, max_len) if isinstance(item, dict)
                else (item[:max_len] + "..." if isinstance(item, str) and len(item) > max_len else item)
                for item in value
            ]
        else:
            sanitized[key] = value

    return sanitized


class AuditLogger:
    """
    Centralized audit logger writing to SQLite WAL database.
    This is the sole write path for audit records.
    """

    def __init__(
        self,
        db_path: Path,
        retention_days: int = 180,
        max_rows: int = 50000,
        lock: Optional[threading.Lock] = None,
    ) -> None:
        self._db_path = db_path
        self._retention_days = retention_days
        self._max_rows = max_rows
        self._lock = lock or threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(str(self._db_path))
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(_CREATE_AUDIT_TABLE)
                for idx_sql in _CREATE_AUDIT_INDEXES.strip().split(";"):
                    if idx_sql.strip():
                        conn.execute(idx_sql)
                conn.commit()
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def log(
        self,
        event_type: str,
        success: bool = True,
        action: Optional[str] = None,
        resource: Optional[str] = None,
        tool: Optional[str] = None,
        task_id: Optional[str] = None,
        step_id: Optional[str] = None,
        user_id: Optional[str] = None,
        role: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        duration_ms: Optional[float] = None,
        failure_reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Record an audit event.
        Returns the generated event_id. Never raises exceptions (fails safe).
        """
        event_id = f"audit_{uuid.uuid4().hex}"
        now_iso = datetime.now(timezone.utc).isoformat()
        clean_meta = sanitize_metadata(metadata)
        meta_json = json.dumps(clean_meta) if clean_meta else None

        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute(
                        """INSERT INTO audit_events
                           (event_id, timestamp, session_id, user_id, role, event_type,
                            action, resource, tool, task_id, step_id, success,
                            duration_ms, metadata_json, failure_reason, request_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            event_id,
                            now_iso,
                            session_id,
                            user_id,
                            role,
                            event_type,
                            action,
                            resource,
                            tool,
                            task_id,
                            step_id,
                            1 if success else 0,
                            duration_ms,
                            meta_json,
                            failure_reason,
                            request_id,
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:
            logger.error("Failed to write audit event: %s", exc)

        return event_id

    def query_events(
        self,
        limit: int = 50,
        offset: int = 0,
        user_id: Optional[str] = None,
        event_type: Optional[str] = None,
        task_id: Optional[str] = None,
        tool: Optional[str] = None,
        success: Optional[bool] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query audit log with pagination and filters."""
        query = "SELECT * FROM audit_events WHERE 1=1"
        count_query = "SELECT COUNT(*) as total FROM audit_events WHERE 1=1"
        params: List[Any] = []

        if user_id:
            query += " AND user_id = ?"
            count_query += " AND user_id = ?"
            params.append(user_id)
        if event_type:
            query += " AND event_type = ?"
            count_query += " AND event_type = ?"
            params.append(event_type)
        if task_id:
            query += " AND task_id = ?"
            count_query += " AND task_id = ?"
            params.append(task_id)
        if tool:
            query += " AND tool = ?"
            count_query += " AND tool = ?"
            params.append(tool)
        if success is not None:
            query += " AND success = ?"
            count_query += " AND success = ?"
            params.append(1 if success else 0)
        if start_time:
            query += " AND timestamp >= ?"
            count_query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            query += " AND timestamp <= ?"
            count_query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        fetch_params = params + [limit, offset]

        with self._lock:
            conn = self._connect()
            try:
                total_row = conn.execute(count_query, params).fetchone()
                total = int(total_row["total"]) if total_row else 0

                rows = conn.execute(query, fetch_params).fetchall()
                events = []
                for r in rows:
                    meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
                    events.append({
                        "event_id": r["event_id"],
                        "timestamp": r["timestamp"],
                        "session_id": r["session_id"],
                        "user_id": r["user_id"],
                        "role": r["role"],
                        "event_type": r["event_type"],
                        "action": r["action"],
                        "resource": r["resource"],
                        "tool": r["tool"],
                        "task_id": r["task_id"],
                        "step_id": r["step_id"],
                        "success": bool(r["success"]),
                        "duration_ms": r["duration_ms"],
                        "metadata": meta,
                        "failure_reason": r["failure_reason"],
                        "request_id": r["request_id"],
                    })

                return {"events": events, "total": total, "limit": limit, "offset": offset}
            finally:
                conn.close()

    def get_summary(self) -> Dict[str, Any]:
        """Aggregate summary metrics for the audit dashboard."""
        with self._lock:
            conn = self._connect()
            try:
                total = conn.execute("SELECT COUNT(*) as cnt FROM audit_events").fetchone()["cnt"]
                failed = conn.execute("SELECT COUNT(*) as cnt FROM audit_events WHERE success = 0").fetchone()["cnt"]
                denied = conn.execute(
                    "SELECT COUNT(*) as cnt FROM audit_events WHERE event_type LIKE '%denied%' OR failure_reason LIKE '%denied%' OR failure_reason LIKE '%forbidden%'"
                ).fetchone()["cnt"]
                tool_execs = conn.execute("SELECT COUNT(*) as cnt FROM audit_events WHERE event_type = 'tool.execution'").fetchone()["cnt"]
                auth_fails = conn.execute("SELECT COUNT(*) as cnt FROM audit_events WHERE event_type = 'auth.login_failed'").fetchone()["cnt"]

                return {
                    "total_events": total,
                    "failed_events": failed,
                    "denied_actions": denied,
                    "tool_executions": tool_execs,
                    "auth_failures": auth_fails,
                }
            finally:
                conn.close()

    def prune_retention(self) -> int:
        """Prune audit records older than retention_days or exceeding max_rows."""
        now = datetime.now(timezone.utc)
        cutoff_iso = (now - timedelta(days=self._retention_days)).isoformat()
        deleted = 0

        with self._lock:
            conn = self._connect()
            try:
                # 1. Prune by age
                cur = conn.execute("DELETE FROM audit_events WHERE timestamp < ?", (cutoff_iso,))
                deleted += cur.rowcount

                # 2. Prune excess rows (oldest first)
                count_row = conn.execute("SELECT COUNT(*) as cnt FROM audit_events").fetchone()
                total = int(count_row["cnt"]) if count_row else 0
                if total > self._max_rows:
                    excess = total - self._max_rows
                    cur = conn.execute(
                        """DELETE FROM audit_events WHERE event_id IN (
                               SELECT event_id FROM audit_events ORDER BY timestamp ASC LIMIT ?
                           )""",
                        (excess,),
                    )
                    deleted += cur.rowcount

                conn.commit()
                return deleted
            finally:
                conn.close()
