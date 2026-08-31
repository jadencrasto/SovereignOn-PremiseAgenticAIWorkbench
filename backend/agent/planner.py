"""
backend/agent/planner.py
-------------------------
Phase 6: Agent Planner — structured execution plan generation.

The planner uses the LLM to create multi-step execution plans for
complex user requests.  Simple requests bypass planning entirely
via a deterministic complexity heuristic.

IMPORTANT:
    Planner output is NOT trusted.  Every plan MUST be validated by
    PlanValidator before execution.  The planner merely proposes;
    the backend decides.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    pending = "pending"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class PlanStatus(str, Enum):
    planning = "planning"
    awaiting_approval = "awaiting_approval"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


# ---------------------------------------------------------------------------
# Plan models
# ---------------------------------------------------------------------------

class PlanStep(BaseModel):
    """A single step in an agent execution plan."""
    id: str = Field(default_factory=lambda: f"step_{uuid.uuid4().hex[:8]}")
    description: str
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    status: str = Field(default=StepStatus.pending.value)
    result: Optional[str] = None
    error: Optional[str] = None


class AgentPlan(BaseModel):
    """A structured execution plan for a user request."""
    task_id: str
    objective: str
    steps: List[PlanStep] = Field(default_factory=list)
    status: str = Field(default=PlanStatus.planning.value)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Complexity heuristic — deterministic, runs BEFORE any LLM call
# ---------------------------------------------------------------------------

# Keywords / patterns that suggest multi-step tasks
_MULTI_STEP_INDICATORS = [
    r"\band\b.*\b(then|also|after|next)\b",
    r"\bfirst\b.*\bthen\b",
    r"\bstep\s*\d",
    r"\bcreate\b.*\b(report|file|summary)\b.*\b(from|using|based)\b",
    r"\bsearch\b.*\b(and|then)\b.*\b(calculate|write|create|summarize)\b",
    r"\bcalculate\b.*\b(and|then)\b.*\b(write|create|save|export)\b",
    r"\bfind\b.*\b(and|then)\b.*\b(compare|calculate|write)\b",
    r"\banalyze\b.*\b(and|then)\b",
    r"\bsummarize\b.*\b(and|then)\b.*\b(save|write|create)\b",
]

_SIMPLE_PATTERNS = [
    r"^(hi|hello|hey|howdy|good\s+(morning|afternoon|evening))\b",
    r"^what\s+(is|are|was|were)\b",
    r"^who\s+(is|are|was|were)\b",
    r"^(explain|define|describe)\s+",
    r"^calculate\s+[\d\.\+\-\*\/\(\)\s\^%]+$",
    r"^(thanks|thank you|ok|okay|got it|sure)\b",
]

_WRITE_KEYWORDS = [
    "create a file", "write a file", "save a file", "export",
    "create a report", "write a report", "generate a report",
    "save the result", "write to file", "save to file",
    "compliance", "runbook", "benchmark", "incident", "anomaly",
    "xlsx", "excel", "diligence", "diagnostics", "cross-check",
]


def should_use_planning(
    message: str,
    planning_enabled: bool = True,
    tools_enabled: bool = True,
) -> bool:
    """
    Deterministic complexity heuristic — decides BEFORE execution whether
    the request warrants the Phase 6 planner or the existing Phase 4 tool loop.

    Returns True if the planner should be used, False if the existing
    chat_stream_with_tools() path is sufficient.
    """
    if not planning_enabled or not tools_enabled:
        return False

    msg_lower = message.lower().strip()

    # Simple greetings / trivial questions — never plan
    for pattern in _SIMPLE_PATTERNS:
        if re.search(pattern, msg_lower):
            return False

    # Short messages (< 30 chars) are almost never multi-step unless they contain industrial keywords
    if len(msg_lower) < 30 and not any(kw in msg_lower for kw in ("runbook", "xlsx", "excel", "incident")):
        return False

    # Explicit multi-step indicators
    for pattern in _MULTI_STEP_INDICATORS:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            return True

    # Write & industrial operations always warrant planning (approval gate)
    for kw in _WRITE_KEYWORDS:
        if kw in msg_lower:
            return True

    # Default: single-action — use existing tool loop
    return False


# ---------------------------------------------------------------------------
# Placeholder file path detection
# ---------------------------------------------------------------------------

_PLACEHOLDER_PATH_PATTERN = re.compile(
    r"^(?:document(?:_search)?(?:_result)?|doc|chunk|result|file)[_\-\s]*\d*(?:\.[a-zA-Z0-9]+)?$",
    re.IGNORECASE,
)


def is_placeholder_path(path: str) -> bool:
    """Return True if path is an invented/placeholder name (e.g. 'document_0.txt')."""
    if not path:
        return True
    cleaned = path.strip()
    return bool(_PLACEHOLDER_PATH_PATTERN.match(cleaned))


# ---------------------------------------------------------------------------
# Plan generation prompt
# ---------------------------------------------------------------------------

_PLAN_SYSTEM_PROMPT = """You are an industrial task planner for a sovereign on-premise AI workbench.

Given the user's request, create a structured execution plan using ONLY the available tools listed below.

RULES:
1. Output ONLY valid JSON — no markdown, no explanation, no preamble.
2. Each step must use a tool from the available list or be a "reasoning" step (tool_name = null).
3. Keep plans concise — use the minimum steps needed to complete the user's request.
4. Tool guidelines:
   - document_search: Searches and retrieves text passages directly from the local knowledge base (e.g. benchmarks, standard operating procedures, runbooks). Use this whenever the user asks to search, find, or summarize information from documents. document_search directly retrieves the full grounded text content. Do NOT follow document_search with file_read.
   - file_read: Reads an existing text file from the workspace (e.g. 'mrpl_lab_composition_test.csv'). ONLY use file_read when the user explicitly provides a specific known filename in their prompt. NEVER call file_read on indexed documents or RAG results. NEVER invent or fabricate placeholder filenames (such as 'document_0.txt', 'document_1.txt', 'doc_0.txt', 'document_0', etc.).
   - calculator: Performs arithmetic or tolerance calculations on numbers (e.g. "4 + 3 * 2").
   - xlsx_report: Generates a styled Excel compliance or diligence report (.xlsx) with title, headers, data rows, and compliance status columns. Always set requires_approval to true.
   - file_write: Creates an output file or incident log in the sandbox. Always set requires_approval to true.
   - artifact_verifier: Verifies a generated report or artifact on disk (checks rows, columns, and SHA-256 hash). Follow xlsx_report or file_write with artifact_verifier whenever creating reports.
   - Reasoning step (tool_name = null): Synthesizes observations, calculates deviations, checks evidence, and provides the final grounded decision-support response.

5. Maximum {max_steps} steps.

OUTPUT FORMAT (JSON array of steps):
[
  {{"description": "what this step does", "tool_name": "tool_name_or_null", "arguments": {{}}, "requires_approval": false}},
  ...
]

AVAILABLE TOOLS:
{tool_descriptions}
"""



class AgentPlanner:
    """
    Generates structured execution plans from user requests.

    Uses the LLM to propose a plan, which MUST be validated by
    PlanValidator before execution.
    """

    def __init__(
        self,
        max_plan_steps: int = 10,
    ) -> None:
        self._max_plan_steps = max_plan_steps

    @property
    def max_plan_steps(self) -> int:
        return self._max_plan_steps

    async def create_plan(
        self,
        task_id: str,
        objective: str,
        tool_registry,
        provider,
        model_name: str,
    ) -> AgentPlan:
        """
        Generate an execution plan for the given objective.

        The plan is NOT validated here — call PlanValidator.validate()
        on the result before execution.
        """
        from backend.models.base import ChatRequest, Message

        # Build tool descriptions for the prompt
        tool_descriptions = ""
        if tool_registry:
            for tool in tool_registry.list_enabled_tools():
                schema = tool.input_schema.model_json_schema()
                props = schema.get("properties", {})
                param_strs = []
                for pname, pinfo in props.items():
                    param_strs.append(
                        f"    - {pname} ({pinfo.get('type', 'any')}): "
                        f"{pinfo.get('description', '')}"
                    )
                params = "\n".join(param_strs) if param_strs else "    (no parameters)"
                approval = " [REQUIRES APPROVAL]" if getattr(tool, "requires_approval", False) else ""
                tool_descriptions += (
                    f"- {tool.name}: {tool.description}{approval}\n"
                    f"  Parameters:\n{params}\n\n"
                )

        system_prompt = _PLAN_SYSTEM_PROMPT.format(
            max_steps=self._max_plan_steps,
            tool_descriptions=tool_descriptions.strip() or "(no tools available)",
        )

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=f"Create an execution plan for: {objective}"),
        ]

        request = ChatRequest(
            messages=messages,
            model=model_name,
            temperature=0.3,  # Low temperature for structured output
            max_tokens=2048,
            stream=False,
        )

        try:
            response = await provider.chat(request)
            raw = response.content.strip()

            # Extract JSON from the response (handle markdown code blocks)
            json_match = re.search(r"\[.*\]", raw, re.DOTALL)
            if json_match:
                raw = json_match.group()

            steps_data = json.loads(raw)

            if not isinstance(steps_data, list):
                raise ValueError("Plan must be a JSON array of steps")

            # Convert to PlanStep objects, filtering out fabricated/placeholder file_read calls
            has_doc_search = any(
                isinstance(s, dict) and s.get("tool_name") == "document_search"
                for s in steps_data if isinstance(s, dict)
            )

            steps = []
            for i, step_data in enumerate(steps_data[:self._max_plan_steps]):
                if not isinstance(step_data, dict):
                    continue
                tool_name = step_data.get("tool_name")
                if isinstance(tool_name, str) and tool_name.strip().lower() in {"null", "none", ""}:
                    tool_name = None
                args = step_data.get("arguments", {})
                if not isinstance(args, dict):
                    args = {}

                # Prevent planner from calling file_read with fabricated/placeholder document paths
                if tool_name == "file_read":
                    path_val = args.get("relative_path") or args.get("filename") or ""
                    path_str = str(path_val).strip()
                    if is_placeholder_path(path_str):
                        logger.info("Pruned fabricated file_read step with placeholder path '%s'", path_val)
                        continue
                    if has_doc_search and path_str.lower() not in objective.lower():
                        logger.info("Pruned ungrounded file_read step '%s' following document_search", path_val)
                        continue

                steps.append(PlanStep(
                    id=f"step_{len(steps) + 1}",
                    description=step_data.get("description", f"Step {len(steps) + 1}"),
                    tool_name=tool_name,
                    arguments=args,
                    requires_approval=step_data.get("requires_approval", False),
                    status=StepStatus.pending.value,
                ))

            if not steps:
                steps = [PlanStep(
                    id="step_1",
                    description=f"Answer the user's request: {objective[:200]}",
                    tool_name=None,
                    arguments={},
                    requires_approval=False,
                    status=StepStatus.pending.value,
                )]

            plan = AgentPlan(
                task_id=task_id,
                objective=objective,
                steps=steps,
                status=PlanStatus.planning.value,
            )

            logger.info(
                "plan_created | task=%s steps=%d objective_len=%d",
                task_id, len(steps), len(objective),
            )
            return plan

        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(
                "plan_parse_error | task=%s error=%s", task_id, str(exc)[:200]
            )
            # Return a minimal single-step plan as fallback
            return AgentPlan(
                task_id=task_id,
                objective=objective,
                steps=[PlanStep(
                    id="step_1",
                    description=f"Answer the user's request: {objective[:200]}",
                    tool_name=None,
                    arguments={},
                    requires_approval=False,
                    status=StepStatus.pending.value,
                )],
                status=PlanStatus.planning.value,
            )
