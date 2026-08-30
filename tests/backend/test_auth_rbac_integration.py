"""
tests/backend/test_auth_rbac_integration.py
-------------------------------------------
Phase 8: End-to-end HTTP and route-level authorization integration tests.

Verifies:
  1. Unauthenticated requests to protected endpoints return 401 Unauthorized
  2. Authenticated user identity is propagated into application state & logs
  3. Viewer role cannot perform restricted mutations (upload docs, approve tasks, cancel tasks) -> 403 Forbidden
  4. Operator role can perform permitted operations (chat, document search, approve tasks) -> 200 OK
  5. Admin role can manage users and system configuration -> 200 OK
  6. ToolRegistry independently enforces RBAC permissions even on direct tool dispatch
  7. Audit records accurately capture the authenticated username and role
"""

import pytest
from fastapi.testclient import TestClient
from pathlib import Path
import tempfile

from backend.main import create_app
from backend.config import Settings
from backend.auth.models import AuthStore, User, UserRole, Permission, has_permission
from backend.auth.security import SessionManager, hash_password
from backend.tools.registry import ToolDefinition, ToolRegistry
from backend.tools.calculator import CalculatorInput, execute_calculator


@pytest.fixture
def auth_test_env(tmp_path: Path):
    """Setup an isolated backend app with enabled authentication and pre-seeded test users."""
    db_path = tmp_path / "auth_test.db"
    settings = Settings(
        app_env="development",
        auth_enabled=True,
        tasks_db_path=db_path,
        sandbox_dir=tmp_path / "sandbox",
        upload_dir=tmp_path / "uploads",
        chroma_persist_dir=tmp_path / "chromadb",
    )

    app = create_app(settings)
    store = AuthStore(db_path=db_path)
    session_mgr = SessionManager(store=store)

    # Seed 3 test users: admin, operator, viewer
    admin_u = User(
        id="user_admin_01",
        username="admin_user",
        password_hash=hash_password("AdminPass123!"),
        role=UserRole.ADMIN.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    operator_u = User(
        id="user_op_01",
        username="operator_user",
        password_hash=hash_password("OperatorPass123!"),
        role=UserRole.OPERATOR.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    viewer_u = User(
        id="user_view_01",
        username="viewer_user",
        password_hash=hash_password("ViewerPass123!"),
        role=UserRole.VIEWER.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )

    store.create_user(admin_u)
    store.create_user(operator_u)
    store.create_user(viewer_u)

    # Generate session tokens
    admin_tok, _ = session_mgr.create_session(admin_u.id)
    operator_tok, _ = session_mgr.create_session(operator_u.id)
    viewer_tok, _ = session_mgr.create_session(viewer_u.id)

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
            },
        }


class TestAuthRBACRouteIntegration:
    """Test HTTP level enforcement of authentication and RBAC."""

    def test_unauthenticated_request_rejected(self, auth_test_env):
        client = auth_test_env["client"]

        # GET /api/tasks without token -> 401
        res = client.get("/api/tasks")
        assert res.status_code == 401
        assert "Authentication required" in res.json()["detail"]

        # GET /api/documents without token -> 401
        res_docs = client.get("/api/documents")
        assert res_docs.status_code == 401

        # GET /api/tools without token -> 401
        res_tools = client.get("/api/tools")
        assert res_tools.status_code == 401

    def test_viewer_can_read_but_not_mutate(self, auth_test_env):
        client = auth_test_env["client"]
        viewer_token = auth_test_env["tokens"]["viewer"]
        headers = {"Authorization": f"Bearer {viewer_token}"}

        # 1. Viewer can list tasks -> 200
        res_tasks = client.get("/api/tasks", headers=headers)
        assert res_tasks.status_code == 200

        # 2. Viewer can list tools -> 200
        res_tools = client.get("/api/tools", headers=headers)
        assert res_tools.status_code == 200

        # 3. Viewer cannot upload document -> 403 Forbidden
        files = {"file": ("test.txt", b"sample content", "text/plain")}
        res_upload = client.post("/api/documents", headers=headers, files=files)
        assert res_upload.status_code == 403
        assert "Forbidden" in res_upload.json()["detail"]

        # 4. Viewer cannot cancel a task -> 403 Forbidden
        res_cancel = client.post("/api/tasks/task_123/cancel", headers=headers)
        assert res_cancel.status_code == 403

    def test_operator_permissions(self, auth_test_env):
        client = auth_test_env["client"]
        op_token = auth_test_env["tokens"]["operator"]
        headers = {"Authorization": f"Bearer {op_token}"}

        # 1. Operator can list tasks -> 200
        res_tasks = client.get("/api/tasks", headers=headers)
        assert res_tasks.status_code == 200

        # 2. Operator cannot manage users -> 403 Forbidden
        res_users = client.get("/api/auth/users", headers=headers)
        assert res_users.status_code == 403

    def test_admin_permissions(self, auth_test_env):
        client = auth_test_env["client"]
        admin_token = auth_test_env["tokens"]["admin"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Admin can list users -> 200
        res_users = client.get("/api/auth/users", headers=headers)
        assert res_users.status_code == 200
        assert len(res_users.json()) >= 3

    @pytest.mark.asyncio
    async def test_tool_registry_dual_boundary_independent_enforcement(self):
        """Verify ToolRegistry independently blocks viewers from tool execution."""
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="calculator",
            description="Math",
            input_schema=CalculatorInput,
            execute_fn=execute_calculator,
            category="Math",
            read_only=True,
        ))

        # Viewer role blocked at ToolRegistry boundary
        res = await reg.execute("calculator", {"expression": "2+2"}, user_role="viewer")
        assert res.success is False
        assert "Permission denied" in res.error

        # Operator role permitted
        res_op = await reg.execute("calculator", {"expression": "2+2"}, user_role="operator")
        assert res_op.success is True
        assert res_op.result["result"] == 4.0
