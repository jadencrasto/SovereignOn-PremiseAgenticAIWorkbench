"""
backend/security/routes.py
---------------------------
Phase 7: Security diagnostics REST API router.

Endpoints:
  GET /api/security/status  — Security posture report (Admin only)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.auth.dependencies import require_permission, require_role
from backend.auth.models import Permission, User, UserRole
from backend.security.checker import SecurityChecker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/security", tags=["security"])


class DiagnosticItem(BaseModel):
    id: str
    category: str
    title: str
    status: str
    details: str
    remediation: str = ""


class SecurityStatusResponse(BaseModel):
    overall_status: str  # PASS | WARN | FAIL
    diagnostics: List[DiagnosticItem]


@router.get(
    "/status",
    response_model=SecurityStatusResponse,
    summary="Get security diagnostics and posture status",
)
async def get_security_status(
    request: Request,
    current_user: User = Depends(require_permission(Permission.VIEW_SECURITY)),
):
    """
    Retrieve security diagnostic checks across authentication, air-gap egress,
    sandbox isolation, and database hardening.
    Requires VIEW_SECURITY permission (Admin).
    """
    auth_store = getattr(request.app.state, "auth_store", None)
    checker = SecurityChecker(auth_store=auth_store)
    diagnostics = checker.run_all_checks()

    # Calculate overall status: FAIL > WARN > PASS
    statuses = [d["status"] for d in diagnostics]
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "WARN" in statuses:
        overall = "WARN"
    else:
        overall = "PASS"

    return SecurityStatusResponse(
        overall_status=overall,
        diagnostics=[DiagnosticItem(**d) for d in diagnostics],
    )


@router.post(
    "/scan",
    response_model=SecurityStatusResponse,
    summary="Execute on-demand security scan and return posture status",
)
async def run_security_scan(
    request: Request,
    current_user: User = Depends(require_permission(Permission.VIEW_SECURITY)),
):
    """
    Execute on-demand security scan across authentication, air-gap egress,
    sandbox isolation, and database hardening.
    Requires VIEW_SECURITY permission (Admin).
    """
    return await get_security_status(request=request, current_user=current_user)

