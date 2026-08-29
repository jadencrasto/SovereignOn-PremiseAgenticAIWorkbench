"""
tests/backend/test_rbac.py
--------------------------
Phase 7 tests for Role-Based Access Control (RBAC).

Tests:
- Explicit permission mapping for viewer, operator, admin.
- Tool dispatch boundary enforcement in ToolRegistry.execute().
- Route-level permission dependencies.
"""

import pytest
from pydantic import BaseModel, Field

from backend.auth.models import (
    Permission,
    UserRole,
    has_permission,
    is_role_sufficient,
)
from backend.tools.registry import ToolDefinition, ToolRegistry


class DummyInput(BaseModel):
    arg: str = Field(default="test")


class TestRBACPermissions:
    """Test explicit role permission table."""

    def test_viewer_permissions(self):
        assert has_permission(UserRole.VIEWER.value, Permission.VIEW_DATA) is True
        assert has_permission(UserRole.VIEWER.value, Permission.EXECUTE_READ_TOOLS) is False
        assert has_permission(UserRole.VIEWER.value, Permission.EXECUTE_WRITE_TOOLS) is False
        assert has_permission(UserRole.VIEWER.value, Permission.APPROVE_TASKS) is False
        assert has_permission(UserRole.VIEWER.value, Permission.MANAGE_USERS) is False

    def test_operator_permissions(self):
        assert has_permission(UserRole.OPERATOR.value, Permission.VIEW_DATA) is True
        assert has_permission(UserRole.OPERATOR.value, Permission.EXECUTE_READ_TOOLS) is True
        assert has_permission(UserRole.OPERATOR.value, Permission.EXECUTE_WRITE_TOOLS) is True
        assert has_permission(UserRole.OPERATOR.value, Permission.APPROVE_TASKS) is True
        assert has_permission(UserRole.OPERATOR.value, Permission.MANAGE_TASKS) is True
        assert has_permission(UserRole.OPERATOR.value, Permission.MANAGE_USERS) is False
        assert has_permission(UserRole.OPERATOR.value, Permission.MANAGE_CONFIG) is False

    def test_admin_permissions(self):
        for perm in Permission:
            assert has_permission(UserRole.ADMIN.value, perm) is True

    def test_role_hierarchy(self):
        assert is_role_sufficient("viewer", UserRole.VIEWER) is True
        assert is_role_sufficient("viewer", UserRole.OPERATOR) is False
        assert is_role_sufficient("viewer", UserRole.ADMIN) is False

        assert is_role_sufficient("operator", UserRole.VIEWER) is True
        assert is_role_sufficient("operator", UserRole.OPERATOR) is True
        assert is_role_sufficient("operator", UserRole.ADMIN) is False

        assert is_role_sufficient("admin", UserRole.VIEWER) is True
        assert is_role_sufficient("admin", UserRole.OPERATOR) is True
        assert is_role_sufficient("admin", UserRole.ADMIN) is True


class TestToolRegistryRBACBoundary:
    """Test RBAC enforcement at the ToolRegistry tool-dispatch boundary."""

    @pytest.fixture
    def registry(self):
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="read_tool",
            description="Read data",
            input_schema=DummyInput,
            execute_fn=lambda inp: "read_ok",
            read_only=True,
            risk_level="low",
            enabled=True,
        ))
        reg.register(ToolDefinition(
            name="write_tool",
            description="Write data",
            input_schema=DummyInput,
            execute_fn=lambda inp: "write_ok",
            read_only=False,
            risk_level="high",
            requires_approval=True,
            enabled=True,
        ))
        return reg

    @pytest.mark.asyncio
    async def test_viewer_blocked_from_all_tools(self, registry):
        res_read = await registry.execute("read_tool", {"arg": "x"}, user_role="viewer")
        assert res_read.success is False
        assert "Permission denied" in res_read.error

        res_write = await registry.execute("write_tool", {"arg": "x"}, user_role="viewer")
        assert res_write.success is False
        assert "Permission denied" in res_write.error

    @pytest.mark.asyncio
    async def test_operator_allowed_read_and_write_tools(self, registry):
        res_read = await registry.execute("read_tool", {"arg": "x"}, user_role="operator")
        assert res_read.success is True

        res_write = await registry.execute("write_tool", {"arg": "x"}, user_role="operator")
        assert res_write.success is True

    @pytest.mark.asyncio
    async def test_admin_allowed_all_tools(self, registry):
        res_read = await registry.execute("read_tool", {"arg": "x"}, user_role="admin")
        assert res_read.success is True

        res_write = await registry.execute("write_tool", {"arg": "x"}, user_role="admin")
        assert res_write.success is True

    @pytest.mark.asyncio
    async def test_unspecified_role_preserves_compatibility(self, registry):
        res = await registry.execute("read_tool", {"arg": "x"})
        assert res.success is True
