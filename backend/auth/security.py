"""
backend/auth/security.py
-------------------------
Phase 7: Cryptographic helpers, session management, and first-run credential generation.

Security Properties:
- Argon2id for password hashing.
- Opaque 256-bit session tokens (`secrets.token_urlsafe(32)`).
- Session tokens stored as SHA-256 hashes (never plaintext in DB).
- Cryptographically random first-run admin credentials with forced password change.
- Constant-time password verification.
- Brute-force rate limiting / lockout.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from backend.auth.models import AuthStore, User, UserRole, SessionData

logger = logging.getLogger(__name__)

# Standard Argon2id hasher with recommended security parameters
_ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id."""
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against an Argon2id hash in constant time."""
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, VerificationError):
        return False
    except Exception as exc:
        logger.error("Unexpected error during password verification: %s", exc)
        return False


def hash_token(raw_token: str) -> str:
    """Compute deterministic SHA-256 hash of a raw session token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    """Generate an opaque 256-bit url-safe session token."""
    return secrets.token_urlsafe(32)


class SessionManager:
    """Manages session creation, sliding idle timeout, and absolute expiration."""

    def __init__(
        self,
        store: AuthStore,
        idle_timeout_seconds: int = 28800,  # 8 hours
        absolute_timeout_seconds: int = 86400,  # 24 hours
    ) -> None:
        self._store = store
        self._idle_timeout_seconds = idle_timeout_seconds
        self._absolute_timeout_seconds = absolute_timeout_seconds

    def create_session(self, user_id: str) -> Tuple[str, SessionData]:
        """Create a new session and return (raw_token, session_data)."""
        raw_token = generate_session_token()
        token_hash = hash_token(raw_token)

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires_at = (now + timedelta(seconds=self._idle_timeout_seconds)).isoformat()

        session = SessionData(
            token_hash=token_hash,
            user_id=user_id,
            created_at=now_iso,
            last_seen_at=now_iso,
            expires_at=expires_at,
        )
        self._store.save_session(session)
        return raw_token, session

    def validate_session(self, raw_token: str) -> Optional[SessionData]:
        """
        Validate session token, enforcing sliding idle timeout and absolute max timeout.
        Returns SessionData if valid, None if invalid or expired.
        """
        if not raw_token:
            return None

        token_hash = hash_token(raw_token)
        session = self._store.get_session(token_hash)
        if not session:
            return None

        now = datetime.now(timezone.utc)

        # Check absolute timeout from created_at
        try:
            created_at = datetime.fromisoformat(session.created_at)
            if now > created_at + timedelta(seconds=self._absolute_timeout_seconds):
                self._store.delete_session(token_hash)
                logger.info("Session %s expired (absolute timeout)", token_hash[:8])
                return None
        except Exception:
            return None

        # Check idle timeout from expires_at
        try:
            expires_at = datetime.fromisoformat(session.expires_at)
            if now > expires_at:
                self._store.delete_session(token_hash)
                logger.info("Session %s expired (idle timeout)", token_hash[:8])
                return None
        except Exception:
            return None

        # Slide idle timeout forward
        new_expires_at = (now + timedelta(seconds=self._idle_timeout_seconds)).isoformat()
        self._store.touch_session(token_hash, now.isoformat(), new_expires_at)
        session.last_seen_at = now.isoformat()
        session.expires_at = new_expires_at

        return session

    def revoke_session(self, raw_token: str) -> bool:
        """Revoke a session by deleting its record."""
        token_hash = hash_token(raw_token)
        return self._store.delete_session(token_hash)


class BruteForceProtector:
    """Protects against password brute-force attacks via rolling-window lockouts."""

    def __init__(
        self,
        store: AuthStore,
        max_attempts: int = 5,
        lockout_window_seconds: int = 900,  # 15 minutes
    ) -> None:
        self._store = store
        self._max_attempts = max_attempts
        self._lockout_window_seconds = lockout_window_seconds

    def is_locked_out(self, username: str) -> bool:
        """Check if username is currently locked out."""
        now = datetime.now(timezone.utc)
        window_start = (now - timedelta(seconds=self._lockout_window_seconds)).isoformat()
        failed_count = self._store.get_failed_login_count(username, window_start)
        return failed_count >= self._max_attempts

    def record_failure(self, username: str, ip: Optional[str] = None) -> bool:
        """Record a failed login attempt. Returns True if account is now locked out."""
        self._store.record_failed_login(username, ip)
        return self.is_locked_out(username)

    def record_success(self, username: str) -> None:
        """Clear failed attempts upon successful authentication."""
        self._store.clear_failed_logins(username)


def initialize_admin_user_if_empty(store: AuthStore) -> Optional[str]:
    """
    If the users database is empty on first startup, generates a cryptographically
    random admin password, stores the user with must_change_password=True,
    and returns the one-time raw credential to be displayed in the startup banner.

    Never creates a predictable default password.
    """
    if store.count_users() > 0:
        return None

    # Generate a cryptographically random 16-character password
    raw_password = secrets.token_urlsafe(12)
    pwd_hash = hash_password(raw_password)
    now_iso = datetime.now(timezone.utc).isoformat()

    admin_user = User(
        id=f"user_{uuid.uuid4().hex[:12]}",
        username="admin",
        password_hash=pwd_hash,
        role=UserRole.ADMIN.value,
        is_active=True,
        must_change_password=True,
        created_at=now_iso,
    )
    store.create_user(admin_user)

    logger.warning("First-run initialization: Created initial administrator account.")
    return raw_password
