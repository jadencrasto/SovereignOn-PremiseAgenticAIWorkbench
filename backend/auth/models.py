"""
backend/auth/models.py
-----------------------
Phase 7: Local Authentication & Role-Based Access Control (RBAC) data models.

Storage: SQLite tables `users` and `sessions` inside `data/tasks/tasks.db`.
All operations use parameterized SQL and thread locking.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums & RBAC Permission Table
# ---------------------------------------------------------------------------

class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Permission(str, Enum):
    VIEW_DATA = "view_data"                     # View documents, tasks, health, audit
    EXECUTE_READ_TOOLS = "execute_read_tools"   # document_search, file_list, file_read, calculator
    EXECUTE_WRITE_TOOLS = "execute_write_tools" # file_write
    APPROVE_TASKS = "approve_tasks"             # Approve high-risk tasks
    MANAGE_TASKS = "manage_tasks"               # Cancel, resume tasks
    MANAGE_USERS = "manage_users"               # Create, modify users
    MANAGE_CONFIG = "manage_config"             # Configuration changes, tool enable/disable
    VIEW_SECURITY = "view_security"             # Security diagnostics & audit logs


# Explicit Role-to-Permissions Mapping (Single Source of Truth)
ROLE_PERMISSIONS: Dict[UserRole, Set[Permission]] = {
    UserRole.VIEWER: {
        Permission.VIEW_DATA,
    },
    UserRole.OPERATOR: {
        Permission.VIEW_DATA,
        Permission.EXECUTE_READ_TOOLS,
        Permission.EXECUTE_WRITE_TOOLS,
        Permission.APPROVE_TASKS,
        Permission.MANAGE_TASKS,
    },
    UserRole.ADMIN: {
        Permission.VIEW_DATA,
        Permission.EXECUTE_READ_TOOLS,
        Permission.EXECUTE_WRITE_TOOLS,
        Permission.APPROVE_TASKS,
        Permission.MANAGE_TASKS,
        Permission.MANAGE_USERS,
        Permission.MANAGE_CONFIG,
        Permission.VIEW_SECURITY,
    },
}

# Role hierarchy for min_role checks
ROLE_HIERARCHY: Dict[UserRole, int] = {
    UserRole.VIEWER: 1,
    UserRole.OPERATOR: 2,
    UserRole.ADMIN: 3,
}


def has_permission(role: str, permission: Permission) -> bool:
    """Check if a role has an explicit permission."""
    try:
        user_role = UserRole(role)
        return permission in ROLE_PERMISSIONS.get(user_role, set())
    except (ValueError, KeyError):
        return False


def is_role_sufficient(user_role: str, min_role: UserRole) -> bool:
    """Check if a user role meets or exceeds a minimum role."""
    try:
        current_level = ROLE_HIERARCHY.get(UserRole(user_role), 0)
        required_level = ROLE_HIERARCHY.get(min_role, 999)
        return current_level >= required_level
    except (ValueError, KeyError):
        return False


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class User(BaseModel):
    id: str
    username: str
    password_hash: str
    role: str = UserRole.VIEWER.value
    is_active: bool = True
    must_change_password: bool = False
    created_at: str
    last_login_at: Optional[str] = None


class UserPublic(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    must_change_password: bool
    created_at: str
    last_login_at: Optional[str] = None


class SessionData(BaseModel):
    token_hash: str
    user_id: str
    created_at: str
    last_seen_at: str
    expires_at: str
    revoked_at: Optional[str] = None


# ---------------------------------------------------------------------------
# SQLite Table Definitions & Store
# ---------------------------------------------------------------------------

_CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id                   TEXT PRIMARY KEY,
    username             TEXT UNIQUE NOT NULL,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'viewer',
    is_active            INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    last_login_at        TEXT
);
"""

_CREATE_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    token_hash           TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    revoked_at           TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

_CREATE_FAILED_LOGINS_TABLE = """
CREATE TABLE IF NOT EXISTS failed_logins (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT NOT NULL,
    ip_address           TEXT,
    timestamp            TEXT NOT NULL
);
"""


class AuthStore:
    """SQLite data access for users and sessions."""

    def __init__(self, db_path: Path, lock: Optional[threading.Lock] = None) -> None:
        self._db_path = db_path
        self._lock = lock or threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(str(self._db_path))
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute(_CREATE_USERS_TABLE)
                conn.execute(_CREATE_SESSIONS_TABLE)
                conn.execute(_CREATE_FAILED_LOGINS_TABLE)
                conn.commit()
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def create_user(self, user: User) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO users
                       (id, username, password_hash, role, is_active, must_change_password, created_at, last_login_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user.id,
                        user.username,
                        user.password_hash,
                        user.role,
                        1 if user.is_active else 0,
                        1 if user.must_change_password else 0,
                        user.created_at,
                        user.last_login_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_user_by_username(self, username: str) -> Optional[User]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM users WHERE username = ?", (username,)
                ).fetchone()
                if not row:
                    return None
                return User(
                    id=row["id"],
                    username=row["username"],
                    password_hash=row["password_hash"],
                    role=row["role"],
                    is_active=bool(row["is_active"]),
                    must_change_password=bool(row["must_change_password"]),
                    created_at=row["created_at"],
                    last_login_at=row["last_login_at"],
                )
            finally:
                conn.close()

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                if not row:
                    return None
                return User(
                    id=row["id"],
                    username=row["username"],
                    password_hash=row["password_hash"],
                    role=row["role"],
                    is_active=bool(row["is_active"]),
                    must_change_password=bool(row["must_change_password"]),
                    created_at=row["created_at"],
                    last_login_at=row["last_login_at"],
                )
            finally:
                conn.close()

    def list_users(self) -> List[UserPublic]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
                return [
                    UserPublic(
                        id=r["id"],
                        username=r["username"],
                        role=r["role"],
                        is_active=bool(r["is_active"]),
                        must_change_password=bool(r["must_change_password"]),
                        created_at=r["created_at"],
                        last_login_at=r["last_login_at"],
                    )
                    for r in rows
                ]
            finally:
                conn.close()

    def count_users(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
                return int(row["cnt"]) if row else 0
            finally:
                conn.close()

    def update_user(
        self,
        user_id: str,
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
        password_hash: Optional[str] = None,
        must_change_password: Optional[bool] = None,
        last_login_at: Optional[str] = None,
    ) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                fields = []
                values = []
                if role is not None:
                    fields.append("role = ?")
                    values.append(role)
                if is_active is not None:
                    fields.append("is_active = ?")
                    values.append(1 if is_active else 0)
                if password_hash is not None:
                    fields.append("password_hash = ?")
                    values.append(password_hash)
                if must_change_password is not None:
                    fields.append("must_change_password = ?")
                    values.append(1 if must_change_password else 0)
                if last_login_at is not None:
                    fields.append("last_login_at = ?")
                    values.append(last_login_at)

                if not fields:
                    return False

                values.append(user_id)
                cur = conn.execute(
                    f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def save_session(self, session: SessionData) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO sessions
                       (token_hash, user_id, created_at, last_seen_at, expires_at, revoked_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        session.token_hash,
                        session.user_id,
                        session.created_at,
                        session.last_seen_at,
                        session.expires_at,
                        session.revoked_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_session(self, token_hash: str) -> Optional[SessionData]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM sessions WHERE token_hash = ? AND revoked_at IS NULL",
                    (token_hash,),
                ).fetchone()
                if not row:
                    return None
                return SessionData(
                    token_hash=row["token_hash"],
                    user_id=row["user_id"],
                    created_at=row["created_at"],
                    last_seen_at=row["last_seen_at"],
                    expires_at=row["expires_at"],
                    revoked_at=row["revoked_at"],
                )
            finally:
                conn.close()

    def touch_session(self, token_hash: str, last_seen_at: str, expires_at: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                    (last_seen_at, expires_at, token_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_session(self, token_hash: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def revoke_all_user_sessions(self, user_id: str) -> int:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE user_id = ?", (user_id,)
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def delete_expired_sessions(self, now_iso: str) -> int:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE expires_at < ?", (now_iso,)
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Failed Logins & Lockout
    # ------------------------------------------------------------------

    def record_failed_login(self, username: str, ip_address: Optional[str] = None) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO failed_logins (username, ip_address, timestamp) VALUES (?, ?, ?)",
                    (username, ip_address, now_iso),
                )
                conn.commit()
            finally:
                conn.close()

    def get_failed_login_count(self, username: str, window_start_iso: str) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM failed_logins WHERE username = ? AND timestamp >= ?",
                    (username, window_start_iso),
                ).fetchone()
                return int(row["cnt"]) if row else 0
            finally:
                conn.close()

    def clear_failed_logins(self, username: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM failed_logins WHERE username = ?", (username,)
                )
                conn.commit()
            finally:
                conn.close()
