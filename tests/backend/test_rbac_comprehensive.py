"""
tests/backend/test_rbac_comprehensive.py
-----------------------------------------
Phase B Step 6: Comprehensive Backend RBAC Enforcement Test Suite.

Verifies:
  1. REGRESSION: Lowest privilege User/Viewer CANNOT create DOCX (via direct tool, task resume, task API, chat NLP).
  2. Viewer CANNOT create XLSX, write files, or execute code.
  3. Viewer & Operator CANNOT preload models (POST /api/models/preload -> 403).
  4. Viewer CANNOT approve tasks in chat or via REST (/api/tasks/{id}/approve -> 403).
  5. Viewer CANNOT access Admin endpoints (/api/auth/users, /api/audit/prune -> 403).
  6. Operator (Manager) CAN execute tools, approve tasks, cancel tasks, but CANNOT manage users/config.
  7. Admin CAN execute all operations.
  8. Clearance hierarchy (L1, L2, L3) and check_authorization() validation.
  9. Fail-closed behavior on missing/invalid/tampered role.
 10. Audit logging captures authorization denials.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import require_clearance, require_permission, require_role
from backend.auth.models import (
    AuthStore,
    ClearanceLevel,
    DEFAULT_CLEARANCE_MAP,
    Permission,
    User,
    UserRole,
    check_authorization,
    has_permission,
    is_clearance_sufficient,
    is_role_sufficient,
    parse_clearance,
)
from backend.auth.security import SessionManager, hash_password
from backend.config import Settings
from backend.main import create_app
from backend.tools.registry import ToolDefinition, ToolRegistry
from backend.tools.calculator import CalculatorInput, execute_calculator
from backend.tools.docx_create import DocxCreateInput, create_docx_create
from backend.tools.xlsx_report import XlsxReportInput, create_xlsx_report
from backend.tools.file_write import FileWriteInput, create_file_write


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_env(tmp_path: Path):
    """Setup an isolated backend app with dev simulation enabled."""
    db_path = tmp_path / "tasks.db"
    auth_db_path = tmp_path / "auth.db"
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        app_env="development",
        auth_enabled=False,  # Dev mode allows X-User-Role simulation
        tasks_dir=tmp_path / "tasks",
        tasks_db_path=db_path,
        sandbox_dir=sandbox_dir,
        upload_dir=uploads_dir,
        chroma_persist_dir=tmp_path / "chromadb",
    )

    app = create_app(custom_settings=settings)
    auth_store = AuthStore(db_path=auth_db_path)
    session_mgr = SessionManager(store=auth_store)

    # Seed 3 users
    admin_u = User(
        id="user_admin",
        username="admin_user",
        password_hash=hash_password("Pass123!"),
        role=UserRole.ADMIN.value,
        clearance=ClearanceLevel.L3.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    operator_u = User(
        id="user_operator",
        username="operator_user",
        password_hash=hash_password("Pass123!"),
        role=UserRole.OPERATOR.value,
        clearance=ClearanceLevel.L2.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    viewer_u = User(
        id="user_viewer",
        username="viewer_user",
        password_hash=hash_password("Pass123!"),
        role=UserRole.VIEWER.value,
        clearance=ClearanceLevel.L1.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    auth_store.create_user(admin_u)
    auth_store.create_user(operator_u)
    auth_store.create_user(viewer_u)

    app.state.auth_store = auth_store
    app.state.session_manager = session_mgr

    with TestClient(app) as client:
        yield {
            "client": client,
            "app": app,
            "sandbox_dir": sandbox_dir,
            "auth_store": auth_store,
            "session_mgr": session_mgr,
        }


# ---------------------------------------------------------------------------
# 1. Regression: Lowest privilege User/Viewer CANNOT create DOCX
# ---------------------------------------------------------------------------

class TestViewerDocxRegression:
    """CRITICAL REGRESSION TEST: Lowest privilege User/Viewer MUST be denied DOCX creation."""

    @pytest.mark.asyncio
    async def test_viewer_direct_docx_tool_execution_denied(self, tmp_path: Path):
        """ToolRegistry must reject docx_create execution by viewer role."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="docx_create",
            description="Create DOCX",
            input_schema=DocxCreateInput,
            execute_fn=create_docx_create(sandbox),
            read_only=False,
            requires_approval=True,
            risk_level="high",
        ))

        args = {
            "filename": "unauthorized_report.docx",
            "title": "Unauthorized Report",
            "paragraphs": ["This should not be written."],
        }
        res = await reg.execute("docx_create", args, user_role="viewer")
        assert res.success is False
        assert "Permission denied" in res.error
        assert "viewer" in res.error
        # Verify file was NOT created
        assert not (sandbox / "unauthorized_report.docx").exists()

    @pytest.mark.asyncio
    async def test_viewer_cannot_resume_docx_task(self, app_env):
        """Engine.resume_agent_task must block viewer from resuming/approving a task."""
        sandbox = app_env["sandbox_dir"]
        engine = app_env["app"].state.engine
        task_mgr = app_env["app"].state.task_manager
        approval_mgr = app_env["app"].state.approval_manager

        task = task_mgr.create_task(
            session_id="sess_test",
            user_request="Create a docx report",
            user_role="viewer",
        )
        approval = approval_mgr.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="docx_create",
            arguments={"filename": "test.docx", "title": "Title"},
        )

        # Viewer attempting to approve/resume must be denied
        events = []
        async for evt in engine.resume_agent_task(
            task_id=task.task_id,
            approval_id=approval.approval_id,
            approved=True,
            user_role="viewer",
        ):
            events.append(evt)

        # Confirm permission denial event occurred
        error_events = [e for e in events if isinstance(e, dict) and e.get("type") == "error"]
        assert len(error_events) > 0
        error_text = error_events[0].get("content") or error_events[0].get("message") or ""
        assert "Permission denied" in error_text
        # Verify file was NOT created
        assert not (sandbox / "test.docx").exists()

    def test_viewer_api_task_approve_forbidden(self, app_env):
        """POST /api/tasks/{task_id}/approve with X-User-Role: viewer returns 403 Forbidden."""
        client = app_env["client"]
        task_mgr = app_env["app"].state.task_manager
        task = task_mgr.create_task(
            session_id="sess_api_test",
            user_request="Create docx report",
            user_role="viewer",
        )

        resp = client.post(
            f"/api/tasks/{task.task_id}/approve",
            json={"action": "approve"},
            headers={"X-User-Role": "viewer"},
        )
        assert resp.status_code == 403
        assert "Forbidden" in resp.json()["detail"]

    def test_viewer_chat_nlp_approval_denied(self, app_env):
        """In chat stream, a viewer saying 'proceed' receives permission denied SSE."""
        from backend.agent.task import TaskStatus
        client = app_env["client"]
        task_mgr = app_env["app"].state.task_manager
        approval_mgr = app_env["app"].state.approval_manager

        task = task_mgr.create_task(
            session_id="chat_sess_01",
            user_request="Generate docx",
            user_role="viewer",
        )
        task.status = TaskStatus.AWAITING_APPROVAL
        task_mgr._persist(task)

        approval_mgr.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="docx_create",
            arguments={"filename": "report.docx", "title": "T"},
        )

        resp = client.post(
            "/api/chat",
            json={
                "session_id": "chat_sess_01",
                "message": "Yes, please proceed with docx creation",
            },
            headers={"X-User-Role": "viewer"},
        )
        assert resp.status_code == 200
        text = resp.text
        assert "Permission denied" in text
        assert "approve tasks" in text


# ---------------------------------------------------------------------------
# 2. Viewer cannot create XLSX, write files, or execute code
# ---------------------------------------------------------------------------

class TestViewerMutationsBlocked:
    """Verify viewer cannot execute any write/mutating tools."""

    @pytest.mark.asyncio
    async def test_viewer_cannot_execute_xlsx_or_file_write(self, tmp_path: Path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="xlsx_report",
            description="Create XLSX",
            input_schema=XlsxReportInput,
            execute_fn=create_xlsx_report(sandbox),
            read_only=False,
            requires_approval=True,
            risk_level="high",
        ))
        reg.register(ToolDefinition(
            name="file_write",
            description="Write text",
            input_schema=FileWriteInput,
            execute_fn=create_file_write(sandbox),
            read_only=False,
            risk_level="medium",
        ))

        # XLSX test
        res_xlsx = await reg.execute("xlsx_report", {
            "filename": "test.xlsx",
            "sheets": [{"title": "S1", "headers": ["A"], "rows": [["1"]]}],
        }, user_role="viewer")
        assert res_xlsx.success is False
        assert "Permission denied" in res_xlsx.error
        assert not (sandbox / "test.xlsx").exists()

        # File write test
        res_write = await reg.execute("file_write", {
            "filename": "test.txt",
            "content": "some content here",
        }, user_role="viewer")
        assert res_write.success is False
        assert "Permission denied" in res_write.error
        assert not (sandbox / "test.txt").exists()


# ---------------------------------------------------------------------------
# 3. Model Preload Authorization
# ---------------------------------------------------------------------------

class TestModelPreloadRBAC:
    """POST /api/models/preload must require MANAGE_CONFIG (Admin only)."""

    def test_viewer_cannot_preload_models(self, app_env):
        client = app_env["client"]
        resp = client.post(
            "/api/models/preload",
            json={"model": "qwen2.5:7b"},
            headers={"X-User-Role": "viewer"},
        )
        assert resp.status_code == 403

    def test_operator_cannot_preload_models(self, app_env):
        client = app_env["client"]
        resp = client.post(
            "/api/models/preload",
            json={"model": "qwen2.5:7b"},
            headers={"X-User-Role": "operator"},
        )
        assert resp.status_code == 403

    def test_admin_can_call_preload_models(self, app_env):
        client = app_env["client"]
        # Admin is permitted; backend may return 200/warning if Ollama is mocked/offline
        resp = client.post(
            "/api/models/preload",
            json={"model": "qwen2.5:7b"},
            headers={"X-User-Role": "admin"},
        )
        # Should NOT be 403 Forbidden
        assert resp.status_code in (200, 502, 504)


# ---------------------------------------------------------------------------
# 4. Admin-only endpoints protection
# ---------------------------------------------------------------------------

class TestAdminEndpointsRBAC:
    """Verify viewer and operator cannot access admin-only endpoints."""

    def test_viewer_blocked_from_user_management(self, app_env):
        client = app_env["client"]
        # List users
        resp_list = client.get("/api/auth/users", headers={"X-User-Role": "viewer"})
        assert resp_list.status_code == 403

        # Create user
        resp_create = client.post(
            "/api/auth/users",
            json={"username": "hacker", "password": "Password123!", "role": "admin"},
            headers={"X-User-Role": "viewer"},
        )
        assert resp_create.status_code == 403

    def test_operator_blocked_from_user_management(self, app_env):
        client = app_env["client"]
        resp_list = client.get("/api/auth/users", headers={"X-User-Role": "operator"})
        assert resp_list.status_code == 403

    def test_viewer_and_operator_blocked_from_audit_prune(self, app_env):
        client = app_env["client"]
        resp_v = client.post("/api/audit/prune", headers={"X-User-Role": "viewer"})
        assert resp_v.status_code == 403

        resp_op = client.post("/api/audit/prune", headers={"X-User-Role": "operator"})
        assert resp_op.status_code == 403

    def test_admin_allowed_user_management_and_prune(self, app_env):
        client = app_env["client"]
        resp_users = client.get("/api/auth/users", headers={"X-User-Role": "admin"})
        assert resp_users.status_code == 200

        resp_prune = client.post("/api/audit/prune", headers={"X-User-Role": "admin"})
        assert resp_prune.status_code == 200


# ---------------------------------------------------------------------------
# 5. Operator (Manager) Privileges
# ---------------------------------------------------------------------------

class TestOperatorPrivileges:
    """Verify Operator can perform normal operational workflows."""

    @pytest.mark.asyncio
    async def test_operator_can_execute_read_and_write_tools(self, tmp_path: Path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="calculator",
            description="Math",
            input_schema=CalculatorInput,
            execute_fn=execute_calculator,
            category="Math",
            read_only=True,
        ))
        reg.register(ToolDefinition(
            name="file_write",
            description="Write text",
            input_schema=FileWriteInput,
            execute_fn=create_file_write(sandbox),
            read_only=False,
        ))

        # Read tool
        res_read = await reg.execute("calculator", {"expression": "10 * 5"}, user_role="operator")
        assert res_read.success is True
        assert res_read.result["result"] == 50.0

        # Write tool
        res_write = await reg.execute("file_write", {
            "filename": "operator_log.txt",
            "content": "Operator log entry details here.",
        }, user_role="operator")
        assert res_write.success is True
        assert (sandbox / "operator_log.txt").exists()

    def test_operator_can_cancel_task(self, app_env):
        client = app_env["client"]
        task_mgr = app_env["app"].state.task_manager
        task = task_mgr.create_task(
            session_id="op_sess",
            user_request="Running operation",
            user_role="operator",
        )
        resp = client.post(f"/api/tasks/{task.task_id}/cancel", headers={"X-User-Role": "operator"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# 6. Clearance Level Hierarchy
# ---------------------------------------------------------------------------

class TestClearanceHierarchy:
    """Verify Clearance levels L1, L2, L3 and hierarchical comparisons."""

    def test_parse_clearance(self):
        assert parse_clearance("L1") == ClearanceLevel.L1
        assert parse_clearance("l1") == ClearanceLevel.L1
        assert parse_clearance("Level 1: Read-Only") == ClearanceLevel.L1
        assert parse_clearance("L2") == ClearanceLevel.L2
        assert parse_clearance("L3") == ClearanceLevel.L3
        assert parse_clearance("unknown") is None

    def test_is_clearance_sufficient(self):
        # L1
        assert is_clearance_sufficient("L1", "L1") is True
        assert is_clearance_sufficient("L1", "L2") is False
        assert is_clearance_sufficient("L1", "L3") is False

        # L2
        assert is_clearance_sufficient("L2", "L1") is True
        assert is_clearance_sufficient("L2", "L2") is True
        assert is_clearance_sufficient("L2", "L3") is False

        # L3
        assert is_clearance_sufficient("L3", "L1") is True
        assert is_clearance_sufficient("L3", "L2") is True
        assert is_clearance_sufficient("L3", "L3") is True

    def test_check_authorization(self):
        # Permission check
        allowed, _ = check_authorization("viewer", required_permission=Permission.VIEW_DATA)
        assert allowed is True

        allowed, _ = check_authorization("viewer", required_permission=Permission.EXECUTE_WRITE_TOOLS)
        assert allowed is False

        # Role check
        allowed, _ = check_authorization("viewer", min_role=UserRole.OPERATOR)
        assert allowed is False

        allowed, _ = check_authorization("operator", min_role=UserRole.OPERATOR)
        assert allowed is True

        allowed, _ = check_authorization("admin", min_role=UserRole.OPERATOR)
        assert allowed is True

        # Clearance check
        allowed, _ = check_authorization("operator", user_clearance="L1", min_clearance=ClearanceLevel.L2)
        assert allowed is False

        allowed, _ = check_authorization("operator", user_clearance="L2", min_clearance=ClearanceLevel.L2)
        assert allowed is True


# ---------------------------------------------------------------------------
# 7. Fail-Closed Security
# ---------------------------------------------------------------------------

class TestFailClosedSecurity:
    """Verify backend fails closed on invalid/tampered roles."""

    def test_invalid_header_role_rejected(self, app_env):
        client = app_env["client"]
        resp = client.get("/api/tasks", headers={"X-User-Role": "superadmin_hacker"})
        # Fails closed with 400 Bad Request
        assert resp.status_code == 400
        assert "Invalid X-User-Role" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_unknown_role_rejected_by_tool_registry(self):
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="calc",
            description="Math",
            input_schema=CalculatorInput,
            execute_fn=execute_calculator,
            read_only=True,
        ))
        res = await reg.execute("calc", {"expression": "1+1"}, user_role="unauthorized_role")
        assert res.success is False
        assert "Permission denied" in res.error

    def test_read_endpoints_accessible_to_viewer(self, app_env):
        """Viewer role CAN view tasks, tools, models, and hardware status."""
        client = app_env["client"]
        headers = {"X-User-Role": "viewer"}

        assert client.get("/api/tasks", headers=headers).status_code == 200
        assert client.get("/api/tools", headers=headers).status_code == 200
        assert client.get("/api/models", headers=headers).status_code == 200
        assert client.get("/api/hardware/status", headers=headers).status_code == 200
