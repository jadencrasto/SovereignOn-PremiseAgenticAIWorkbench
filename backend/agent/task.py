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
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

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
    FAILED_TIMEOUT = "failed_timeout"
    FAILED_INTERRUPTED = "failed_interrupted"
    CANCELLED = "cancelled"


# Allowed state transitions
_VALID_TASK_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.PLANNING, TaskStatus.CANCELLED, TaskStatus.FAILED_INTERRUPTED},
    TaskStatus.PLANNING: {TaskStatus.AWAITING_APPROVAL, TaskStatus.EXECUTING, TaskStatus.FAILED, TaskStatus.FAILED_TIMEOUT, TaskStatus.FAILED_INTERRUPTED, TaskStatus.CANCELLED},
    TaskStatus.AWAITING_APPROVAL: {TaskStatus.EXECUTING, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.FAILED_TIMEOUT, TaskStatus.FAILED_INTERRUPTED},
    TaskStatus.EXECUTING: {
        TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.FAILED_TIMEOUT,
        TaskStatus.FAILED_INTERRUPTED, TaskStatus.AWAITING_APPROVAL, TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: set(),           # terminal
    TaskStatus.FAILED: set(),              # terminal
    TaskStatus.FAILED_TIMEOUT: set(),      # terminal
    TaskStatus.FAILED_INTERRUPTED: set(),  # terminal
    TaskStatus.CANCELLED: set(),           # terminal
}

TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.FAILED_TIMEOUT,
    TaskStatus.FAILED_INTERRUPTED,
    TaskStatus.CANCELLED,
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
    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex}")
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
    user_id: Optional[str] = None
    user_role: Optional[str] = None
    clearance: Optional[str] = None


# ---------------------------------------------------------------------------
# Task manager
# ---------------------------------------------------------------------------

class TaskManager:
    """
    Manages task lifecycle with strict state machine enforcement
    and SQLite persistence.
    """

    def __init__(self, store: TaskStore, approval_manager: Any = None) -> None:
        self._store = store
        self._approval_manager = approval_manager
        self._lock = threading.RLock()
        self._audit_logger: Any = None
        self._emitted_lifecycle_events: Set[Tuple[str, str]] = set()

    def set_approval_manager(self, approval_manager: Any) -> None:
        """Inject ApprovalManager reference to synchronize on approval invalidation/cancellation."""
        self._approval_manager = approval_manager

    def set_audit_logger(self, audit_logger: Any) -> None:
        """Inject central AuditLogger (Step 16)."""
        self._audit_logger = audit_logger

    def _emit_task_audit(
        self,
        event_type: str,
        task: TaskState,
        action: str,
        success: bool = True,
        failure_reason: Optional[str] = None,
    ) -> None:
        """Emit central audit log event for task lifecycle, deduplicated per task."""
        if not self._audit_logger:
            return
        key = (task.task_id, event_type)
        if key in self._emitted_lifecycle_events:
            return
        self._emitted_lifecycle_events.add(key)
        try:
            self._audit_logger.log(
                event_type=event_type,
                action=action,
                resource=f"task:{task.task_id}",
                task_id=task.task_id,
                user_id=task.user_id,
                role=task.user_role,
                session_id=task.session_id,
                success=success,
                failure_reason=failure_reason,
                metadata={
                    "status": task.status,
                    "clearance": task.clearance,
                    "current_step_idx": task.current_step_idx,
                },
            )
        except Exception as exc:
            logger.error("Failed to emit task lifecycle audit event '%s': %s", event_type, exc)

    def create_task(
        self,
        session_id: str,
        user_request: str,
        user_id: Optional[str] = None,
        user_role: Optional[str] = None,
        clearance: Optional[str] = None,
    ) -> TaskState:
        """Create a new task in PENDING state."""
        task = TaskState(
            session_id=session_id,
            user_request=user_request,
            user_id=user_id,
            user_role=user_role,
            clearance=clearance,
        )
        self._persist(task)
        logger.info(
            "task_created | task=%s session=%s user_role=%s request_len=%d",
            task.task_id, session_id, user_role, len(user_request),
        )
        self._emit_task_audit("task.created", task, action="create_task", success=True)
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
        user_id: Optional[Union[str, List[str]]] = None,
    ) -> List[TaskState]:
        """List tasks with optional status and user_id filter."""
        rows = self._store.list_tasks(limit=limit, status=status, user_id=user_id)
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
        with self._lock:
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
            if new_status in TERMINAL_TASK_STATUSES:
                task.completed_at = task.updated_at

            self._persist(task)

            logger.info(
                "task_status_changed | task=%s status=%s",
                task_id, new_status,
            )

            # Central audit logging for terminal states (Step 16)
            if new_status == TaskStatus.COMPLETED:
                self._emit_task_audit("task.completed", task, action="task_completed", success=True)
            elif new_status in (TaskStatus.FAILED, TaskStatus.FAILED_TIMEOUT, TaskStatus.FAILED_INTERRUPTED):
                self._emit_task_audit(
                    "task.failed",
                    task,
                    action=f"task_{new_status}",
                    success=False,
                    failure_reason=error or task.error or f"Task transitioned to {new_status}",
                )
            elif new_status == TaskStatus.CANCELLED:
                self._emit_task_audit("task.cancelled", task, action="cancel_task", success=True)

            return task

    def set_plan(self, task_id: str, plan: AgentPlan) -> TaskState:
        """Attach a validated plan to a task."""
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            task = self.get_task(task_id)
            if task is None:
                raise TaskStateError(f"Task not found: {task_id}")

            task.current_step_idx += 1
            task.updated_at = datetime.now(timezone.utc).isoformat()
            self._persist(task)
            return task

    def cancel_task(self, task_id: str) -> TaskState:
        """Cancel a task, skipping all pending steps and invalidating pending approvals."""
        with self._lock:
            task = self.get_task(task_id)
            if task is None:
                raise TaskStateError(f"Task not found: {task_id}")

            if task.status in TERMINAL_TASK_STATUSES:
                raise TaskStateError(
                    f"Cannot cancel task {task_id} in terminal state: {task.status}"
                )

            self._validate_transition(task.status, TaskStatus.CANCELLED)

            # Invalidate / reject pending approval in ApprovalManager or store
            if self._approval_manager:
                try:
                    pending = self._approval_manager.get_pending_for_task(task_id)
                    if pending:
                        self._approval_manager.reject(pending.approval_id, reason="Task cancelled by user")
                except Exception as exc:
                    logger.warning("Failed to reject approval for cancelled task %s: %s", task_id, exc)
            else:
                try:
                    pending_row = self._store.get_pending_approval_for_task(task_id)
                    if pending_row:
                        now_iso = datetime.now(timezone.utc).isoformat()
                        self._store.update_approval_status(pending_row["approval_id"], "rejected", resolved_at=now_iso)
                except Exception as exc:
                    logger.warning("Failed to reject pending approval row for cancelled task %s: %s", task_id, exc)

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
            self._emit_task_audit("task.cancelled", task, action="cancel_task", success=True)
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
            "user_id": task.user_id,
            "user_role": task.user_role,
            "clearance": task.clearance,
        })

    def recover_tasks_on_startup(
        self,
        tool_registry: Any = None,
        approval_manager: Any = None,
    ) -> Dict[str, int]:
        """
        Scan and recover tasks on server restart per Phase 7 restart recovery rules:
        - EXECUTING: Mark FAILED_INTERRUPTED (never auto-resume in-flight side effects).
        - AWAITING_APPROVAL: Re-verify approval binding against current plan & current ToolRegistry state.
          If tool was disabled or changed, invalidate approval to force re-approval.
        - PLANNING / QUEUED: Safe to leave for replanning/validation on resume.

        Returns counts of recovered/updated tasks.
        """
        counts = {"interrupted": 0, "re_approval_required": 0, "active": 0}
        active_tasks = self._store.list_tasks()

        for t_row in active_tasks:
            status = t_row.get("status")
            task_id = t_row.get("task_id")

            if status == TaskStatus.EXECUTING:
                # Mark as interrupted — cannot assume in-flight state completed safely
                self.update_status(
                    task_id,
                    TaskStatus.FAILED_INTERRUPTED,
                    error="Task execution was interrupted by a server restart. Manual review or replanning required.",
                )
                counts["interrupted"] += 1
                logger.warning("Task %s recovered: Marked FAILED_INTERRUPTED due to restart.", task_id)

            elif status == TaskStatus.AWAITING_APPROVAL:
                effective_approval_mgr = approval_manager or getattr(self, "_approval_manager", None)
                recovered_as_interrupted = False
                if effective_approval_mgr:
                    pending = effective_approval_mgr.get_pending_for_task(task_id)
                    if pending:
                        if tool_registry:
                            tool = tool_registry.get(pending.tool_name) if hasattr(tool_registry, "get") else (
                                tool_registry.get_tool(pending.tool_name) if hasattr(tool_registry, "get_tool") else None
                            )
                            if not tool or not tool.enabled:
                                # Tool disabled or missing — invalidate pending approval
                                effective_approval_mgr.reject(
                                    pending.approval_id,
                                    reason="Tool configuration changed or tool disabled during server restart. Re-approval required.",
                                )
                                counts["re_approval_required"] += 1
                                logger.warning(
                                    "Task %s approval invalidated: Tool '%s' disabled or missing on restart.",
                                    task_id, pending.tool_name,
                                )
                    else:
                        # No pending approval found for a task in AWAITING_APPROVAL.
                        # Check for approved approvals that were never executed before restart/crash.
                        approved_list = []
                        if hasattr(effective_approval_mgr, "get_approved_for_task"):
                            approved_list = effective_approval_mgr.get_approved_for_task(task_id)
                        elif hasattr(self._store, "get_approved_approvals_for_task"):
                            approved_list = self._store.get_approved_approvals_for_task(task_id)

                        if approved_list:
                            # Reconcile divergence: approval was marked approved, but task remained awaiting_approval.
                            # Invalidate stale approved approval(s) so they cannot be replayed or reused.
                            for appr in approved_list:
                                appr_id = appr.approval_id if hasattr(appr, "approval_id") else appr.get("approval_id")
                                if appr_id and hasattr(self._store, "update_approval_status"):
                                    self._store.update_approval_status(
                                        appr_id,
                                        status="rejected",
                                        resolved_at=datetime.now(timezone.utc).isoformat(),
                                    )

                            self.update_status(
                                task_id,
                                TaskStatus.FAILED_INTERRUPTED,
                                error="Task approval was processed but execution was interrupted before starting. Please resubmit your request.",
                            )
                            counts["interrupted"] += 1
                            recovered_as_interrupted = True
                            logger.warning(
                                "Task %s recovered: Found approved-but-unapplied approval divergence across restart; marked FAILED_INTERRUPTED.",
                                task_id,
                            )
                if not recovered_as_interrupted:
                    counts["active"] += 1

            elif status == TaskStatus.PLANNING:
                # Interrupted during plan generation — cannot safely assume plan was completed
                self.update_status(
                    task_id,
                    TaskStatus.FAILED_INTERRUPTED,
                    error="Task planning was interrupted by a server restart. Please re-submit your request.",
                )
                counts["interrupted"] += 1
                logger.warning("Task %s recovered: Marked FAILED_INTERRUPTED during planning restart.", task_id)

            elif status in (TaskStatus.PENDING,):
                counts["active"] += 1

        return counts

    def get_monitoring_summary(self, stale_threshold_seconds: int = 3600) -> Dict[str, Any]:
        """
        Return operational task monitoring metrics:
        - Tasks grouped by status.
        - Stale task count (non-terminal tasks updated > threshold ago).
        - Recent task details with execution duration.
        """
        all_tasks = self.list_tasks(limit=100)
        now = datetime.now(timezone.utc)

        grouped: Dict[str, List[Dict[str, Any]]] = {
            TaskStatus.PENDING: [],
            TaskStatus.PLANNING: [],
            TaskStatus.AWAITING_APPROVAL: [],
            TaskStatus.EXECUTING: [],
            TaskStatus.COMPLETED: [],
            TaskStatus.FAILED: [],
            TaskStatus.FAILED_TIMEOUT: [],
            TaskStatus.FAILED_INTERRUPTED: [],
            TaskStatus.CANCELLED: [],
        }

        stale_count = 0

        for t in all_tasks:
            t_status = t.status
            if t_status not in grouped:
                grouped[t_status] = []

            # Check staleness for non-terminal states
            is_stale = False
            if t_status in (TaskStatus.PENDING, TaskStatus.PLANNING, TaskStatus.AWAITING_APPROVAL, TaskStatus.EXECUTING):
                try:
                    updated_at = datetime.fromisoformat(t.updated_at)
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=timezone.utc)
                    diff = (now - updated_at).total_seconds()
                    if diff > stale_threshold_seconds:
                        is_stale = True
                        stale_count += 1
                except Exception as exc:
                    logger.warning("Failed checking task staleness: %s", exc)

            step_count = len(t.plan.steps) if t.plan and t.plan.steps else 0
            completed_steps = sum(1 for s in t.plan.steps if s.status == "completed") if t.plan and t.plan.steps else 0

            grouped[t_status].append({
                "task_id": t.task_id,
                "session_id": t.session_id,
                "user_request": t.user_request[:150],
                "status": t.status,
                "step_count": step_count,
                "completed_steps": completed_steps,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
                "completed_at": t.completed_at,
                "is_stale": is_stale,
                "error": t.error,
            })

        return {
            "total_tasks": len(all_tasks),
            "stale_tasks": stale_count,
            "counts_by_status": {k: len(v) for k, v in grouped.items()},
            "grouped_tasks": grouped,
        }

    @staticmethod
    def _from_row(row: Dict[str, Any]) -> TaskState:
        """Reconstruct TaskState from a database row."""
        plan = None
        if row.get("plan_json"):
            try:
                plan = AgentPlan.model_validate_json(row["plan_json"])
            except Exception as exc:
                logger.warning("Failed to parse plan JSON for task %s: %s", row.get("task_id"), exc)

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
            user_id=row.get("user_id"),
            user_role=row.get("user_role"),
            clearance=row.get("clearance"),
        )
