"""
backend/schemas/tools.py
-------------------------
Pydantic schemas for the tools API.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class ToolInfoResponse(BaseModel):
    """Schema for a single tool in the GET /api/tools response."""
    name: str
    description: str
    category: str
    input_schema: Dict[str, Any]
    read_only: bool
    requires_confirmation: bool
    requires_approval: bool = False        # Phase 6
    risk_level: str = "low"               # Phase 6
    enabled: bool


class ToolsListResponse(BaseModel):
    """GET /api/tools response."""
    tools: List[ToolInfoResponse]
    total: int
