"""
backend/schemas/tasks.py
-------------------------
Phase 6: Pydantic schemas for the tasks API.

These are the data contracts between the frontend and backend for
task management, plan display, and approval workflows.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Plan / Step schemas (API response format)
# ---------------------------------------------------------------------------

class PlanStepSchema(BaseModel):
    """A single step in a plan, as returned by the API."""
    id: str
    description: str
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    status: str = "pending"
    result: Optional[str] = None
    error: Optional[str] = None


class AgentPlanSchema(BaseModel):
    """An execution plan, as returned by the API."""
    task_id: str
    objective: str
    steps: List[PlanStepSchema] = Field(default_factory=list)
    status: str = "planning"
    created_at: str = ""


# ---------------------------------------------------------------------------
# Task schemas
# ---------------------------------------------------------------------------

class TaskSchema(BaseModel):
    """Full task detail, as returned by GET /api/tasks/{task_id}."""
    task_id: str
    session_id: str
    user_request: str
    plan: Optional[AgentPlanSchema] = None
    current_step_idx: int = 0
    status: str
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None


class TaskSummarySchema(BaseModel):
    """Compact task summary for list responses."""
    task_id: str
    session_id: str
    user_request: str
    status: str
    step_count: int = 0
    completed_steps: int = 0
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None


class TaskListResponse(BaseModel):
    """GET /api/tasks response."""
    tasks: List[TaskSummarySchema]
    total: int


# ---------------------------------------------------------------------------
# Approval schemas
# ---------------------------------------------------------------------------

class ApprovalSchema(BaseModel):
    """Approval record as returned by the API."""
    approval_id: str
    task_id: str
    step_id: str
    tool_name: str
    arguments_hash: str
    risk_level: str = "low"
    reason: str = ""
    status: str = "pending"
    created_at: str
    expires_at: str
    resolved_at: Optional[str] = None


class ApprovalActionRequest(BaseModel):
    """POST body for approve/reject actions."""
    action: str = Field(
        ..., description="'approve' or 'reject'",
        pattern="^(approve|reject)$",
    )
    reason: Optional[str] = Field(
        default="",
        description="Optional reason for the decision",
    )


class ApprovalListResponse(BaseModel):
    """GET /api/tasks/{task_id}/approvals response."""
    approvals: List[ApprovalSchema]
    total: int
