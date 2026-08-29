"""
tests/backend/test_security_checks.py
-------------------------------------
Phase 7 tests for SecurityChecker diagnostics.
"""

import pytest
from pathlib import Path

from backend.auth.models import AuthStore, User, UserRole
from backend.config import Settings
from backend.security.checker import SecurityChecker


class TestSecurityDiagnostics:
    """Test SecurityChecker PASS / WARN / FAIL rules."""

    def test_auth_mode_diagnostics(self):
        # 1. Dev mode warning
        cfg_dev = Settings(app_env="development", auth_enabled=False)
        checker_dev = SecurityChecker(cfg=cfg_dev)
        checks = {c["id"]: c for c in checker_dev.run_all_checks()}
        assert checks["SEC-001"]["status"] == "WARN"

        # 2. Prod mode failure
        cfg_prod_fail = Settings(app_env="production", auth_enabled=False)
        checker_prod_fail = SecurityChecker(cfg=cfg_prod_fail)
        checks = {c["id"]: c for c in checker_prod_fail.run_all_checks()}
        assert checks["SEC-001"]["status"] == "FAIL"

        # 3. Enabled auth pass
        cfg_pass = Settings(app_env="production", auth_enabled=True)
        checker_pass = SecurityChecker(cfg=cfg_pass)
        checks = {c["id"]: c for c in checker_pass.run_all_checks()}
        assert checks["SEC-001"]["status"] == "PASS"

    def test_default_credentials_diagnostic(self, tmp_path: Path):
        db_file = tmp_path / "test_sec_users.db"
        auth_store = AuthStore(db_path=db_file)

        # Seed user with must_change_password = True
        auth_store.create_user(User(
            id="user_admin",
            username="admin",
            password_hash="hash",
            role=UserRole.ADMIN.value,
            must_change_password=True,
            created_at="2026-01-01T00:00:00Z",
        ))

        checker = SecurityChecker(auth_store=auth_store)
        checks = {c["id"]: c for c in checker.run_all_checks()}
        assert checks["SEC-002"]["status"] == "WARN"

        # Rotate password
        auth_store.update_user("user_admin", must_change_password=False)
        checks_after = {c["id"]: c for c in checker.run_all_checks()}
        assert checks_after["SEC-002"]["status"] == "PASS"

    def test_egress_diagnostic(self):
        cfg_local = Settings(ollama_base_url="http://localhost:11434")
        checker_local = SecurityChecker(cfg=cfg_local)
        checks = {c["id"]: c for c in checker_local.run_all_checks()}
        assert checks["SEC-003"]["status"] == "PASS"

        cfg_ext = Settings(ollama_base_url="http://external-api.corp.net:11434")
        checker_ext = SecurityChecker(cfg=cfg_ext)
        checks_ext = {c["id"]: c for c in checker_ext.run_all_checks()}
        assert checks_ext["SEC-003"]["status"] == "WARN"
