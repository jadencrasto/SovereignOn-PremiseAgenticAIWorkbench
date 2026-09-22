"""
tests/backend/test_task_lifecycle_concurrency.py
------------------------------------------------
Phase D — Step 14: Comprehensive regression tests for task lifecycle management,
cancellation and execution race conditions, approval consistency and RBAC,
startup recovery for PLANNING tasks, and concurrent persistence.
"""

import asyncio
import threading
import time
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from backend.agent.approval import ApprovalManager
from backend.agent.planner import AgentPlan, PlanStep, StepStatus
from backend.agent.task import TaskManager, TaskState, TaskStatus, TaskStateError, TERMINAL_TASK_STATUSES
from backend.agent.task_store import TaskStore
from backend.auth.models import ClearanceLevel, Permission, User, UserRole, AuthStore
from backend.auth.security import SessionManager, hash_password
from backend.config import Settings
from backend.main import create_app
from backend.tools.registry import ToolDefinition, ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def lifecycle_env(tmp_path: Path):
    db_file = tmp_path / "lifecycle_tasks.db"
    store = TaskStore(db_path=db_file)
    approval_mgr = ApprovalManager(store=store)
    task_mgr = TaskManager(store=store, approval_manager=approval_mgr)

    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name="test_tool",
        description="Test tool",
        input_schema=MagicMock(),
        execute_fn=lambda inp: "ok",
        enabled=True,
    ))

    return {
        "store": store,
        "task_mgr": task_mgr,
        "approval_mgr": approval_mgr,
        "tool_registry": reg,
    }


@pytest.fixture
def multi_user_app(tmp_path: Path):
    """Setup app with auth enabled and pre-seeded users for HTTP endpoint testing."""
    db_path = tmp_path / "tasks_api.db"
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

    admin_user = User(
        id="user_admin",
        username="admin_alice",
        password_hash=hash_password("AdminPass123!"),
        role=UserRole.ADMIN.value,
        clearance=ClearanceLevel.L3.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    op_a = User(
        id="user_op_a",
        username="operator_bob",
        password_hash=hash_password("OperatorPass123!"),
        role=UserRole.OPERATOR.value,
        clearance=ClearanceLevel.L2.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    op_b = User(
        id="user_op_b",
        username="operator_charlie",
        password_hash=hash_password("OperatorPass456!"),
        role=UserRole.OPERATOR.value,
        clearance=ClearanceLevel.L2.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )

    for u in (admin_user, op_a, op_b):
        auth_store.create_user(u)

    admin_tok, _ = session_mgr.create_session(admin_user.id)
    op_a_tok, _ = session_mgr.create_session(op_a.id)
    op_b_tok, _ = session_mgr.create_session(op_b.id)

    app.state.auth_store = auth_store
    app.state.session_manager = session_mgr

    with TestClient(app) as client:
        yield {
            "client": client,
            "app": app,
            "task_manager": app.state.task_manager,
            "approval_manager": app.state.approval_manager,
            "engine": app.state.engine,
            "users": {"admin": admin_user, "op_a": op_a, "op_b": op_b},
            "headers": {
                "admin": {"Authorization": f"Bearer {admin_tok}"},
                "op_a": {"Authorization": f"Bearer {op_a_tok}"},
                "op_b": {"Authorization": f"Bearer {op_b_tok}"},
            },
        }


# ---------------------------------------------------------------------------
# Priority 1: State Machine Integrity Tests
# ---------------------------------------------------------------------------

class TestStateMachineIntegrity:
    """Validate terminal states are fully protected against cancellation and invalid transitions."""

    def test_cancelling_failed_timeout_task_raises_error(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        task = task_mgr.create_task(session_id="s1", user_request="Timeout test")
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.EXECUTING)
        task_mgr.update_status(task.task_id, TaskStatus.FAILED_TIMEOUT, error="Timed out")

        with pytest.raises(TaskStateError, match="in terminal state"):
            task_mgr.cancel_task(task.task_id)

        task_after = task_mgr.get_task(task.task_id)
        assert task_after.status == TaskStatus.FAILED_TIMEOUT

    def test_cancelling_failed_interrupted_task_raises_error(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        task = task_mgr.create_task(session_id="s1", user_request="Interrupted test")
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.EXECUTING)
        task_mgr.update_status(task.task_id, TaskStatus.FAILED_INTERRUPTED, error="Server restart")

        with pytest.raises(TaskStateError, match="in terminal state"):
            task_mgr.cancel_task(task.task_id)

        task_after = task_mgr.get_task(task.task_id)
        assert task_after.status == TaskStatus.FAILED_INTERRUPTED

    def test_cancelling_completed_task_raises_error(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        task = task_mgr.create_task(session_id="s1", user_request="Completed test")
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.EXECUTING)
        task_mgr.update_status(task.task_id, TaskStatus.COMPLETED, result="Done")

        with pytest.raises(TaskStateError, match="in terminal state"):
            task_mgr.cancel_task(task.task_id)

        task_after = task_mgr.get_task(task.task_id)
        assert task_after.status == TaskStatus.COMPLETED

    def test_cancelling_already_cancelled_task_raises_error(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        task = task_mgr.create_task(session_id="s1", user_request="Cancel cancel")
        task_mgr.cancel_task(task.task_id)

        with pytest.raises(TaskStateError, match="in terminal state"):
            task_mgr.cancel_task(task.task_id)


# ---------------------------------------------------------------------------
# Priority 2: Cancellation & Execution Races
# ---------------------------------------------------------------------------

class TestCancellationRaces:
    """Validate race handling when task is cancelled concurrently with execution finalization."""

    @pytest.mark.asyncio
    async def test_prevent_completed_after_cancellation_in_engine_run(self, multi_user_app):
        """When a task is cancelled during execution, engine yields task_cancelled and does not overwrite."""
        task_mgr = multi_user_app["task_manager"]
        engine = multi_user_app["engine"]

        plan = AgentPlan(
            task_id="placeholder",
            objective="Engine cancel test",
            steps=[
                PlanStep(id="step_1", description="Reasoning step", tool_name=None, requires_approval=False)
            ],
            status="executing",
        )

        async def mock_create_plan(task_id, **kwargs):
            plan.task_id = task_id
            task_mgr.cancel_task(task_id)
            return plan

        engine._planner = MagicMock()
        engine._planner.create_plan = AsyncMock(side_effect=mock_create_plan)
        engine._plan_validator = MagicMock()
        engine._plan_validator.validate = MagicMock(return_value=[])
        engine._retrieve_context = AsyncMock(return_value=[])

        # Running the agent task must cleanly yield task_cancelled and NOT overwrite with COMPLETED
        events = []
        async for evt in engine.run_agent_task("s1", "Engine cancel test"):
            events.append(evt)

        # Confirm task_cancelled event was yielded
        cancelled_events = [e for e in events if isinstance(e, dict) and e.get("type") == "task_cancelled"]
        assert len(cancelled_events) >= 1

        # Confirm task state remained CANCELLED and was never overwritten with COMPLETED
        cancelled_task_id = cancelled_events[0]["task_id"]
        final_task = task_mgr.get_task(cancelled_task_id)
        assert final_task.status == TaskStatus.CANCELLED

    def test_cancelling_awaiting_approval_invalidates_pending_approval(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        approval_mgr = lifecycle_env["approval_mgr"]
        store = lifecycle_env["store"]

        task = task_mgr.create_task(session_id="s1", user_request="Approval cancel test")
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)

        approval = approval_mgr.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="test_tool",
            arguments={"arg": "val"},
            risk_level="high",
            reason="Requires confirmation",
        )

        assert approval_mgr.get_pending_for_task(task.task_id) is not None

        # Cancel task while awaiting approval
        task_mgr.cancel_task(task.task_id)

        # Pending approval must be cleared/rejected
        pending = approval_mgr.get_pending_for_task(task.task_id)
        assert pending is None

        # Direct retrieval from store shows status rejected
        appr_record = store.get_approval(approval.approval_id)
        assert appr_record["status"] == "rejected"

    def test_approving_cancelled_task_returns_400(self, multi_user_app):
        client = multi_user_app["client"]
        task_mgr = multi_user_app["task_manager"]
        approval_mgr = multi_user_app["approval_manager"]
        headers_a = multi_user_app["headers"]["op_a"]
        user_a = multi_user_app["users"]["op_a"]

        task = task_mgr.create_task(
            session_id="s1",
            user_request="Approve cancelled test",
            user_id=user_a.id,
        )
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)

        appr = approval_mgr.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="test_tool",
            arguments={"x": 1},
        )

        # Cancel the task
        task_mgr.cancel_task(task.task_id)
        assert task_mgr.get_task(task.task_id).status == TaskStatus.CANCELLED

        # Now attempt to approve via API
        resp = client.post(
            f"/api/tasks/{task.task_id}/approve",
            json={"action": "approve"},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "cannot approve task in cancelled state" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Priority 3: Approval Authorization & Consistency
# ---------------------------------------------------------------------------

class TestApprovalConsistency:
    """Validate approval authorization, divergence handling, and duplicate execution prevention."""

    def test_unauthorized_operator_cannot_approve_another_users_task(self, multi_user_app):
        client = multi_user_app["client"]
        task_mgr = multi_user_app["task_manager"]
        approval_mgr = multi_user_app["approval_manager"]
        user_a = multi_user_app["users"]["op_a"]
        headers_b = multi_user_app["headers"]["op_b"]  # Operator B trying to approve Operator A's task

        task = task_mgr.create_task(
            session_id="s_idor",
            user_request="Private task for Bob",
            user_id=user_a.id,
        )
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)

        approval_mgr.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="test_tool",
            arguments={"cmd": "run"},
        )

        # Operator B attempts to approve Operator A's task
        resp = client.post(
            f"/api/tasks/{task.task_id}/approve",
            json={"action": "approve"},
            headers=headers_b,
        )
        # IDOR protection: returns 404 to avoid leaking task existence
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

        # Task remains in awaiting_approval
        assert task_mgr.get_task(task.task_id).status == TaskStatus.AWAITING_APPROVAL

    def test_duplicate_approval_attempts_rejected(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        approval_mgr = lifecycle_env["approval_mgr"]

        task = task_mgr.create_task(session_id="s1", user_request="Dup approval test")
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)

        approval = approval_mgr.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="test_tool",
            arguments={"k": "v"},
        )

        # First approval succeeds
        appr_obj = approval_mgr.approve(approval.approval_id)
        assert appr_obj.status == "approved"

        # Second approval attempt must raise ValueError (atomic WHERE check rejects it)
        with pytest.raises(ValueError, match="already approved"):
            approval_mgr.approve(approval.approval_id)

    def test_approval_divergence_when_task_in_terminal_state(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        approval_mgr = lifecycle_env["approval_mgr"]

        task = task_mgr.create_task(session_id="s1", user_request="Divergence test")
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)

        approval = approval_mgr.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="test_tool",
            arguments={"x": "y"},
        )

        # Force task directly into terminal state
        task_mgr.update_status(task.task_id, TaskStatus.FAILED, error="Forced failure")

        # Attempting to approve via approval_mgr directly should be rejected because task is terminal
        with pytest.raises(ValueError, match="failed state"):
            approval_mgr.approve(approval.approval_id)


# ---------------------------------------------------------------------------
# Priority 4: Startup Recovery Tests
# ---------------------------------------------------------------------------

class TestStartupRecovery:
    """Validate server restart recovery behavior for PLANNING, EXECUTING, and AWAITING_APPROVAL tasks."""

    def test_abandoned_planning_task_marked_failed_interrupted(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        approval_mgr = lifecycle_env["approval_mgr"]
        tool_reg = lifecycle_env["tool_registry"]

        task = task_mgr.create_task(session_id="s_rec", user_request="Planning during restart")
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)

        counts = task_mgr.recover_tasks_on_startup(tool_registry=tool_reg, approval_manager=approval_mgr)
        assert counts["interrupted"] == 1

        recovered = task_mgr.get_task(task.task_id)
        assert recovered.status == TaskStatus.FAILED_INTERRUPTED
        assert "interrupted by a server restart" in recovered.error

    def test_recovery_does_not_overwrite_completed_or_cancelled_states(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        approval_mgr = lifecycle_env["approval_mgr"]
        tool_reg = lifecycle_env["tool_registry"]

        t_done = task_mgr.create_task(session_id="s1", user_request="Done task")
        task_mgr.update_status(t_done.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(t_done.task_id, TaskStatus.EXECUTING)
        task_mgr.update_status(t_done.task_id, TaskStatus.COMPLETED, result="All good")

        t_canc = task_mgr.create_task(session_id="s2", user_request="Cancelled task")
        task_mgr.cancel_task(t_canc.task_id)

        counts = task_mgr.recover_tasks_on_startup(tool_registry=tool_reg, approval_manager=approval_mgr)
        assert counts["interrupted"] == 0

        assert task_mgr.get_task(t_done.task_id).status == TaskStatus.COMPLETED
        assert task_mgr.get_task(t_canc.task_id).status == TaskStatus.CANCELLED

    def test_approved_unapplied_approval_divergence_recovery(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        approval_mgr = lifecycle_env["approval_mgr"]
        tool_reg = lifecycle_env["tool_registry"]
        store = lifecycle_env["store"]

        task = task_mgr.create_task(session_id="s_div", user_request="Crash divergence task")
        plan = AgentPlan(
            task_id=task.task_id,
            objective="Divergence test",
            steps=[
                PlanStep(
                    id="step_1",
                    description="High risk tool",
                    tool_name="test_tool",
                    arguments={"arg": 1},
                    requires_approval=True,
                    status=StepStatus.awaiting_approval.value,
                )
            ],
            status="executing",
        )
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.set_plan(task.task_id, plan)
        task_mgr.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)

        approval = approval_mgr.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="test_tool",
            arguments={"arg": 1},
        )

        # Simulate crash window: approval is marked 'approved' in SQLite,
        # but process terminates before step/task can update to EXECUTING
        approval_mgr.approve(approval.approval_id)
        assert store.get_approval(approval.approval_id)["status"] == "approved"
        assert task_mgr.get_task(task.task_id).status == TaskStatus.AWAITING_APPROVAL

        # Startup recovery must detect this divergence
        counts = task_mgr.recover_tasks_on_startup(tool_registry=tool_reg, approval_manager=approval_mgr)
        assert counts["interrupted"] == 1

        recovered = task_mgr.get_task(task.task_id)
        assert recovered.status == TaskStatus.FAILED_INTERRUPTED
        assert "execution was interrupted before starting" in recovered.error

        # Stale approval must be invalidated/rejected to prevent replay or reuse
        stale_appr = store.get_approval(approval.approval_id)
        assert stale_appr["status"] == "rejected"

        # Stale approval reuse fails cryptographic/status verification
        assert not approval_mgr.verify_approval_for_execution(
            approval_id=approval.approval_id,
            task_id=task.task_id,
            step_id="step_1",
            tool_name="test_tool",
            arguments={"arg": 1},
            tool_registry=tool_reg,
        )

    def test_normal_pending_approval_recovery_remains_active(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        approval_mgr = lifecycle_env["approval_mgr"]
        tool_reg = lifecycle_env["tool_registry"]
        store = lifecycle_env["store"]

        task = task_mgr.create_task(session_id="s_norm", user_request="Normal pending task")
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)

        approval = approval_mgr.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="test_tool",
            arguments={"param": "ok"},
        )

        counts = task_mgr.recover_tasks_on_startup(tool_registry=tool_reg, approval_manager=approval_mgr)
        assert counts["interrupted"] == 0
        assert counts["active"] == 1

        task_after = task_mgr.get_task(task.task_id)
        assert task_after.status == TaskStatus.AWAITING_APPROVAL
        assert store.get_approval(approval.approval_id)["status"] == "pending"


# ---------------------------------------------------------------------------
# Priority 5: Concurrency Tests
# ---------------------------------------------------------------------------

class TestConcurrencyAndThreadSafety:
    """Validate multithreaded cancellation and state update consistency."""

    def test_concurrent_cancellation_and_status_updates(self, lifecycle_env):
        task_mgr = lifecycle_env["task_mgr"]
        task = task_mgr.create_task(session_id="s_threads", user_request="Thread safety test")
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.EXECUTING)

        errors = []
        stop_event = threading.Event()

        def worker_updates():
            while not stop_event.is_set():
                try:
                    task_mgr.update_status(task.task_id, TaskStatus.EXECUTING)
                except TaskStateError:
                    # Once cancelled, updating to EXECUTING is safely rejected
                    break
                except Exception as exc:
                    errors.append(exc)
                    break
                time.sleep(0.001)

        def worker_cancel():
            time.sleep(0.01)
            try:
                task_mgr.cancel_task(task.task_id)
            except Exception as exc:
                errors.append(exc)
            finally:
                stop_event.set()

        t1 = threading.Thread(target=worker_updates)
        t2 = threading.Thread(target=worker_cancel)

        t1.start()
        t2.start()

        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert not errors, f"Unexpected errors during concurrent operations: {errors}"
        final_status = task_mgr.get_task(task.task_id).status
        assert final_status == TaskStatus.CANCELLED
