"""
backend/api/tools.py
---------------------
Tools API router — exposes tool metadata for the frontend.

Endpoints:
    GET /api/tools — list all registered tools with schemas
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from backend.schemas.tools import ToolInfoResponse, ToolsListResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("", response_model=ToolsListResponse, summary="List registered tools")
async def list_tools(request: Request):
    """Return metadata for all registered tools."""
    registry = request.app.state.tool_registry

    tool_infos = []
    for tool in registry.list_tools():
        schema = tool.input_schema.model_json_schema()
        schema.pop("title", None)
        tool_infos.append(ToolInfoResponse(
            name=tool.name,
            description=tool.description,
            category=tool.category,
            input_schema=schema,
            read_only=tool.read_only,
            requires_confirmation=tool.requires_confirmation,
            enabled=tool.enabled,
        ))

    return ToolsListResponse(tools=tool_infos, total=len(tool_infos))
