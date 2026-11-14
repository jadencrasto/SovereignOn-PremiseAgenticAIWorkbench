"""
backend/api/tools.py
---------------------
Tools API router — exposes tool metadata for the frontend.

Endpoints:
    GET /api/tools — list all registered tools with schemas
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from backend.schemas.tools import ToolInfoResponse, ToolsListResponse
from backend.auth.dependencies import get_current_user, require_permission
from backend.auth.models import Permission, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("", response_model=ToolsListResponse, summary="List registered tools")
async def list_tools(
    request: Request,
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    """Return metadata for all registered tools."""
    registry = request.app.state.tool_registry
    from backend.security.execution_policy import ARTIFACT_WRITE_TOOLS
    from backend.security.task_access import task_content_revalidation_available

    tool_infos = []
    for tool in registry.list_tools():
        schema = tool.input_schema.model_json_schema()
        schema.pop("title", None)
        artifact_blocked = (tool.name in ARTIFACT_WRITE_TOOLS or
                            (tool.name == 'artifact_verifier' and not task_content_revalidation_available()))
        tool_infos.append(ToolInfoResponse(
            name=tool.name,
            description=tool.description,
            category=tool.category,
            input_schema=schema,
            read_only=tool.read_only,
            requires_confirmation=tool.requires_confirmation,
            requires_approval=tool.requires_approval,
            risk_level=tool.risk_level,
            enabled=tool.enabled,
            availability='blocked' if artifact_blocked else ('disabled' if not tool.enabled else 'unknown'),
            availability_reason=('Artifact verification is unavailable.' if tool.name == 'artifact_verifier'
                                 else 'Verified artifact generation is unavailable.') if artifact_blocked else None,
        ))

    return ToolsListResponse(tools=tool_infos, total=len(tool_infos))
