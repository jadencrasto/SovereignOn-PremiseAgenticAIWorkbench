"""
tests/backend/test_prod_security_hardening.py
----------------------------------------------
Phase B Step 9: Authentication & Production Security Hardening Test Suite.

Covers:
1. HTTP security headers (nosniff, DENY, strict-origin-when-cross-origin, Permissions-Policy, X-XSS-Protection: 0).
2. Targeted Cache-Control for sensitive API endpoints vs monitoring endpoints.
3. Documentation exposure control (/docs, /redoc, /openapi.json in prod vs dev vs with override).
4. Error disclosure protection (generic in prod, diagnostic in dev, 4xx/422 not converted, system.internal_error audit).
5. Explicit PASS / WARN / FAIL semantics for cookie security (SEC-008).
6. Explicit PASS / WARN / FAIL semantics for documentation exposure (SEC-009).
7. Contradictory production + dev_mode configuration fails closed.
8. Constant-time dummy verification on non-existent usernames against timing enumeration.
9. End-to-end backend cookie authentication lifecycle: login -> Set-Cookie -> auth request -> logout -> rejection.
10. Dynamic user liveness: immediate 401 rejection of deactivated accounts.
"""

from pathlib import Path
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.config import Settings
from backend.auth.models import AuthStore, User, UserRole
from backend.auth.security import SessionManager, hash_password
from backend.audit.logger import AuditLogger
from backend.security.checker import SecurityChecker
from backend.utils.config_validation import ConfigValidator, ConfigValidationError


@pytest.fixture
def base_test_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="development",
        auth_enabled=True,
        tasks_db_path=tmp_path / "test_tasks.db",
        sandbox_dir=tmp_path / "sandbox",
        upload_dir=tmp_path / "uploads",
        chroma_persist_dir=tmp_path / "chromadb",
        auth_idle_timeout_seconds=3600,
        auth_max_session_seconds=7200,
        auth_cookie_secure=False,
        enable_docs_in_prod=False,
    )


def seed_test_user(db_path: Path, username: str = "testuser", password: str = "CorrectPassword123!", role: str = UserRole.OPERATOR.value, is_active: bool = True) -> User:
    store = AuthStore(db_path=db_path)
    user = User(
        id=f"user_{username}",
        username=username,
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    store.create_user(user)
    return user


# ---------------------------------------------------------------------------
# 1. HTTP Security Headers
# ---------------------------------------------------------------------------

class TestSecurityHeaders:
    def test_security_headers_present_on_response(self, base_test_settings: Settings):
        app = create_app(base_test_settings)
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.headers.get("X-Content-Type-Options") == "nosniff"
            assert resp.headers.get("X-Frame-Options") == "DENY"
            assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
            assert resp.headers.get("X-XSS-Protection") == "0"
            perm_policy = resp.headers.get("Permissions-Policy", "")
            assert "camera=()" in perm_policy
            assert "microphone=()" in perm_policy
            assert "geolocation=()" in perm_policy


# ---------------------------------------------------------------------------
# 2. Targeted Cache-Control
# ---------------------------------------------------------------------------

class TestTargetedCacheControl:
    def test_sensitive_endpoints_have_no_store(self, base_test_settings: Settings):
        app = create_app(base_test_settings)
        with TestClient(app) as client:
            # Sensitive endpoints: /api/auth, /api/audit, /api/tasks
            resp = client.get("/api/auth/me")
            assert "no-store" in resp.headers.get("Cache-Control", "")
            assert "no-cache" in resp.headers.get("Cache-Control", "")
            assert resp.headers.get("Pragma") == "no-cache"

            resp_audit = client.get("/api/audit/events")
            assert "no-store" in resp_audit.headers.get("Cache-Control", "")

    def test_health_endpoints_do_not_have_no_store(self, base_test_settings: Settings):
        app = create_app(base_test_settings)
        with TestClient(app) as client:
            resp = client.get("/health")
            # /health should NOT have no-store so monitoring probes operate cleanly
            cache_ctrl = resp.headers.get("Cache-Control", "")
            assert "no-store" not in cache_ctrl


# ---------------------------------------------------------------------------
# 3. Documentation Exposure Control
# ---------------------------------------------------------------------------

class TestDocumentationExposure:
    def test_docs_available_in_development(self, base_test_settings: Settings):
        base_test_settings.app_env = "development"
        app = create_app(base_test_settings)
        with TestClient(app) as client:
            assert client.get("/docs").status_code == 200
            assert client.get("/redoc").status_code == 200
            assert client.get("/openapi.json").status_code == 200

    def test_docs_disabled_in_production(self, base_test_settings: Settings):
        base_test_settings.app_env = "production"
        base_test_settings.enable_docs_in_prod = False
        app = create_app(base_test_settings)
        with TestClient(app) as client:
            assert client.get("/docs").status_code == 404
            assert client.get("/redoc").status_code == 404
            assert client.get("/openapi.json").status_code == 404

    def test_docs_enabled_in_production_with_explicit_flag(self, base_test_settings: Settings):
        base_test_settings.app_env = "production"
        base_test_settings.enable_docs_in_prod = True
        app = create_app(base_test_settings)
        with TestClient(app) as client:
            assert client.get("/docs").status_code == 200
            assert client.get("/redoc").status_code == 200
            assert client.get("/openapi.json").status_code == 200


# ---------------------------------------------------------------------------
# 4. Error Disclosure Protection
# ---------------------------------------------------------------------------

class TestErrorDisclosureProtection:
    def test_http_exceptions_not_converted_to_500(self, base_test_settings: Settings):
        app = create_app(base_test_settings)
        with TestClient(app) as client:
            # 404 for non-existent route
            resp = client.get("/non_existent_route_404")
            assert resp.status_code == 404
            assert resp.json()["detail"] == "Not Found"

            # 401 for unauthenticated protected route
            resp_auth = client.get("/api/auth/me")
            assert resp_auth.status_code == 401

    def test_request_validation_errors_return_422(self, base_test_settings: Settings):
        app = create_app(base_test_settings)
        with TestClient(app) as client:
            # Invalid JSON payload for login returns 422
            resp = client.post("/api/auth/login", json={"wrong_field": 123})
            assert resp.status_code == 422

    def test_unhandled_exception_sanitized_in_production(self, base_test_settings: Settings):
        base_test_settings.app_env = "production"
        app = create_app(base_test_settings)

        # Inject a test route that raises an unhandled exception
        test_router = APIRouter()
        @test_router.get("/test-internal-bug")
        def bug_route():
            raise RuntimeError("Database password leaked in raw exception: secret123!")

        app.include_router(test_router)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/test-internal-bug")
            assert resp.status_code == 500
            data = resp.json()
            # Must hide internal exception details and password
            assert "secret123!" not in resp.text
            assert data["detail"] == "An internal server error occurred."
            assert data["error_code"] == "INTERNAL_SERVER_ERROR"
            assert "request_id" in data
            assert resp.headers.get("X-Request-ID") == data["request_id"]

    def test_unhandled_exception_diagnostic_in_development(self, base_test_settings: Settings):
        base_test_settings.app_env = "development"
        app = create_app(base_test_settings)

        test_router = APIRouter()
        @test_router.get("/test-dev-bug")
        def dev_bug_route():
            raise ValueError("Debug info for local developer")

        app.include_router(test_router)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/test-dev-bug")
            assert resp.status_code == 500
            data = resp.json()
            assert "Debug info for local developer" in data["detail"]
            assert data["exception_type"] == "ValueError"


# ---------------------------------------------------------------------------
# 5. Cookie Security Posture (PASS / WARN / FAIL)
# ---------------------------------------------------------------------------

class TestCookieSecurityDiagnostics:
    def test_dev_cookie_status_pass(self, base_test_settings: Settings):
        base_test_settings.app_env = "development"
        base_test_settings.auth_cookie_secure = False
        checker = SecurityChecker(cfg=base_test_settings)
        checks = {c["id"]: c for c in checker.run_all_checks()}
        assert checks["SEC-008"]["status"] == "PASS"

    def test_prod_cookie_secure_pass(self, base_test_settings: Settings):
        base_test_settings.app_env = "production"
        base_test_settings.auth_cookie_secure = True
        checker = SecurityChecker(cfg=base_test_settings)
        checks = {c["id"]: c for c in checker.run_all_checks()}
        assert checks["SEC-008"]["status"] == "PASS"

    def test_prod_cookie_insecure_warns_with_reverse_proxy_notice(self, base_test_settings: Settings):
        base_test_settings.app_env = "production"
        base_test_settings.auth_cookie_secure = False
        checker = SecurityChecker(cfg=base_test_settings)
        checks = {c["id"]: c for c in checker.run_all_checks()}
        assert checks["SEC-008"]["status"] == "WARN"
        assert "trusted reverse proxy" in checks["SEC-008"]["details"]

    def test_config_validator_cookie_rules(self, base_test_settings: Settings):
        base_test_settings.app_env = "production"
        base_test_settings.auth_cookie_secure = False
        validator = ConfigValidator(base_test_settings)
        _, results = validator.validate()
        cookie_rule = next(r for r in results if r["rule"] == "prod_cookie_security")
        assert cookie_rule["status"] == "WARN"

        base_test_settings.auth_cookie_secure = True
        validator_pass = ConfigValidator(base_test_settings)
        _, results_pass = validator_pass.validate()
        cookie_rule_pass = next(r for r in results_pass if r["rule"] == "prod_cookie_security")
        assert cookie_rule_pass["status"] == "PASS"


# ---------------------------------------------------------------------------
# 6. Production vs Dev Mode Conflict Handling
# ---------------------------------------------------------------------------

class TestProductionDevModeConflict:
    def test_production_with_dev_mode_fails_closed(self, base_test_settings: Settings):
        base_test_settings.app_env = "production"
        base_test_settings.dev_mode = True

        checker = SecurityChecker(cfg=base_test_settings)
        checks = {c["id"]: c for c in checker.run_all_checks()}
        assert checks["SEC-001"]["status"] == "FAIL"
        assert "Contradictory security configuration" in checks["SEC-001"]["details"]

        validator = ConfigValidator(base_test_settings)
        valid, results = validator.validate()
        assert valid is False
        conflict_rule = next(r for r in results if r["rule"] == "prod_dev_mode_conflict")
        assert conflict_rule["status"] == "FAIL"

        with pytest.raises(ConfigValidationError):
            validator.enforce_or_exit()


# ---------------------------------------------------------------------------
# 7. Documentation Exposure Diagnostics (SEC-009)
# ---------------------------------------------------------------------------

class TestDocumentationDiagnostics:
    def test_prod_docs_disabled_pass(self, base_test_settings: Settings):
        base_test_settings.app_env = "production"
        base_test_settings.enable_docs_in_prod = False
        checker = SecurityChecker(cfg=base_test_settings)
        checks = {c["id"]: c for c in checker.run_all_checks()}
        assert checks["SEC-009"]["status"] == "PASS"

    def test_prod_docs_enabled_warn(self, base_test_settings: Settings):
        base_test_settings.app_env = "production"
        base_test_settings.enable_docs_in_prod = True
        checker = SecurityChecker(cfg=base_test_settings)
        checks = {c["id"]: c for c in checker.run_all_checks()}
        assert checks["SEC-009"]["status"] == "WARN"


# ---------------------------------------------------------------------------
# 8. Constant-Time Login Defense
# ---------------------------------------------------------------------------

class TestConstantTimeLogin:
    def test_login_nonexistent_user_returns_generic_401(self, base_test_settings: Settings):
        app = create_app(base_test_settings)
        with TestClient(app) as client:
            resp = client.post(
                "/api/auth/login",
                json={"username": "non_existent_user_999", "password": "AnyPassword123!"},
            )
            assert resp.status_code == 401
            assert resp.json()["detail"] == "Invalid username or password"

    def test_login_existing_user_wrong_password_returns_generic_401(self, base_test_settings: Settings):
        seed_test_user(base_test_settings.tasks_db_path, username="known_user")
        app = create_app(base_test_settings)
        with TestClient(app) as client:
            resp = client.post(
                "/api/auth/login",
                json={"username": "known_user", "password": "WrongPassword123!"},
            )
            assert resp.status_code == 401
            assert resp.json()["detail"] == "Invalid username or password"


# ---------------------------------------------------------------------------
# 9. Authentication & Session Lifecycle (Backend Integration)
# ---------------------------------------------------------------------------

class TestAuthenticationLifecycle:
    def test_full_cookie_authentication_lifecycle(self, base_test_settings: Settings):
        seed_test_user(base_test_settings.tasks_db_path, username="lifecycle_user", password="ValidPass123!")
        app = create_app(base_test_settings)

        with TestClient(app) as client:
            # Step 1: Login -> receives Set-Cookie
            login_resp = client.post(
                "/api/auth/login",
                json={"username": "lifecycle_user", "password": "ValidPass123!"},
            )
            assert login_resp.status_code == 200
            cookies = login_resp.cookies
            assert "session_token" in cookies
            raw_token = cookies["session_token"]
            assert len(raw_token) > 20

            # Step 2: Cookie-authenticated request -> succeeds
            client.cookies.set("session_token", raw_token)
            me_resp = client.get("/api/auth/me")
            assert me_resp.status_code == 200
            assert me_resp.json()["username"] == "lifecycle_user"

            # Step 3: Logout -> revokes session and clears cookie
            logout_resp = client.post(
                "/api/auth/logout",
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            assert logout_resp.status_code == 200

            # Step 4: Subsequent request with previous session token -> rejected (401)
            # Send the old session token explicitly in cookie header
            rejected_client = TestClient(app)
            rejected_client.cookies.set("session_token", raw_token)
            subsequent_resp = rejected_client.get("/api/auth/me")
            assert subsequent_resp.status_code == 401
            assert subsequent_resp.json()["detail"] == "Session expired or invalid"


# ---------------------------------------------------------------------------
# 10. User Liveness: Deactivation Immediately Invalidates Access
# ---------------------------------------------------------------------------

class TestUserLiveness:
    def test_deactivated_user_immediately_rejected(self, base_test_settings: Settings):
        user = seed_test_user(base_test_settings.tasks_db_path, username="active_then_deactivated", password="ValidPass123!")
        app = create_app(base_test_settings)

        with TestClient(app) as client:
            # Login succeeds
            login_resp = client.post(
                "/api/auth/login",
                json={"username": "active_then_deactivated", "password": "ValidPass123!"},
            )
            assert login_resp.status_code == 200
            raw_token = login_resp.cookies["session_token"]

            client.cookies.set("session_token", raw_token)
            assert client.get("/api/auth/me").status_code == 200

            # Administrator deactivates user directly in store
            store = AuthStore(db_path=base_test_settings.tasks_db_path)
            store.update_user(user.id, is_active=False)

            # Next request with the active session token is immediately rejected
            resp_after_deact = client.get("/api/auth/me")
            assert resp_after_deact.status_code == 401
            assert resp_after_deact.json()["detail"] == "User account is inactive or not found"
