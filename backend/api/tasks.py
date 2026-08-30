"""
backend/api/tasks.py
---------------------
Phase 6: Tasks API router — task management, approvals, and task history.

Endpoints:
    GET    /api/tasks                     — list tasks
    GET    /api/tasks/{task_id}           — get task detail
    POST   /api/tasks/{task_id}/approve   — approve/reject pending step
    POST   /api/tasks/{task_id}/cancel    — cancel a task
    GET    /api/tasks/{task_id}/approvals — list approval history

All endpoints are backward-compatible and purely additive.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse

from backend.schemas.tasks import (
    TaskSchema,
    TaskSummarySchema,
    TaskListResponse,
    ApprovalSchema,
    ApprovalActionRequest,
    ApprovalListResponse,
    AgentPlanSchema,
    PlanStepSchema,
)
from backend.auth.dependencies import get_current_user, require_permission
from backend.auth.models import Permission, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def get_task_manager(request: Request):
    return request.app.state.task_manager


def get_approval_manager(request: Request):
    return request.app.state.approval_manager


def get_engine(request: Request):
    return request.app.state.engine


# ---------------------------------------------------------------------------
# GET /api/tasks/monitor — Operational Monitoring (Phase 7)
# ---------------------------------------------------------------------------

@router.get("/monitor", summary="Operational task monitoring metrics (Phase 7)")
async def monitor_tasks(
    request: Request,
    stale_threshold_seconds: int = Query(default=3600, ge=60, le=86400),
    task_manager=Depends(get_task_manager),
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    """
    Return operational aggregation of all agent tasks:
    - Grouped by lifecycle status (including timeout & interrupted)
    - Stale task counts
    - Total task metrics
    """
    return task_manager.get_monitoring_summary(stale_threshold_seconds=stale_threshold_seconds)


# ---------------------------------------------------------------------------
# GET /api/tasks — list tasks
# ---------------------------------------------------------------------------

@router.get("", response_model=TaskListResponse, summary="List agent tasks")
async def list_tasks(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    status: Optional[str] = Query(default=None),
    task_manager=Depends(get_task_manager),
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    """List recent tasks, optionally filtered by status."""
    tasks = task_manager.list_tasks(limit=limit, status=status)

    summaries = []
    for t in tasks:
        step_count = 0
        completed_steps = 0
        if t.plan and t.plan.steps:
            step_count = len(t.plan.steps)
            completed_steps = sum(
                1 for s in t.plan.steps if s.status == "completed"
            )

        summaries.append(TaskSummarySchema(
            task_id=t.task_id,
            session_id=t.session_id,
            user_request=t.user_request[:200],
            status=t.status,
            step_count=step_count,
            completed_steps=completed_steps,
            created_at=t.created_at,
            updated_at=t.updated_at,
            completed_at=t.completed_at,
        ))

    return TaskListResponse(tasks=summaries, total=len(summaries))


# ---------------------------------------------------------------------------
# GET /api/tasks/{task_id} — get task detail
# ---------------------------------------------------------------------------

@router.get(
    "/{task_id}",
    response_model=TaskSchema,
    summary="Get task detail with execution plan",
)
async def get_task(
    task_id: str,
    task_manager=Depends(get_task_manager),
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    """Retrieve the full task state including plan steps and current progress."""
    task = task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    plan_schema = None
    if task.plan:
        plan_schema = AgentPlanSchema(
            task_id=task.plan.task_id,
            objective=task.plan.objective,
            steps=[
                PlanStepSchema(
                    id=s.id,
                    description=s.description,
                    tool_name=s.tool_name,
                    arguments=s.arguments,
                    requires_approval=s.requires_approval,
                    status=s.status,
                    result=s.result,
                    error=s.error,
                )
                for s in task.plan.steps
            ],
            status=task.plan.status,
            created_at=task.plan.created_at,
        )

    return TaskSchema(
        task_id=task.task_id,
        session_id=task.session_id,
        user_request=task.user_request,
        plan=plan_schema,
        current_step_idx=task.current_step_idx,
        status=task.status,
        result=task.result,
        error=task.error,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/approve — approve or reject a pending step
# ---------------------------------------------------------------------------

@router.post(
    "/{task_id}/approve",
    summary="Approve or reject a pending step",
)
async def approve_task_step(
    task_id: str,
    body: ApprovalActionRequest,
    request: Request,
    task_manager=Depends(get_task_manager),
    approval_manager=Depends(get_approval_manager),
    engine=Depends(get_engine),
    current_user: User = Depends(require_permission(Permission.APPROVE_TASKS)),
):
    """
    Approve or reject the pending approval for a task.

    The approval must match the exact task/step/tool/arguments that were
    submitted.  If any mismatch is detected, execution is rejected.
    """
    # Find the pending approval
    pending = approval_manager.get_pending_for_task(task_id)
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail=f"No pending approval found for task {task_id}",
        )

    approved = body.action == "approve"

    # Use the engine to resume the task (handles hash verification and RBAC role passing)
    async def _resume_stream():
        async for item in engine.resume_agent_task(
            task_id=task_id,
            approval_id=pending.approval_id,
            approved=approved,
            user_role=current_user.role,
        ):
            if isinstance(item, str):
                yield f"data: {json.dumps({'type': 'delta', 'content': item})}\n\n"
            elif isinstance(item, dict):
                yield f"data: {json.dumps(item)}\n\n"
            elif isinstance(item, list):
                # Sources sentinel — skip in approval response
                pass

    return StreamingResponse(
        _resume_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/cancel — cancel a task
# ---------------------------------------------------------------------------

@router.post(
    "/{task_id}/cancel",
    summary="Cancel a running or paused task",
)
async def cancel_task(
    task_id: str,
    task_manager=Depends(get_task_manager),
    current_user: User = Depends(require_permission(Permission.MANAGE_TASKS)),
):
    """Cancel a task that is in progress or awaiting approval."""
    from backend.agent.task import TaskStateError

    try:
        task = task_manager.cancel_task(task_id)
        return {
            "task_id": task.task_id,
            "status": task.status,
            "message": "Task cancelled",
        }
    except TaskStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /api/tasks/{task_id}/approvals — list approval history
# ---------------------------------------------------------------------------

@router.get(
    "/{task_id}/approvals",
    response_model=ApprovalListResponse,
    summary="List approval history for a task",
)
async def list_task_approvals(
    task_id: str,
    approval_manager=Depends(get_approval_manager),
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    """List all approval requests (including resolved) for a task."""
    approvals = approval_manager.get_approvals_for_task(task_id)

    schemas = [
        ApprovalSchema(
            approval_id=a.approval_id,
            task_id=a.task_id,
            step_id=a.step_id,
            tool_name=a.tool_name,
            arguments_hash=a.arguments_hash,
            risk_level=a.risk_level,
            reason=a.reason,
            status=a.status,
            created_at=a.created_at,
            expires_at=a.expires_at,
            resolved_at=a.resolved_at,
        )
        for a in approvals
    ]

    return ApprovalListResponse(approvals=schemas, total=len(schemas))
