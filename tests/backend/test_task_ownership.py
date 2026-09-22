"""
tests/backend/test_task_ownership.py
------------------------------------
Phase D — Step 13: Tests for task ID ownership, IDOR protection,
SSE task ID propagation, tool audit linkage, and task ID entropy.
"""

import json
import re
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.agent.planner import AgentPlan, PlanStep, StepStatus
from backend.agent.task import TaskManager, TaskState, TaskStatus
from backend.agent.task_store import TaskStore
from backend.audit.logger import AuditLogger
from backend.auth.models import ClearanceLevel, Permission, User, UserRole, AuthStore
from backend.auth.security import SessionManager, hash_password
from backend.config import Settings
from backend.main import create_app
from backend.schemas.chat import StreamChunk
from backend.tools.calculator import CalculatorInput, execute_calculator
from backend.tools.registry import ToolDefinition, ToolRegistry


# ---------------------------------------------------------------------------
# Fixture: Multi-User Authenticated Environment
# ---------------------------------------------------------------------------

@pytest.fixture
def multi_user_env(tmp_path: Path):
    """Setup an isolated backend app with enabled authentication and pre-seeded test users."""
    db_path = tmp_path / "tasks.db"
    settings = Settings(
        app_env="development",
        auth_enabled=True,
        tasks_db_path=db_path,
        sandbox_dir=tmp_path / "sandbox",
        upload_dir=tmp_path / "uploads",
        chroma_persist_dir=tmp_path / "chromadb",
    )
    settings.sandbox_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    app = create_app(settings)
    auth_store = AuthStore(db_path=db_path)
    session_mgr = SessionManager(store=auth_store)

    # 1. Admin user
    admin_user = User(
        id="user_admin_01",
        username="admin_alice",
        password_hash=hash_password("AdminPass123!"),
        role=UserRole.ADMIN.value,
        clearance=ClearanceLevel.L3.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    # 2. Operator A (User A)
    op_a = User(
        id="user_op_a_01",
        username="operator_bob",
        password_hash=hash_password("OperatorPass123!"),
        role=UserRole.OPERATOR.value,
        clearance=ClearanceLevel.L2.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    # 3. Operator B (User B)
    op_b = User(
        id="user_op_b_01",
        username="operator_charlie",
        password_hash=hash_password("OperatorPass456!"),
        role=UserRole.OPERATOR.value,
        clearance=ClearanceLevel.L2.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    # 4. Viewer (read-only user)
    viewer_user = User(
        id="user_viewer_01",
        username="viewer_dave",
        password_hash=hash_password("ViewerPass123!"),
        role=UserRole.VIEWER.value,
        clearance=ClearanceLevel.L1.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )

    for u in (admin_user, op_a, op_b, viewer_user):
        auth_store.create_user(u)

    admin_tok, _ = session_mgr.create_session(admin_user.id)
    op_a_tok, _ = session_mgr.create_session(op_a.id)
    op_b_tok, _ = session_mgr.create_session(op_b.id)
    viewer_tok, _ = session_mgr.create_session(viewer_user.id)

    app.state.auth_store = auth_store
    app.state.session_manager = session_mgr

    with TestClient(app) as client:
        yield {
            "client": client,
            "app": app,
            "task_manager": app.state.task_manager,
            "users": {
                "admin": admin_user,
                "op_a": op_a,
                "op_b": op_b,
                "viewer": viewer_user,
            },
            "headers": {
                "admin": {"Authorization": f"Bearer {admin_tok}"},
                "op_a": {"Authorization": f"Bearer {op_a_tok}"},
                "op_b": {"Authorization": f"Bearer {op_b_tok}"},
                "viewer": {"Authorization": f"Bearer {viewer_tok}"},
            },
        }


# ---------------------------------------------------------------------------
# Priority 1: Task Ownership and IDOR Protection Tests
# ---------------------------------------------------------------------------

class TestTaskOwnershipAndIDOR:
    """Test multi-user task isolation, ownership verification, and IDOR prevention."""

    def test_user_a_cannot_view_user_b_task(self, multi_user_env):
        """User A (Operator Bob) cannot view User B's (Operator Charlie's) task; returns 404."""
        tm: TaskManager = multi_user_env["task_manager"]
        client: TestClient = multi_user_env["client"]

        # Create task owned by Operator B
        task_b = tm.create_task(
            session_id="sess_b_01",
            user_request="Charlie's confidential task",
            user_id="user_op_b_01",
            user_role=UserRole.OPERATOR.value,
        )

        # Operator A attempts to view Operator B's task
        resp = client.get(
            f"/api/tasks/{task_b.task_id}",
            headers=multi_user_env["headers"]["op_a"],
        )
        # Must return 404 to avoid leaking task existence
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_user_a_cannot_cancel_user_b_task(self, multi_user_env):
        """User A (Operator Bob) cannot cancel User B's (Operator Charlie's) task; returns 404."""
        tm: TaskManager = multi_user_env["task_manager"]
        client: TestClient = multi_user_env["client"]

        task_b = tm.create_task(
            session_id="sess_b_02",
            user_request="Charlie's in-progress task",
            user_id="user_op_b_01",
            user_role=UserRole.OPERATOR.value,
        )

        # Operator A attempts to cancel Operator B's task
        resp = client.post(
            f"/api/tasks/{task_b.task_id}/cancel",
            headers=multi_user_env["headers"]["op_a"],
        )
        assert resp.status_code == 404

        # Verify task was NOT cancelled
        persisted = tm.get_task(task_b.task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.PENDING

    def test_task_list_excludes_other_users_tasks(self, multi_user_env):
        """Task list endpoint returns ONLY the calling user's tasks."""
        tm: TaskManager = multi_user_env["task_manager"]
        client: TestClient = multi_user_env["client"]

        # Create tasks for Operator A and Operator B
        task_a = tm.create_task(
            session_id="sess_a_list",
            user_request="Bob's task 1",
            user_id="user_op_a_01",
            user_role=UserRole.OPERATOR.value,
        )
        task_b = tm.create_task(
            session_id="sess_b_list",
            user_request="Charlie's task 1",
            user_id="user_op_b_01",
            user_role=UserRole.OPERATOR.value,
        )

        # Bob requests task list
        resp_bob = client.get("/api/tasks", headers=multi_user_env["headers"]["op_a"])
        assert resp_bob.status_code == 200
        bob_tasks = resp_bob.json()["tasks"]
        bob_task_ids = [t["task_id"] for t in bob_tasks]
        assert task_a.task_id in bob_task_ids
        assert task_b.task_id not in bob_task_ids

        # Charlie requests task list
        resp_charlie = client.get("/api/tasks", headers=multi_user_env["headers"]["op_b"])
        assert resp_charlie.status_code == 200
        charlie_tasks = resp_charlie.json()["tasks"]
        charlie_task_ids = [t["task_id"] for t in charlie_tasks]
        assert task_b.task_id in charlie_task_ids
        assert task_a.task_id not in charlie_task_ids

    def test_administrator_can_view_and_cancel_any_task(self, multi_user_env):
        """Administrators can view all tasks and cancel any user's task."""
        tm: TaskManager = multi_user_env["task_manager"]
        client: TestClient = multi_user_env["client"]

        task_a = tm.create_task(
            session_id="sess_a_admin_test",
            user_request="Bob's task for admin review",
            user_id="user_op_a_01",
            user_role=UserRole.OPERATOR.value,
        )

        # Admin views Bob's task
        resp_view = client.get(
            f"/api/tasks/{task_a.task_id}",
            headers=multi_user_env["headers"]["admin"],
        )
        assert resp_view.status_code == 200
        assert resp_view.json()["task_id"] == task_a.task_id

        # Admin lists all tasks -> sees Bob's task
        resp_list = client.get(
            "/api/tasks",
            headers=multi_user_env["headers"]["admin"],
        )
        assert resp_list.status_code == 200
        all_ids = [t["task_id"] for t in resp_list.json()["tasks"]]
        assert task_a.task_id in all_ids

        # Admin cancels Bob's task
        resp_cancel = client.post(
            f"/api/tasks/{task_a.task_id}/cancel",
            headers=multi_user_env["headers"]["admin"],
        )
        assert resp_cancel.status_code == 200
        assert resp_cancel.json()["status"] == TaskStatus.CANCELLED

    def test_legacy_null_owner_tasks_safe_handling(self, multi_user_env):
        """Legacy tasks with user_id=NULL return 404 to regular users and are accessible to Admin."""
        tm: TaskManager = multi_user_env["task_manager"]
        client: TestClient = multi_user_env["client"]

        # Create legacy task with user_id=None
        legacy_task = tm.create_task(
            session_id="sess_legacy",
            user_request="Legacy task with no owner",
            user_id=None,
            user_role=None,
        )

        # Regular operator cannot view legacy task
        resp_op = client.get(
            f"/api/tasks/{legacy_task.task_id}",
            headers=multi_user_env["headers"]["op_a"],
        )
        assert resp_op.status_code == 404

        # Regular operator does not see legacy task in list
        resp_list = client.get(
            "/api/tasks",
            headers=multi_user_env["headers"]["op_a"],
        )
        assert legacy_task.task_id not in [t["task_id"] for t in resp_list.json()["tasks"]]

        # Administrator CAN view legacy task
        resp_admin = client.get(
            f"/api/tasks/{legacy_task.task_id}",
            headers=multi_user_env["headers"]["admin"],
        )
        assert resp_admin.status_code == 200
        assert resp_admin.json()["task_id"] == legacy_task.task_id

    def test_task_creation_stores_authenticated_user_id(self, tmp_path: Path):
        """TaskManager stores user_id and persists it across reloads."""
        db_file = tmp_path / "test_store.db"
        store = TaskStore(db_path=db_file)
        manager = TaskManager(store=store)

        task = manager.create_task(
            session_id="sess_store_uid",
            user_request="Audit user_id storage",
            user_id="user_stable_12345",
            user_role="operator",
        )
        assert task.user_id == "user_stable_12345"

        reloaded = manager.get_task(task.task_id)
        assert reloaded is not None
        assert reloaded.user_id == "user_stable_12345"


# ---------------------------------------------------------------------------
# Priority 2: SSE Task ID Consistency Tests
# ---------------------------------------------------------------------------

class TestSSETaskIdConsistency:
    """Test that StreamChunk serializes task_id and SSE event streams expose task_id consistently."""

    def test_stream_chunk_task_id_serialization(self):
        """StreamChunk includes task_id in json output."""
        chunk = StreamChunk(
            type="task_started",
            content="task_1234567890abcdef1234567890abcdef",
            task_id="task_1234567890abcdef1234567890abcdef",
            session_id="sess_chunk_test",
        )
        raw_json = chunk.model_dump_json()
        parsed = json.loads(raw_json)

        assert parsed["type"] == "task_started"
        assert parsed["task_id"] == "task_1234567890abcdef1234567890abcdef"
        assert parsed["content"] == "task_1234567890abcdef1234567890abcdef"

    def test_task_completed_chunk_task_id(self):
        """StreamChunk task_completed event contains task_id."""
        chunk = StreamChunk(
            type="task_completed",
            content="task_completed_id",
            task_id="task_completed_id",
            session_id="sess_done",
        )
        parsed = json.loads(chunk.model_dump_json())
        assert parsed["task_id"] == "task_completed_id"


# ---------------------------------------------------------------------------
# Priority 3: Tool Execution Audit Linkage Tests
# ---------------------------------------------------------------------------

class TestToolAuditLinkage:
    """Test that tool execution audit logs capture task_id and step_id."""

    @pytest.mark.asyncio
    async def test_tool_execution_logs_task_and_step_id(self, tmp_path: Path):
        """Executing a tool via ToolRegistry forwards task_id and step_id to AuditLogger."""
        audit_db = tmp_path / "audit.db"
        audit_logger = AuditLogger(db_path=audit_db)

        registry = ToolRegistry()
        registry.set_audit_logger(audit_logger)

        registry.register(
            ToolDefinition(
                name="calculator",
                description="Math calculator",
                category="utility",
                input_schema=CalculatorInput,
                execute_fn=execute_calculator,
                read_only=True,
                risk_level="low",
            )
        )

        test_task_id = "task_aabbccddeeff00112233445566778899"
        test_step_id = "step_calc_01"

        result = await registry.execute(
            name="calculator",
            arguments={"expression": "100 * 5"},
            session_id="sess_calc_audit",
            user_role="operator",
            task_id=test_task_id,
            step_id=test_step_id,
        )
        assert result.success is True

        # Query the audit database
        entries = audit_logger.query_events(event_type="tool.execution")["events"]
        assert len(entries) >= 1
        last_entry = entries[0]
        assert last_entry["tool"] == "calculator"
        assert last_entry["task_id"] == test_task_id
        assert last_entry["step_id"] == test_step_id

    @pytest.mark.asyncio
    async def test_tool_arguments_cannot_override_audit_context(self, tmp_path: Path):
        """LLM tool arguments cannot spoof or overwrite the server-injected task_id."""
        audit_db = tmp_path / "audit_spoof.db"
        audit_logger = AuditLogger(db_path=audit_db)

        registry = ToolRegistry()
        registry.set_audit_logger(audit_logger)

        registry.register(
            ToolDefinition(
                name="calculator",
                description="Math calculator",
                category="utility",
                input_schema=CalculatorInput,
                execute_fn=execute_calculator,
                read_only=True,
                risk_level="low",
            )
        )

        trusted_task_id = "task_trusted_9999"
        trusted_step_id = "step_trusted_01"

        # Attacker injects task_id and step_id into tool arguments dict
        spoofed_args = {
            "expression": "40 + 2",
            "task_id": "task_ATTACKER_SPOOFED",
            "step_id": "step_ATTACKER_SPOOFED",
        }

        result = await registry.execute(
            name="calculator",
            arguments=spoofed_args,
            session_id="sess_spoof",
            user_role="operator",
            task_id=trusted_task_id,
            step_id=trusted_step_id,
        )
        assert result.success is True

        entries = audit_logger.query_events(event_type="tool.execution")["events"]
        assert len(entries) >= 1
        assert entries[0]["task_id"] == trusted_task_id
        assert entries[0]["step_id"] == trusted_step_id


# ---------------------------------------------------------------------------
# Priority 5: Task ID Entropy Tests
# ---------------------------------------------------------------------------

class TestTaskIdEntropy:
    """Test that task IDs provide 128-bit UUID4 entropy (32 hexadecimal digits)."""

    def test_task_id_format_matches_32_hex_uuid4(self):
        """Newly created TaskState generates IDs matching 'task_[0-9a-f]{32}'."""
        task = TaskState(
            session_id="sess_entropy",
            user_request="Test ID format",
        )
        pattern = r"^task_[0-9a-f]{32}$"
        assert re.match(pattern, task.task_id), (
            f"Expected task_id to match {pattern}, got: '{task.task_id}' (len={len(task.task_id)})"
        )
        assert len(task.task_id) == 37  # 5 chars ('task_') + 32 chars

    def test_task_id_batch_uniqueness(self):
        """Generate 1,000 task IDs to verify zero collisions across rapid creation."""
        ids = set()
        for _ in range(1000):
            t = TaskState(session_id="s", user_request="r")
            assert t.task_id not in ids, f"Collision detected for task ID: {t.task_id}"
            ids.add(t.task_id)
        assert len(ids) == 1000
