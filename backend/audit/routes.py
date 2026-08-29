"""
backend/audit/routes.py
------------------------
Phase 7: Centralized Audit REST API endpoints.

Endpoints:
  GET  /api/audit/events   — Query audit logs (filtered & paginated)
  GET  /api/audit/summary  — Summary metrics for the Audit Dashboard
  POST /api/audit/prune    — Trigger retention pruning (admin only)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.audit.logger import AuditLogger
from backend.auth.dependencies import get_current_user, require_permission, require_role
from backend.auth.models import Permission, User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditEventResponse(BaseModel):
    event_id: str
    timestamp: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    role: Optional[str] = None
    event_type: str
    action: Optional[str] = None
    resource: Optional[str] = None
    tool: Optional[str] = None
    task_id: Optional[str] = None
    step_id: Optional[str] = None
    success: bool
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    failure_reason: Optional[str] = None
    request_id: Optional[str] = None


class AuditListResponse(BaseModel):
    events: List[AuditEventResponse]
    total: int
    limit: int
    offset: int


class AuditSummaryResponse(BaseModel):
    total_events: int
    failed_events: int
    denied_actions: int
    tool_executions: int
    auth_failures: int


@router.get(
    "/events",
    response_model=AuditListResponse,
    summary="Query audit log events",
)
async def list_audit_events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
    tool: Optional[str] = Query(default=None),
    success: Optional[bool] = Query(default=None),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    current_user: User = Depends(require_permission(Permission.VIEW_SECURITY)),
):
    """
    Retrieve paginated audit events with flexible filters.
    Requires VIEW_SECURITY permission (Admin).
    """
    audit_logger: AuditLogger = getattr(request.app.state, "audit_logger", None)
    if not audit_logger:
        raise HTTPException(status_code=500, detail="Audit logger uninitialized")

    result = audit_logger.query_events(
        limit=limit,
        offset=offset,
        user_id=user_id,
        event_type=event_type,
        task_id=task_id,
        tool=tool,
        success=success,
        start_time=start_time,
        end_time=end_time,
    )
    return result


@router.get(
    "/summary",
    response_model=AuditSummaryResponse,
    summary="Get aggregate audit summary statistics",
)
async def get_audit_summary(
    request: Request,
    current_user: User = Depends(require_permission(Permission.VIEW_SECURITY)),
):
    """
    Return high-level aggregate metrics for the Audit Dashboard.
    Requires VIEW_SECURITY permission.
    """
    audit_logger: AuditLogger = getattr(request.app.state, "audit_logger", None)
    if not audit_logger:
        raise HTTPException(status_code=500, detail="Audit logger uninitialized")

    return audit_logger.get_summary()


@router.post(
    "/prune",
    summary="Trigger retention pruning of audit log",
)
async def prune_audit_log(
    request: Request,
    admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Trigger manual retention pruning of audit events older than configured retention days."""
    audit_logger: AuditLogger = getattr(request.app.state, "audit_logger", None)
    if not audit_logger:
        raise HTTPException(status_code=500, detail="Audit logger uninitialized")

    deleted = audit_logger.prune_retention()
    return {"message": f"Pruned {deleted} expired audit records", "deleted_rows": deleted}
