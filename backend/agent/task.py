"""
backend/agent/task.py
----------------------
Phase 6: Task state machine and manager.

Manages the lifecycle of agent tasks with strict state transitions.
Invalid transitions raise TaskStateError.

Task states: pending → planning → awaiting_approval → executing → completed/failed/cancelled
Step states: pending → awaiting_approval → approved → running → completed/failed/skipped
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.agent.planner import AgentPlan, PlanStep, StepStatus, PlanStatus
from backend.agent.task_store import TaskStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task states
# ---------------------------------------------------------------------------

class TaskStatus:
    PENDING = "pending"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Allowed state transitions
_VALID_TASK_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.PLANNING, TaskStatus.CANCELLED},
    TaskStatus.PLANNING: {TaskStatus.AWAITING_APPROVAL, TaskStatus.EXECUTING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.AWAITING_APPROVAL: {TaskStatus.EXECUTING, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.EXECUTING: {
        TaskStatus.COMPLETED, TaskStatus.FAILED,
        TaskStatus.AWAITING_APPROVAL, TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: set(),   # terminal
    TaskStatus.FAILED: set(),      # terminal
    TaskStatus.CANCELLED: set(),   # terminal
}

_VALID_STEP_TRANSITIONS = {
    StepStatus.pending.value: {StepStatus.awaiting_approval.value, StepStatus.running.value, StepStatus.skipped.value},
    StepStatus.awaiting_approval.value: {StepStatus.approved.value, StepStatus.skipped.value, StepStatus.failed.value},
    StepStatus.approved.value: {StepStatus.running.value, StepStatus.skipped.value},
    StepStatus.running.value: {StepStatus.completed.value, StepStatus.failed.value},
    StepStatus.completed.value: set(),   # terminal
    StepStatus.failed.value: set(),      # terminal
    StepStatus.skipped.value: set(),     # terminal
}


class TaskStateError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


# ---------------------------------------------------------------------------
# Task state model
# ---------------------------------------------------------------------------

class TaskState(BaseModel):
    """Complete state of an agent task."""
    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    session_id: str
    user_request: str
    plan: Optional[AgentPlan] = None
    current_step_idx: int = 0
    status: str = Field(default=TaskStatus.PENDING)
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Task manager
# ---------------------------------------------------------------------------

class TaskManager:
    """
    Manages task lifecycle with strict state machine enforcement
    and SQLite persistence.
    """

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def create_task(
        self,
        session_id: str,
        user_request: str,
    ) -> TaskState:
        """Create a new task in PENDING state."""
        task = TaskState(
            session_id=session_id,
            user_request=user_request,
        )
        self._persist(task)
        logger.info(
            "task_created | task=%s session=%s request_len=%d",
            task.task_id, session_id, len(user_request),
        )
        return task

    def get_task(self, task_id: str) -> Optional[TaskState]:
        """Retrieve a task by ID from persistence."""
        row = self._store.get_task(task_id)
        if row is None:
            return None
        return self._from_row(row)

    def list_tasks(
        self,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> List[TaskState]:
        """List tasks with optional status filter."""
        rows = self._store.list_tasks(limit=limit, status=status)
        return [self._from_row(r) for r in rows]

    def update_status(
        self,
        task_id: str,
        new_status: str,
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> TaskState:
        """
        Transition a task to a new status.

        Raises TaskStateError if the transition is invalid.
        """
        task = self.get_task(task_id)
        if task is None:
            raise TaskStateError(f"Task not found: {task_id}")

        self._validate_transition(task.status, new_status)

        task.status = new_status
        task.updated_at = datetime.now(timezone.utc).isoformat()
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error
        if new_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.completed_at = task.updated_at

        self._persist(task)

        logger.info(
            "task_status_changed | task=%s status=%s",
            task_id, new_status,
        )
        return task

    def set_plan(self, task_id: str, plan: AgentPlan) -> TaskState:
        """Attach a validated plan to a task."""
        task = self.get_task(task_id)
        if task is None:
            raise TaskStateError(f"Task not found: {task_id}")

        task.plan = plan
        task.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist(task)
        return task

    def update_step_status(
        self,
        task_id: str,
        step_id: str,
        new_status: str,
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> TaskState:
        """
        Update a specific step's status within a task's plan.

        Raises TaskStateError if the step transition is invalid.
        """
        task = self.get_task(task_id)
        if task is None:
            raise TaskStateError(f"Task not found: {task_id}")
        if task.plan is None:
            raise TaskStateError(f"Task {task_id} has no plan")

        step = None
        for s in task.plan.steps:
            if s.id == step_id:
                step = s
                break

        if step is None:
            raise TaskStateError(f"Step not found: {step_id} in task {task_id}")

        self._validate_step_transition(step.status, new_status)

        step.status = new_status
        if result is not None:
            step.result = result
        if error is not None:
            step.error = error

        task.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist(task)

        logger.info(
            "step_status_changed | task=%s step=%s status=%s",
            task_id, step_id, new_status,
        )
        return task

    def advance_step(self, task_id: str) -> TaskState:
        """Move to the next step index."""
        task = self.get_task(task_id)
        if task is None:
            raise TaskStateError(f"Task not found: {task_id}")

        task.current_step_idx += 1
        task.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist(task)
        return task

    def cancel_task(self, task_id: str) -> TaskState:
        """Cancel a task, skipping all pending steps."""
        task = self.get_task(task_id)
        if task is None:
            raise TaskStateError(f"Task not found: {task_id}")

        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            raise TaskStateError(
                f"Cannot cancel task {task_id} in terminal state: {task.status}"
            )

        # Skip all non-terminal steps
        if task.plan:
            for step in task.plan.steps:
                if step.status in (StepStatus.pending.value, StepStatus.awaiting_approval.value, StepStatus.approved.value):
                    step.status = StepStatus.skipped.value

        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now(timezone.utc).isoformat()
        task.updated_at = task.completed_at
        self._persist(task)

        logger.info("task_cancelled | task=%s", task_id)
        return task

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_transition(self, current: str, target: str) -> None:
        """Validate a task state transition."""
        allowed = _VALID_TASK_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise TaskStateError(
                f"Invalid task transition: {current} → {target}. "
                f"Allowed: {allowed}"
            )

    def _validate_step_transition(self, current: str, target: str) -> None:
        """Validate a step state transition."""
        allowed = _VALID_STEP_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise TaskStateError(
                f"Invalid step transition: {current} → {target}. "
                f"Allowed: {allowed}"
            )

    def _persist(self, task: TaskState) -> None:
        """Save task to SQLite."""
        plan_json = None
        if task.plan:
            plan_json = task.plan.model_dump_json()

        self._store.save_task({
            "task_id": task.task_id,
            "session_id": task.session_id,
            "user_request": task.user_request,
            "plan_json": plan_json,
            "current_step_idx": task.current_step_idx,
            "status": task.status,
            "result": task.result,
            "error": task.error,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "completed_at": task.completed_at,
        })

    @staticmethod
    def _from_row(row: Dict[str, Any]) -> TaskState:
        """Reconstruct TaskState from a database row."""
        plan = None
        if row.get("plan_json"):
            try:
                plan = AgentPlan.model_validate_json(row["plan_json"])
            except Exception as exc:
                logger.warning("Failed to parse plan JSON for task %s: %s", row["task_id"], exc)

        return TaskState(
            task_id=row["task_id"],
            session_id=row["session_id"],
            user_request=row["user_request"],
            plan=plan,
            current_step_idx=row.get("current_step_idx", 0),
            status=row["status"],
            result=row.get("result"),
            error=row.get("error"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row.get("completed_at"),
        )
