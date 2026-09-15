"""
tests/backend/test_auth_step7.py
---------------------------------
Phase B Step 7: Comprehensive Authentication, CSRF, and Endpoint Security Tests.

Covers all 20 required verification points:
1. Production request with no credentials -> 401.
2. Production request with invalid session -> 401.
3. Production request with inactive/expired session -> 401.
4. Production request with valid viewer session -> authenticated as viewer.
5. Production X-User-Role: admin cannot elevate viewer.
6. Production X-Clearance-Level: L3 cannot elevate viewer/operator.
7. Development no header -> dev_admin.
8. Development explicit viewer -> viewer.
9. Development explicit operator -> operator.
10. Development invalid role -> rejected (400).
11. Development invalid clearance -> rejected (400).
12. Protected cookie-authenticated mutation without CSRF defense -> rejected (403 with CSRF detail).
13. Explicit distinction:
    - missing CSRF header -> 403 (CSRF detail)
    - valid CSRF header + insufficient permission -> 403 (RBAC permission detail)
    - valid CSRF header + sufficient permission -> allowed.
14. Check representative mutations across task/document/user/model/audit/chat paths.
15. Bearer token requests not blocked by cookie-CSRF gating.
16. Protected read endpoints without auth in production -> 401.
17. Protected mutations without auth in production -> 401.
18. Authenticated insufficient-permission user -> 403.
19. Admin remains functional across protected endpoints.
20. Knowledge Graph clearance query param cannot elevate in production.
"""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.config import Settings
from backend.auth.models import AuthStore, ClearanceLevel, Permission, User, UserRole
from backend.auth.security import SessionManager, hash_password


@pytest.fixture
def prod_env(tmp_path: Path):
    """Production environment fixture with auth_enabled=True."""
    db_path = tmp_path / "prod_auth.db"
    settings = Settings(
        app_env="production",
        auth_enabled=True,
        tasks_db_path=db_path,
        sandbox_dir=tmp_path / "sandbox",
        upload_dir=tmp_path / "uploads",
        chroma_persist_dir=tmp_path / "chromadb",
        auth_idle_timeout_seconds=3600,
        auth_max_session_seconds=7200,
    )

    app = create_app(settings)
    store = AuthStore(db_path=db_path)
    session_mgr = SessionManager(
        store=store,
        idle_timeout_seconds=3600,
        absolute_timeout_seconds=7200,
    )

    # Seed admin, operator, viewer, and an inactive user
    admin_u = User(
        id="u_admin",
        username="admin_test",
        password_hash=hash_password("AdminPass123!"),
        role=UserRole.ADMIN.value,
        clearance=ClearanceLevel.L3.value,
        is_active=True,
        created_at="2026-01-01T00:00:00Z",
    )
    operator_u = User(
        id="u_op",
        username="op_test",
        password_hash=hash_password("OpPass123!"),
        role=UserRole.OPERATOR.value,
        clearance=ClearanceLevel.L2.value,
        is_active=True,
        created_at="2026-01-01T00:00:00Z",
    )
    viewer_u = User(
        id="u_view",
        username="view_test",
        password_hash=hash_password("ViewPass123!"),
        role=UserRole.VIEWER.value,
        clearance=ClearanceLevel.L1.value,
        is_active=True,
        created_at="2026-01-01T00:00:00Z",
    )
    inactive_u = User(
        id="u_inactive",
        username="inactive_test",
        password_hash=hash_password("InactivePass123!"),
        role=UserRole.OPERATOR.value,
        clearance=ClearanceLevel.L2.value,
        is_active=False,
        created_at="2026-01-01T00:00:00Z",
    )

    store.create_user(admin_u)
    store.create_user(operator_u)
    store.create_user(viewer_u)
    store.create_user(inactive_u)

    admin_tok, _ = session_mgr.create_session(admin_u.id)
    operator_tok, _ = session_mgr.create_session(operator_u.id)
    viewer_tok, _ = session_mgr.create_session(viewer_u.id)
    inactive_tok, _ = session_mgr.create_session(inactive_u.id)

    app.state.auth_store = store
    app.state.session_manager = session_mgr

    with TestClient(app) as client:
        yield {
            "client": client,
            "app": app,
            "store": store,
            "session_mgr": session_mgr,
            "tokens": {
                "admin": admin_tok,
                "operator": operator_tok,
                "viewer": viewer_tok,
                "inactive": inactive_tok,
            },
        }


@pytest.fixture
def dev_env(tmp_path: Path):
    """Development environment fixture with auth_enabled=False."""
    db_path = tmp_path / "dev_auth.db"
    settings = Settings(
        app_env="development",
        auth_enabled=False,
        tasks_db_path=db_path,
        sandbox_dir=tmp_path / "sandbox",
        upload_dir=tmp_path / "uploads",
        chroma_persist_dir=tmp_path / "chromadb",
    )

    app = create_app(settings)
    store = AuthStore(db_path=db_path)
    session_mgr = SessionManager(store=store)

    app.state.auth_store = store
    app.state.session_manager = session_mgr

    with TestClient(app) as client:
        yield {
            "client": client,
            "app": app,
            "store": store,
            "session_mgr": session_mgr,
        }


class TestProductionAuthenticationAndHeaderIsolation:
    """Tests 1 - 6: Production authentication and header spoofing isolation."""

    def test_1_prod_no_credentials_returns_401(self, prod_env):
        client = prod_env["client"]
        res = client.get("/api/tasks")
        assert res.status_code == 401
        assert "Authentication required" in res.json()["detail"]

        # Mutating route
        res_post = client.post("/api/documents", json={})
        assert res_post.status_code == 401

    def test_2_prod_invalid_session_returns_401(self, prod_env):
        client = prod_env["client"]
        headers = {"Authorization": "Bearer totally_bogus_token"}
        res = client.get("/api/tasks", headers=headers)
        assert res.status_code == 401
        assert "Session expired or invalid" in res.json()["detail"]

        # Cookie with invalid token
        res_cookie = client.get("/api/tasks", cookies={"session_token": "fake_cookie_token"})
        assert res_cookie.status_code == 401

    def test_3_prod_inactive_and_expired_session_returns_401(self, prod_env):
        client = prod_env["client"]
        inactive_tok = prod_env["tokens"]["inactive"]

        # Inactive user with valid session
        res = client.get("/api/tasks", headers={"Authorization": f"Bearer {inactive_tok}"})
        assert res.status_code == 401
        assert "inactive or not found" in res.json()["detail"]

        # Revoked / deleted session
        session_mgr = prod_env["session_mgr"]
        admin_tok = prod_env["tokens"]["admin"]
        session_mgr.revoke_session(admin_tok)

        res_revoked = client.get("/api/tasks", headers={"Authorization": f"Bearer {admin_tok}"})
        assert res_revoked.status_code == 401

    def test_4_prod_valid_viewer_session_authenticates_as_viewer(self, prod_env):
        client = prod_env["client"]
        viewer_tok = prod_env["tokens"]["viewer"]

        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {viewer_tok}"})
        assert res.status_code == 200
        body = res.json()
        assert body["username"] == "view_test"
        assert body["role"] == "viewer"
        assert body["clearance"] == "L1"

    def test_5_prod_x_user_role_admin_cannot_elevate_viewer(self, prod_env):
        client = prod_env["client"]
        viewer_tok = prod_env["tokens"]["viewer"]

        # Viewer tries to spoof admin role in production
        headers = {
            "Authorization": f"Bearer {viewer_tok}",
            "X-User-Role": "admin",
            "X-Clearance-Level": "L3",
        }
        # Checking own profile
        res_me = client.get("/api/auth/me", headers=headers)
        assert res_me.status_code == 200
        assert res_me.json()["role"] == "viewer"  # NOT elevated to admin!

        # Viewer tries to call an admin-only endpoint
        res_admin = client.get("/api/auth/users", headers=headers)
        assert res_admin.status_code == 403
        assert "Forbidden: 'admin' role or higher required" in res_admin.json()["detail"]

    def test_6_prod_x_clearance_header_cannot_elevate_clearance(self, prod_env):
        client = prod_env["client"]
        operator_tok = prod_env["tokens"]["operator"]

        headers = {
            "Authorization": f"Bearer {operator_tok}",
            "X-Clearance-Level": "L3",
        }
        res_me = client.get("/api/auth/me", headers=headers)
        assert res_me.status_code == 200
        assert res_me.json()["clearance"] == "L2"  # Retains database clearance, header ignored


class TestDevelopmentSimulationAndGuardrails:
    """Tests 7 - 11: Development mode simulation and fail-closed validation."""

    def test_7_dev_no_header_defaults_to_dev_admin(self, dev_env):
        client = dev_env["client"]
        res = client.get("/api/auth/me")
        assert res.status_code == 200
        body = res.json()
        assert body["username"] == "dev_admin"
        assert body["role"] == "admin"
        assert body["clearance"] == "L3"

    def test_8_dev_explicit_viewer_role(self, dev_env):
        client = dev_env["client"]
        headers = {"X-User-Role": "viewer"}
        res = client.get("/api/auth/me", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["username"] == "dev_viewer"
        assert body["role"] == "viewer"
        assert body["clearance"] == "L1"

        # Viewer cannot manage users in dev mode
        res_users = client.get("/api/auth/users", headers=headers)
        assert res_users.status_code == 403

    def test_9_dev_explicit_operator_role(self, dev_env):
        client = dev_env["client"]
        headers = {"X-User-Role": "operator"}
        res = client.get("/api/auth/me", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["username"] == "dev_operator"
        assert body["role"] == "operator"
        assert body["clearance"] == "L2"

    def test_10_dev_invalid_role_rejected(self, dev_env):
        client = dev_env["client"]
        headers = {"X-User-Role": "superuser"}
        res = client.get("/api/tasks", headers=headers)
        assert res.status_code == 400
        assert "Invalid X-User-Role header: 'superuser'" in res.json()["detail"]

    def test_11_dev_invalid_clearance_rejected(self, dev_env):
        client = dev_env["client"]
        headers = {
            "X-User-Role": "operator",
            "X-Clearance-Level": "TOP_SECRET_UNKNOWN",
        }
        res = client.get("/api/tasks", headers=headers)
        assert res.status_code == 400
        assert "Invalid X-Clearance-Level header: 'TOP_SECRET_UNKNOWN'" in res.json()["detail"]


class TestCSRFProtectionAndDistinctions:
    """Tests 12 - 15: Cookie CSRF protection and distinction from RBAC."""

    def test_12_cookie_mutation_without_csrf_rejected(self, prod_env):
        client = prod_env["client"]
        admin_tok = prod_env["tokens"]["admin"]

        # Admin tries to prune audit logs with cookie auth but without X-Requested-With
        res = client.post(
            "/api/audit/prune",
            json={"keep_days": 30},
            cookies={"session_token": admin_tok},
        )
        assert res.status_code == 403
        assert "CSRF protection: X-Requested-With header is required" in res.json()["detail"]

    def test_13_csrf_distinction_three_states(self, prod_env):
        """
        Explicitly verify the three distinct outcomes:
        1. Missing CSRF header -> 403 (CSRF detail)
        2. Valid CSRF header + Insufficient Permission -> 403 (RBAC permission detail)
        3. Valid CSRF header + Sufficient Permission -> Allowed
        """
        client = prod_env["client"]
        viewer_tok = prod_env["tokens"]["viewer"]
        admin_tok = prod_env["tokens"]["admin"]

        # State 1: Missing CSRF header on cookie request -> 403 CSRF
        res1 = client.post(
            "/api/models/preload",
            json={"model_id": "qwen2.5:7b"},
            cookies={"session_token": viewer_tok},
        )
        assert res1.status_code == 403
        assert "CSRF protection" in res1.json()["detail"]

        # State 2: Valid CSRF header + Insufficient Permission -> 403 RBAC
        res2 = client.post(
            "/api/models/preload",
            json={"model_id": "qwen2.5:7b"},
            cookies={"session_token": viewer_tok},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert res2.status_code == 403
        assert "Forbidden: permission 'manage_config' required" in res2.json()["detail"]

        # State 3: Valid CSRF header + Sufficient Permission -> Allowed
        res3 = client.post(
            "/api/audit/prune",
            json={"keep_days": 30},
            cookies={"session_token": admin_tok},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert res3.status_code == 200
        assert "deleted_rows" in res3.json()

    def test_14_csrf_across_representative_mutating_endpoints(self, prod_env):
        client = prod_env["client"]
        admin_tok = prod_env["tokens"]["admin"]
        cookies = {"session_token": admin_tok}

        mutations = [
            ("POST", "/api/chat", {"message": "hello"}),
            ("POST", "/api/tasks/task-001/approve", {"action": "approve"}),
            ("POST", "/api/tasks/task-001/cancel", {}),
            ("POST", "/api/models/preload", {"model_id": "qwen2.5:7b"}),
            ("POST", "/api/audit/prune", {"keep_days": 30}),
            ("POST", "/api/security/scan", {}),
            ("POST", "/api/auth/logout", {}),
            ("POST", "/api/auth/change-password", {"current_password": "p", "new_password": "new"}),
            ("POST", "/api/auth/users", {"username": "test_user_csrf", "password": "Password123!", "role": "operator"}),
            ("PATCH", "/api/auth/users/u_op", {"role": "operator"}),
        ]

        for method, path, payload in mutations:
            if method == "POST":
                res = client.post(path, json=payload, cookies=cookies)
            elif method == "PATCH":
                res = client.patch(path, json=payload, cookies=cookies)
            else:
                continue

            assert res.status_code == 403, f"{method} {path} should be rejected with 403 CSRF without header"
            assert "CSRF protection" in res.json()["detail"]

    def test_15_bearer_token_not_blocked_by_cookie_csrf(self, prod_env):
        """Bearer token requests are not ambient browser credentials and do not require X-Requested-With."""
        client = prod_env["client"]
        admin_tok = prod_env["tokens"]["admin"]
        headers = {"Authorization": f"Bearer {admin_tok}"}

        res = client.post("/api/audit/prune", json={"keep_days": 30}, headers=headers)
        assert res.status_code == 200
        assert "deleted_rows" in res.json()


class TestEndpointCoverageAndInformationLeakage:
    """Tests 16 - 20: Full endpoint audit coverage, information leakage, and Knowledge Graph."""

    def test_16_protected_read_endpoints_require_auth_in_prod(self, prod_env):
        client = prod_env["client"]
        read_endpoints = [
            "/api/tasks",
            "/api/documents",
            "/api/models",
            "/api/models/default",
            "/api/models/capabilities",
            "/api/models/scan",
            "/api/tools",
            "/api/artifacts",
            "/api/hardware/status",
            "/api/demo/scenarios",
            "/api/knowledge-graph",
            "/api/security/status",
            "/api/audit/events",
            "/api/audit/summary",
        ]

        for path in read_endpoints:
            res = client.get(path)
            assert res.status_code == 401, f"{path} did not return 401 when unauthenticated"
            assert "Authentication required" in res.json()["detail"]

    def test_17_knowledge_graph_query_param_cannot_elevate_in_prod(self, prod_env):
        client = prod_env["client"]
        viewer_tok = prod_env["tokens"]["viewer"]

        # Viewer tries to pass ?clearance=admin in production mode
        res = client.get(
            "/api/knowledge-graph?clearance=admin",
            headers={"Authorization": f"Bearer {viewer_tok}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["user_role"] == "viewer"
        assert data["effective_clearance"] == "viewer"  # NOT admin!
        assert data["clearance_level"] == 1

        # Classified nodes must be redacted stubs, not revealed
        classified_nodes = [n for n in data["nodes"] if n.get("category") == "restricted_stub"]
        assert len(classified_nodes) > 0
        for node in classified_nodes:
            assert node["properties"]["access_status"] == "DENIED_RBAC_GATE"

    def test_18_no_sensitive_information_leakage(self, prod_env):
        client = prod_env["client"]

        # 1. Invalid login should not disclose whether user exists
        res_login = client.post("/api/auth/login", json={"username": "nonexistent_user", "password": "wrong"})
        assert res_login.status_code == 401
        assert res_login.json()["detail"] == "Invalid username or password"

        # 2. Malformed tokens must not trigger 500 or leak stack trace
        res_malformed = client.get("/api/tasks", headers={"Authorization": "Bearer !!!malformed:::token##"})
        assert res_malformed.status_code == 401
        assert "detail" in res_malformed.json()
        assert "traceback" not in res_malformed.text.lower()
        assert "sqlite" not in res_malformed.text.lower()

    def test_19_admin_remains_functional_across_endpoints(self, prod_env):
        client = prod_env["client"]
        admin_tok = prod_env["tokens"]["admin"]
        headers = {"Authorization": f"Bearer {admin_tok}"}

        # Admin reads
        assert client.get("/api/demo/scenarios", headers=headers).status_code == 200
        assert client.get("/api/knowledge-graph", headers=headers).status_code == 200
        assert client.get("/api/tasks", headers=headers).status_code == 200
        assert client.get("/api/models", headers=headers).status_code == 200
        assert client.get("/api/auth/users", headers=headers).status_code == 200

        # Admin mutation
        res_user = client.post(
            "/api/auth/users",
            json={"username": "new_created_op", "password": "Password123!", "role": "operator"},
            headers=headers,
        )
        assert res_user.status_code == 201
        assert res_user.json()["username"] == "new_created_op"
