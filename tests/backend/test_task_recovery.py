"""
tests/backend/test_task_recovery.py
------------------------------------
Phase 7 tests for Restart Recovery & Operational Task Monitoring.

Tests:
- Server restart recovery table:
    * EXECUTING -> marked FAILED_INTERRUPTED (never auto-resume side-effects)
    * AWAITING_APPROVAL -> re-verified against current ToolRegistry state (invalidated if tool disabled)
    * PLANNING / PENDING -> safe to resume
- Stale task detection based on updated_at threshold
- Aggregated task monitoring metrics
"""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from backend.agent.approval import ApprovalManager
from backend.agent.task import TaskManager, TaskStatus, TaskState
from backend.agent.task_store import TaskStore
from backend.tools.registry import ToolDefinition, ToolRegistry


class TestTaskRestartRecovery:
    """Test restart recovery matrix on startup."""

    @pytest.fixture
    def setup_env(self, tmp_path: Path):
        db_file = tmp_path / "test_tasks_recovery.db"
        store = TaskStore(db_path=db_file)
        task_mgr = TaskManager(store=store)
        approval_mgr = ApprovalManager(store=store)

        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="safe_tool",
            description="Safe",
            input_schema=MagicMock(),
            execute_fn=lambda inp: "ok",
            enabled=True,
        ))
        reg.register(ToolDefinition(
            name="disabled_tool",
            description="Disabled",
            input_schema=MagicMock(),
            execute_fn=lambda inp: "ok",
            enabled=False,
        ))

        return task_mgr, store, approval_mgr, reg

    def test_executing_task_marked_failed_interrupted(self, setup_env):
        task_mgr, store, approval_mgr, reg = setup_env

        # Create and transition task to EXECUTING
        task = task_mgr.create_task(session_id="s1", user_request="Write files")
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.EXECUTING)

        # Run startup recovery scan
        counts = task_mgr.recover_tasks_on_startup(tool_registry=reg, approval_manager=approval_mgr)
        assert counts["interrupted"] == 1

        recovered = task_mgr.get_task(task.task_id)
        assert recovered.status == TaskStatus.FAILED_INTERRUPTED
        assert "interrupted by a server restart" in recovered.error

    def test_awaiting_approval_reverified_against_tool_registry(self, setup_env):
        task_mgr, store, approval_mgr, reg = setup_env

        # Create task awaiting approval for a disabled tool
        task = task_mgr.create_task(session_id="s1", user_request="Run disabled tool")
        task_mgr.update_status(task.task_id, TaskStatus.PLANNING)
        task_mgr.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)

        approval = approval_mgr.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="disabled_tool",
            arguments={"param": "val"},
        )

        # Run startup recovery scan
        counts = task_mgr.recover_tasks_on_startup(tool_registry=reg, approval_manager=approval_mgr)
        assert counts["re_approval_required"] == 1

        # Pending approval should now be invalidated / rejected
        pending = approval_mgr.get_pending_for_task(task.task_id)
        assert pending is None  # No longer pending

    def test_stale_task_detection_and_monitoring(self, setup_env):
        task_mgr, store, approval_mgr, reg = setup_env

        # Create fresh task
        t_fresh = task_mgr.create_task(session_id="s1", user_request="Fresh task")

        # Create stale task
        t_stale = task_mgr.create_task(session_id="s1", user_request="Stale task")
        old_iso = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        with store._lock:
            conn = store._connect()
            conn.execute("UPDATE tasks SET updated_at = ? WHERE task_id = ?", (old_iso, t_stale.task_id))
            conn.commit()
            conn.close()

        summary = task_mgr.get_monitoring_summary(stale_threshold_seconds=3600)
        assert summary["total_tasks"] == 2
        assert summary["stale_tasks"] == 1
        assert summary["counts_by_status"][TaskStatus.PENDING] == 2
