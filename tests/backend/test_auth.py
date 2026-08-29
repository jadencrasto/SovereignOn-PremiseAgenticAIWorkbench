"""
tests/backend/test_auth.py
--------------------------
Phase 7 tests for Local Authentication, Argon2id hashing, Sessions, and Lockout.
"""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from backend.auth.models import AuthStore, User, UserRole, SessionData
from backend.auth.security import (
    BruteForceProtector,
    SessionManager,
    generate_session_token,
    hash_password,
    hash_token,
    initialize_admin_user_if_empty,
    verify_password,
)


@pytest.fixture
def auth_store(tmp_path: Path):
    db_file = tmp_path / "test_auth.db"
    store = AuthStore(db_path=db_file)
    # Seed a standard test user for FK relations
    test_user = User(
        id="user_123",
        username="testuser",
        password_hash=hash_password("Password123!"),
        role=UserRole.OPERATOR.value,
        is_active=True,
        must_change_password=False,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    store.create_user(test_user)
    return store


class TestPasswordHashing:
    """Test Argon2id hashing and verification."""

    def test_hash_and_verify_success(self):
        pwd = "CorrectHorseBatteryStaple123!"
        hashed = hash_password(pwd)
        assert hashed.startswith("$argon2id$")
        assert verify_password(pwd, hashed) is True

    def test_verify_failure_wrong_password(self):
        hashed = hash_password("Secret123!")
        assert verify_password("WrongPassword", hashed) is False

    def test_verify_failure_malformed_hash(self):
        assert verify_password("Secret123!", "not_a_valid_hash") is False


class TestFirstRunAdminGeneration:
    """Test cryptographically random first-run admin credential generation."""

    def test_initialize_generates_random_admin(self, tmp_path):
        empty_store = AuthStore(db_path=tmp_path / "empty_auth.db")
        pwd1 = initialize_admin_user_if_empty(empty_store)
        assert pwd1 is not None
        assert len(pwd1) >= 12

        admin = empty_store.get_user_by_username("admin")
        assert admin is not None
        assert admin.role == UserRole.ADMIN.value
        assert admin.must_change_password is True
        assert verify_password(pwd1, admin.password_hash) is True

        # Second call does not re-generate
        pwd2 = initialize_admin_user_if_empty(empty_store)
        assert pwd2 is None

    def test_credentials_are_random_across_runs(self, tmp_path):
        store1 = AuthStore(db_path=tmp_path / "db1.db")
        store2 = AuthStore(db_path=tmp_path / "db2.db")
        p1 = initialize_admin_user_if_empty(store1)
        p2 = initialize_admin_user_if_empty(store2)
        assert p1 != p2  # Never predictable default


class TestSessionManagement:
    """Test sliding idle timeout and absolute expiration."""

    def test_create_and_validate_session(self, auth_store):
        mgr = SessionManager(auth_store, idle_timeout_seconds=3600, absolute_timeout_seconds=7200)
        raw_token, session = mgr.create_session("user_123")

        validated = mgr.validate_session(raw_token)
        assert validated is not None
        assert validated.user_id == "user_123"

    def test_idle_timeout_expiration(self, auth_store):
        mgr = SessionManager(auth_store, idle_timeout_seconds=1, absolute_timeout_seconds=10)
        raw_token, session = mgr.create_session("user_123")

        # Manually expire the session idle time
        token_hash = hash_token(raw_token)
        past_iso = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        auth_store.touch_session(token_hash, past_iso, past_iso)

        assert mgr.validate_session(raw_token) is None

    def test_absolute_timeout_expiration(self, auth_store):
        mgr = SessionManager(auth_store, idle_timeout_seconds=3600, absolute_timeout_seconds=1)
        raw_token, session = mgr.create_session("user_123")

        # Manually set created_at into the past beyond absolute timeout
        token_hash = hash_token(raw_token)
        past_iso = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        future_iso = (datetime.now(timezone.utc) + timedelta(seconds=1000)).isoformat()
        auth_store.save_session(SessionData(
            token_hash=token_hash,
            user_id="user_123",
            created_at=past_iso,
            last_seen_at=past_iso,
            expires_at=future_iso,
        ))

        assert mgr.validate_session(raw_token) is None

    def test_revoke_session(self, auth_store):
        mgr = SessionManager(auth_store)
        raw_token, _ = mgr.create_session("user_123")
        assert mgr.revoke_session(raw_token) is True
        assert mgr.validate_session(raw_token) is None


class TestBruteForceProtection:
    """Test failed login tracking and lockout."""

    def test_lockout_after_max_attempts(self, auth_store):
        protector = BruteForceProtector(auth_store, max_attempts=3, lockout_window_seconds=60)

        assert protector.is_locked_out("attacker") is False

        protector.record_failure("attacker")
        assert protector.is_locked_out("attacker") is False

        protector.record_failure("attacker")
        assert protector.is_locked_out("attacker") is False

        # 3rd failure locks out
        is_locked = protector.record_failure("attacker")
        assert is_locked is True
        assert protector.is_locked_out("attacker") is True

        # Successful login clears attempts
        protector.record_success("attacker")
        assert protector.is_locked_out("attacker") is False
