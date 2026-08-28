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
    ) -> ToolResult:
        """
        Validate arguments and execute a tool.

        Steps:
            1. Check tool exists
            2. Check tool is enabled
            3. Validate arguments via Pydantic
            4. Execute the tool function
            5. Log audit entry
            6. Return structured ToolResult

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
            result = await tool.execute_fn(validated)
            duration = (time.monotonic() - t0) * 1000

            self._audit_log(
                session_id=session_id,
                tool_name=name,
                arguments=arguments,
                success=True,
                duration_ms=duration,
                result_summary=self._summarize_result(result),
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

    def _audit_log(
        self,
        session_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        success: bool,
        duration_ms: float,
        result_summary: str,
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
            "arguments": safe_args,
            "success": success,
            "duration_ms": round(duration_ms, 2),
            "result_summary": result_summary[:300],
        }

        if success:
            logger.info("TOOL_AUDIT | %s", json.dumps(audit))
        else:
            logger.warning("TOOL_AUDIT | %s", json.dumps(audit))

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
