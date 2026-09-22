"""
backend/tools/registry.py
--------------------------
Central ToolRegistry — the single authority for tool registration,
validation, execution, and metadata export.

Design:
    - Each tool is a ToolDefinition with a typed Pydantic input schema.
    - The registry validates arguments before execution.
    - Execution is always async and exception-safe.
    - Audit logging is built into execute().
    - Tool metadata can be exported as JSON for LLM prompt injection.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Dict, List, Optional, Type

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    """Declarative specification of a single tool."""
    name: str
    description: str
    input_schema: Type[BaseModel]           # Pydantic model for argument validation
    execute_fn: Callable[..., Awaitable[Any]]  # async (validated_args) -> result
    category: str = "general"
    read_only: bool = True
    requires_confirmation: bool = False
    requires_approval: bool = False         # Phase 6: planning approval gate
    risk_level: str = "low"                 # Phase 6: "low" | "medium" | "high"
    enabled: bool = True


@dataclass
class ToolResult:
    """Structured result returned from every tool execution."""
    tool: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """
    Manages registered tools, validates inputs, executes safely, and
    produces structured audit logs.

    Usage:
        registry = ToolRegistry()
        registry.register(tool_def)
        result = await registry.execute("calculator", {"expression": "2+2"}, session_id="abc")
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool definition. Overwrites if name already exists."""
        self._tools[tool.name] = tool
        logger.info(
            "ToolRegistry: registered '%s' [category=%s, read_only=%s, enabled=%s]",
            tool.name, tool.category, tool.read_only, tool.enabled,
        )

    def disable(self, name: str) -> bool:
        """Disable a tool by name. Returns True if found."""
        t = self._tools.get(name)
        if t:
            t.enabled = False
            return True
        return False

    def enable(self, name: str) -> bool:
        """Enable a tool by name. Returns True if found."""
        t = self._tools.get(name)
        if t:
            t.enabled = True
            return True
        return False

    def unregister(self, name: str) -> bool:
        """Remove a tool. Returns True if it existed."""
        existed = name in self._tools
        self._tools.pop(name, None)
        return existed

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[ToolDefinition]:
        """Return a tool definition by name, or None."""
        return self._tools.get(name)

    def list_tools(self) -> List[ToolDefinition]:
        """Return all registered tools."""
        return list(self._tools.values())

    def list_enabled_tools(self) -> List[ToolDefinition]:
        """Return only enabled tools."""
        return [t for t in self._tools.values() if t.enabled]

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        session_id: str = "",
        user_role: Optional[str] = None,
        task_id: Optional[str] = None,
        step_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> ToolResult:
        """
        Validate arguments, enforce RBAC permissions, and execute a tool.

        Steps:
            1. Check tool exists
            2. Check tool is enabled
            3. Enforce RBAC permission (if user_role provided)
            4. Prevent cross-user artifact collision / overwrite
            5. Validate arguments via Pydantic
            6. Execute the tool function with authenticated server context
            7. Record artifact ownership if output artifact produced
            8. Log audit entry with trusted user attribution
            9. Return structured ToolResult

        Never raises — all failures are captured in ToolResult.
        """
        t0 = time.monotonic()

        # --- Check existence ---
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                tool=name, success=False,
                error=f"Unknown tool: '{name}'. Available tools: {list(self._tools.keys())}",
            )

        # --- Check enabled ---
        if not tool.enabled:
            return ToolResult(
                tool=name, success=False,
                error=f"Tool '{name}' is currently disabled.",
            )

        # --- Enforce RBAC (Phase 7 & Phase B) ---
        if user_role is not None:
            from backend.auth.models import UserRole, Permission, has_permission
            try:
                role_enum = UserRole(user_role)
            except (ValueError, KeyError):
                err_msg = f"Permission denied: unknown role '{user_role}'."
                self._audit_log(
                    session_id=session_id,
                    tool_name=name,
                    arguments=arguments,
                    success=False,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    result_summary=f"RBAC_DENIAL: {err_msg}",
                    task_id=task_id,
                    step_id=step_id,
                    user_id=user_id,
                    user_role=user_role,
                )
                return ToolResult(tool=name, success=False, error=err_msg)

            # Viewer cannot execute any tool
            if role_enum == UserRole.VIEWER:
                err_msg = f"Permission denied: role '{user_role}' cannot execute tools."
                self._audit_log(
                    session_id=session_id,
                    tool_name=name,
                    arguments=arguments,
                    success=False,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    result_summary=f"RBAC_DENIAL: {err_msg}",
                    task_id=task_id,
                    step_id=step_id,
                    user_id=user_id,
                    user_role=user_role,
                )
                return ToolResult(tool=name, success=False, error=err_msg)

            # Mutating tool requires EXECUTE_WRITE_TOOLS
            if not tool.read_only and not has_permission(user_role, Permission.EXECUTE_WRITE_TOOLS):
                err_msg = f"Permission denied: role '{user_role}' cannot execute mutating tool '{name}'."
                self._audit_log(
                    session_id=session_id,
                    tool_name=name,
                    arguments=arguments,
                    success=False,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    result_summary=f"RBAC_DENIAL: {err_msg}",
                    task_id=task_id,
                    step_id=step_id,
                    user_id=user_id,
                    user_role=user_role,
                )
                return ToolResult(tool=name, success=False, error=err_msg)

            # Non-mutating tool requires EXECUTE_READ_TOOLS
            if tool.read_only and not has_permission(user_role, Permission.EXECUTE_READ_TOOLS):
                err_msg = f"Permission denied: role '{user_role}' cannot execute tool '{name}'."
                self._audit_log(
                    session_id=session_id,
                    tool_name=name,
                    arguments=arguments,
                    success=False,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    result_summary=f"RBAC_DENIAL: {err_msg}",
                    task_id=task_id,
                    step_id=step_id,
                    user_id=user_id,
                    user_role=user_role,
                )
                return ToolResult(tool=name, success=False, error=err_msg)

        # --- Collision and Cross-User Overwrite Prevention (Step 16) ---
        if name in ("file_write", "docx_create", "xlsx_report") and self._task_store:
            req_filename = arguments.get("filename")
            if req_filename and isinstance(req_filename, str):
                from backend.tools.safety import sanitize_filename
                clean_target = sanitize_filename(req_filename)
                if not clean_target.lower().endswith(".docx") and name == "docx_create":
                    clean_target += ".docx"
                elif not clean_target.lower().endswith(".xlsx") and name == "xlsx_report":
                    clean_target += ".xlsx"
                existing = self._task_store.get_artifact(clean_target)
                if existing:
                    existing_user = existing.get("user_id")
                    existing_task = existing.get("task_id")
                    if existing_user and (not user_id or str(existing_user) != str(user_id)):
                        err_msg = f"Permission denied: Artifact '{clean_target}' is owned by another user."
                        self._audit_log(
                            session_id=session_id,
                            tool_name=name,
                            arguments=arguments,
                            success=False,
                            duration_ms=(time.monotonic() - t0) * 1000,
                            result_summary=f"COLLISION_DENIAL: {err_msg}",
                            task_id=task_id,
                            step_id=step_id,
                            user_id=user_id,
                            user_role=user_role,
                        )
                        return ToolResult(tool=name, success=False, error=err_msg)
                    if existing_task and task_id and str(existing_task) != str(task_id):
                        err_msg = f"Conflict: Artifact '{clean_target}' belongs to another task ({existing_task}). Overwrite prevented."
                        self._audit_log(
                            session_id=session_id,
                            tool_name=name,
                            arguments=arguments,
                            success=False,
                            duration_ms=(time.monotonic() - t0) * 1000,
                            result_summary=f"CROSS_TASK_DENIAL: {err_msg}",
                            task_id=task_id,
                            step_id=step_id,
                            user_id=user_id,
                            user_role=user_role,
                        )
                        return ToolResult(tool=name, success=False, error=err_msg)

        # --- Validate arguments ---
        try:
            validated = tool.input_schema(**arguments)
        except ValidationError as exc:
            errors = exc.errors()
            error_summary = "; ".join(
                f"{e.get('loc', ['?'])}: {e.get('msg', 'invalid')}" for e in errors
            )
            return ToolResult(
                tool=name, success=False,
                error=f"Invalid arguments for '{name}': {error_summary}",
            )
        except Exception as exc:
            return ToolResult(
                tool=name, success=False,
                error=f"Argument validation error for '{name}': {exc}",
            )

        # --- Execute ---
        try:
            import inspect
            sig = inspect.signature(tool.execute_fn)
            has_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            call_kwargs = {}
            if has_var_kwargs or "user_role" in sig.parameters:
                call_kwargs["user_role"] = user_role
            if has_var_kwargs or "user_id" in sig.parameters:
                call_kwargs["user_id"] = user_id
            if has_var_kwargs or "task_id" in sig.parameters:
                call_kwargs["task_id"] = task_id
            if has_var_kwargs or "step_id" in sig.parameters:
                call_kwargs["step_id"] = step_id
            if has_var_kwargs or "session_id" in sig.parameters:
                call_kwargs["session_id"] = session_id

            if inspect.iscoroutinefunction(tool.execute_fn):
                result = await tool.execute_fn(validated, **call_kwargs)
            else:
                res = tool.execute_fn(validated, **call_kwargs)
                if inspect.iscoroutine(res):
                    result = await res
                else:
                    result = res
            duration = (time.monotonic() - t0) * 1000

            # Step 16: Record artifact ownership if artifact-producing tool succeeded
            if name in ("file_write", "docx_create", "xlsx_report") and self._task_store:
                res_dict = result if isinstance(result, dict) else {}
                created_name = res_dict.get("filename")
                created_path = res_dict.get("created_path")
                size_bytes = res_dict.get("size_bytes", 0)
                if created_name:
                    from backend.config import settings
                    from backend.tools.safety import validate_path_within
                    try:
                        target_file_path = validate_path_within(created_name, settings.sandbox_dir)
                        if created_path:
                            validate_path_within(created_path, settings.sandbox_dir)
                        self._task_store.save_artifact(
                            filename=created_name,
                            path=str(target_file_path.relative_to(settings.sandbox_dir.parent)),
                            task_id=task_id,
                            user_id=user_id,
                            user_role=user_role,
                            size_bytes=size_bytes,
                        )
                    except Exception as exc:
                        logger.warning("Failed to record artifact ownership in store: %s", exc)

            self._audit_log(
                session_id=session_id,
                tool_name=name,
                arguments=arguments,
                success=True,
                duration_ms=duration,
                result_summary=self._summarize_result(result),
                task_id=task_id,
                step_id=step_id,
                user_id=user_id,
                user_role=user_role,
            )

            return ToolResult(
                tool=name, success=True,
                result=result, duration_ms=duration,
            )

        except Exception as exc:
            duration = (time.monotonic() - t0) * 1000
            error_msg = str(exc)

            self._audit_log(
                session_id=session_id,
                tool_name=name,
                arguments=arguments,
                success=False,
                duration_ms=duration,
                result_summary=f"ERROR: {error_msg[:200]}",
                task_id=task_id,
                step_id=step_id,
                user_id=user_id,
                user_role=user_role,
            )

            return ToolResult(
                tool=name, success=False,
                error=error_msg, duration_ms=duration,
            )

    # ------------------------------------------------------------------
    # LLM metadata export
    # ------------------------------------------------------------------

    def get_tool_schemas_for_llm(self) -> List[Dict[str, Any]]:
        """
        Export enabled tool definitions as JSON-serializable dicts
        for injection into the LLM system prompt.
        """
        schemas = []
        for tool in self.list_enabled_tools():
            schema = tool.input_schema.model_json_schema()
            # Clean up Pydantic internal keys
            schema.pop("title", None)
            schemas.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": schema,
            })
        return schemas

    def format_tools_for_prompt(self) -> str:
        """
        Format enabled tool definitions as a human-readable block
        suitable for the system prompt.
        """
        tools = self.list_enabled_tools()
        if not tools:
            return ""

        lines = ["## Available Tools\n"]
        for tool in tools:
            schema = tool.input_schema.model_json_schema()
            props = schema.get("properties", {})
            required = schema.get("required", [])

            lines.append(f"### {tool.name}")
            lines.append(f"**Description:** {tool.description}")
            lines.append(f"**Category:** {tool.category}")
            lines.append(f"**Read-only:** {'yes' if tool.read_only else 'no (mutating)'}")
            lines.append("**Parameters:**")

            for pname, pinfo in props.items():
                ptype = pinfo.get("type", "any")
                pdesc = pinfo.get("description", "")
                default = pinfo.get("default", None)
                req_mark = " (required)" if pname in required else f" (default: {default})"
                lines.append(f"  - `{pname}` ({ptype}){req_mark}: {pdesc}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    _audit_logger: Any = None
    _task_store: Any = None

    def set_audit_logger(self, audit_logger: Any) -> None:
        """Inject the centralized AuditLogger (Phase 7)."""
        self._audit_logger = audit_logger

    def set_task_store(self, task_store: Any) -> None:
        """Inject the TaskStore for artifact ownership tracking (Step 16)."""
        self._task_store = task_store

    def _audit_log(
        self,
        session_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        success: bool,
        duration_ms: float,
        result_summary: str,
        task_id: Optional[str] = None,
        step_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> None:
        """Emit a structured audit log entry for tool execution."""
        # Sanitize arguments for logging (truncate large values)
        safe_args = {}
        for k, v in arguments.items():
            s = str(v)
            safe_args[k] = s[:200] + "..." if len(s) > 200 else s

        audit = {
            "event": "tool_execution",
            "session_id": session_id,
            "tool": tool_name,
            "task_id": task_id,
            "step_id": step_id,
            "user_id": user_id,
            "user_role": user_role,
            "arguments": safe_args,
            "success": success,
            "duration_ms": round(duration_ms, 2),
            "result_summary": result_summary[:300],
        }

        if success:
            logger.info("TOOL_AUDIT | %s", json.dumps(audit))
        else:
            logger.warning("TOOL_AUDIT | %s", json.dumps(audit))

        # Centralized audit logger write
        if self._audit_logger:
            try:
                self._audit_logger.log(
                    event_type="tool.execution",
                    tool=tool_name,
                    session_id=session_id,
                    task_id=task_id,
                    step_id=step_id,
                    user_id=user_id,
                    role=user_role,
                    success=success,
                    duration_ms=round(duration_ms, 2),
                    failure_reason=result_summary if not success else None,
                    metadata={"arguments": safe_args, "summary": result_summary[:200]},
                )
            except Exception as exc:
                logger.error("Failed to forward tool execution to AuditLogger: %s", exc)

    @staticmethod
    def _summarize_result(result: Any) -> str:
        """Produce a short summary of a tool result for logging."""
        if result is None:
            return "null"
        if isinstance(result, dict):
            keys = list(result.keys())
            return f"dict({len(keys)} keys: {keys[:5]})"
        if isinstance(result, list):
            return f"list({len(result)} items)"
        s = str(result)
        return s[:200]
