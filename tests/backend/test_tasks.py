"""
tests/backend/test_tasks.py
----------------------------
Phase 6 tests for TaskStore (SQLite persistence) and TaskManager (state machine).
"""

import pytest
from pathlib import Path

from backend.agent.planner import AgentPlan, PlanStep, StepStatus
from backend.agent.task import TaskManager, TaskStatus, TaskStateError
from backend.agent.task_store import TaskStore


class TestTaskStore:
    """Test SQLite persistence layer."""

    @pytest.fixture
    def store(self, tmp_path: Path):
        db_file = tmp_path / "test_tasks.db"
        return TaskStore(db_path=db_file)

    def test_save_and_get_task(self, store):
        store.save_task({
            "task_id": "t_1",
            "session_id": "s_1",
            "user_request": "Analyze report",
            "status": "pending",
        })
        row = store.get_task("t_1")
        assert row is not None
        assert row["task_id"] == "t_1"
        assert row["user_request"] == "Analyze report"
        assert row["status"] == "pending"

    def test_list_tasks_and_filter(self, store):
        store.save_task({"task_id": "t_1", "session_id": "s_1", "user_request": "r1", "status": "pending"})
        store.save_task({"task_id": "t_2", "session_id": "s_1", "user_request": "r2", "status": "completed"})

        all_tasks = store.list_tasks()
        assert len(all_tasks) == 2

        pending_tasks = store.list_tasks(status="pending")
        assert len(pending_tasks) == 1
        assert pending_tasks[0]["task_id"] == "t_1"

    def test_update_task_status(self, store):
        store.save_task({"task_id": "t_1", "session_id": "s_1", "user_request": "r1", "status": "pending"})
        store.update_task_status("t_1", "completed", result="Done successfully")
        row = store.get_task("t_1")
        assert row["status"] == "completed"
        assert row["result"] == "Done successfully"

    def test_delete_task(self, store):
        store.save_task({"task_id": "t_1", "session_id": "s_1", "user_request": "r1", "status": "pending"})
        assert store.delete_task("t_1") is True
        assert store.get_task("t_1") is None

    def test_save_and_list_audit_events(self, store):
        store.save_task({"task_id": "t_1", "session_id": "s_1", "user_request": "r1", "status": "pending"})
        store.save_event({
            "task_id": "t_1",
            "step_id": "s_1",
            "event_type": "tool_executed",
            "tool_name": "calculator",
            "risk_level": "low",
            "success": True,
        })
        events = store.list_events_for_task("t_1")
        assert len(events) == 1
        assert events[0]["event_type"] == "tool_executed"


class TestTaskManager:
    """Test Task state machine and validation."""

    @pytest.fixture
    def manager(self, tmp_path: Path):
        db_file = tmp_path / "test_tasks.db"
        store = TaskStore(db_path=db_file)
        return TaskManager(store=store)

    def test_create_task(self, manager):
        task = manager.create_task(session_id="sess_1", user_request="Do planning")
        assert task.task_id.startswith("task_")
        assert task.status == TaskStatus.PENDING

    def test_valid_task_lifecycle(self, manager):
        task = manager.create_task(session_id="sess_1", user_request="Do planning")

        # pending -> planning
        t = manager.update_status(task.task_id, TaskStatus.PLANNING)
        assert t.status == TaskStatus.PLANNING

        # planning -> executing
        t = manager.update_status(task.task_id, TaskStatus.EXECUTING)
        assert t.status == TaskStatus.EXECUTING

        # executing -> completed
        t = manager.update_status(task.task_id, TaskStatus.COMPLETED, result="All done")
        assert t.status == TaskStatus.COMPLETED
        assert t.result == "All done"
        assert t.completed_at is not None

    def test_invalid_task_transition_raises_error(self, manager):
        task = manager.create_task(session_id="sess_1", user_request="Do planning")
        # pending -> completed directly is illegal
        with pytest.raises(TaskStateError):
            manager.update_status(task.task_id, TaskStatus.COMPLETED)

    def test_step_lifecycle(self, manager):
        task = manager.create_task(session_id="sess_1", user_request="Do planning")
        plan = AgentPlan(
            task_id=task.task_id,
            objective="Test",
            steps=[PlanStep(id="s1", description="Step 1", tool_name="calculator", arguments={})],
        )
        manager.set_plan(task.task_id, plan)

        # pending -> running -> completed
        manager.update_step_status(task.task_id, "s1", StepStatus.running.value)
        updated = manager.update_step_status(task.task_id, "s1", StepStatus.completed.value, result="42")
        assert updated.plan.steps[0].status == StepStatus.completed.value
        assert updated.plan.steps[0].result == "42"

    def test_cancel_task(self, manager):
        task = manager.create_task(session_id="sess_1", user_request="Do planning")
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        cancelled = manager.cancel_task(task.task_id)
        assert cancelled.status == TaskStatus.CANCELLED
