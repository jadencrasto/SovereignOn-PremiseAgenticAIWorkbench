"""
backend/agent/plan_validator.py
-------------------------------
Phase 6: Deterministic plan validation.

Every LLM-generated plan MUST pass through this validator before
execution.  The LLM is NEVER the final authority on plan safety.

Validates:
    - tool exists and is enabled
    - tool arguments match the Pydantic schema
    - maximum plan length
    - valid step/plan states
    - approval requirements based on tool risk_level
    - no recursive/self-referential patterns
    - no forbidden execution patterns
    - sandbox constraints
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from pydantic import ValidationError

from backend.agent.planner import AgentPlan, PlanStep, StepStatus, PlanStatus
from backend.agent.numeric_receipts import terminal_scale_reference

logger = logging.getLogger(__name__)


class PlanValidationError:
    """A single validation error in a plan."""

    def __init__(self, step_id: Optional[str], message: str) -> None:
        self.step_id = step_id
        self.message = message

    def __repr__(self) -> str:
        prefix = f"Step {self.step_id}: " if self.step_id else ""
        return f"{prefix}{self.message}"


class PlanValidator:
    """
    Deterministic validator for agent execution plans.

    This class enforces all safety constraints before a plan is allowed
    to execute.  The LLM output is treated as untrusted input.
    """

    def __init__(
        self,
        tool_registry,
        max_plan_steps: int = 10,
    ) -> None:
        self._tool_registry = tool_registry
        self._max_plan_steps = max_plan_steps

    def validate(self, plan: AgentPlan, *, tool_lookup=None, local_measurement=False) -> List[PlanValidationError]:
        """
        Validate an entire plan.  Returns a list of errors (empty = valid).

        Checks:
            1. Plan has at least one step
            2. Plan does not exceed max_plan_steps
            3. Each step's tool exists and is enabled (if tool_name is set)
            4. Each step's arguments match the tool's Pydantic schema
            5. Approval requirements are correctly set based on tool risk
            6. No duplicate step IDs
            7. No recursive patterns (same tool called with same args)
        """
        errors: List[PlanValidationError] = []
        get_tool = self._tool_registry.get if tool_lookup is None else tool_lookup

        # --- Plan-level checks ---
        if not plan.steps:
            errors.append(PlanValidationError(None, "Plan has no steps"))
            return errors

        if len(plan.steps) > self._max_plan_steps:
            errors.append(PlanValidationError(
                None,
                f"Plan has {len(plan.steps)} steps, maximum is {self._max_plan_steps}"
            ))

        # --- Check for duplicate step IDs ---
        seen_ids = set()
        for step in plan.steps:
            if step.id in seen_ids:
                errors.append(PlanValidationError(
                    step.id, f"Duplicate step ID: {step.id}"
                ))
            seen_ids.add(step.id)

        # Bound workflows finish only when every operation has a trusted,
        # explicitly declared read-only result contract. Legacy unbound plans
        # keep their prior behavior and cannot act as dependency evidence.
        if any(step.bindings for step in plan.steps):
            for step in plan.steps:
                tool = get_tool(step.tool_name) if step.tool_name else None
                if (not tool or not tool.enabled or not tool.read_only or tool.requires_approval
                        or not tool.result_schema or not callable(tool.result_verifier)):
                    errors.append(PlanValidationError(step.id, 'Bound workflow requires verified read-only tool contracts'))

        # --- Per-step validation ---
        tool_call_signatures = []
        previous_steps = {}
        for step in plan.steps:
            step_errors = self._validate_step(step, get_tool, tool_lookup is not None)
            target = get_tool(step.tool_name) if step.tool_name else None
            for argument, binding in step.bindings.items():
                source = previous_steps.get(binding.step_id)
                source_tool = get_tool(source.tool_name) if source and source.tool_name else None
                output = getattr(source_tool, 'result_schema', None)
                if (not target or not target.read_only or target.requires_approval
                        or argument not in target.input_schema.model_fields or argument in step.arguments
                        or not source_tool or not source_tool.enabled or not source_tool.read_only
                        or source_tool.requires_approval or output is None
                        or not callable(getattr(source_tool, 'result_verifier', None))
                        or binding.field not in output.model_fields):
                    errors.append(PlanValidationError(step.id, 'Invalid or unavailable step binding'))
            if step.tool_name == 'calculator':
                expression = step.arguments.get('expression')
                if isinstance(expression, str):
                    references = re.findall(r'@([A-Za-z0-9_]+)\.result', expression)
                    for ref in references:
                        source = previous_steps.get(ref)
                        if source and source.tool_name == 'calculator':
                            continue
                        if (source and source.tool_name == 'unit_conversion'
                                and (local_measurement or (step is plan.steps[-1]
                                                           and terminal_scale_reference(expression) == ref))):
                            continue
                        errors.append(PlanValidationError(step.id, 'Invalid calculator dependency'))
                    if '@' in re.sub(r'@[A-Za-z0-9_]+\.result', '', expression):
                        errors.append(PlanValidationError(step.id, 'Invalid calculator dependency'))
            previous_steps[step.id] = step
            errors.extend(step_errors)

            # Check for recursive patterns (same tool + same args repeated)
            if step.tool_name:
                sig = (step.tool_name, str(sorted(step.arguments.items())), tuple(sorted((key, value.step_id, value.field) for key, value in step.bindings.items())))
                if sig in tool_call_signatures:
                    errors.append(PlanValidationError(
                        step.id,
                        f"Recursive pattern: '{step.tool_name}' called with identical arguments"
                    ))
                tool_call_signatures.append(sig)

        if errors:
            logger.warning(
                "plan_validation_failed | task=%s errors=%d: %s",
                plan.task_id, len(errors),
                "; ".join(str(e) for e in errors[:5]),
            )
        else:
            logger.info(
                "plan_validated | task=%s steps=%d",
                plan.task_id, len(plan.steps),
            )

        return errors

    def _validate_step(self, step: PlanStep, get_tool, local: bool) -> List[PlanValidationError]:
        """Validate a single plan step."""
        errors: List[PlanValidationError] = []

        # Normalize string 'null'/'none' to None
        if isinstance(step.tool_name, str) and step.tool_name.strip().lower() in {"null", "none", ""}:
            step.tool_name = None

        # Reasoning-only steps (no tool) — always valid
        if step.tool_name is None:
            return errors

        # --- Check tool exists ---
        tool = get_tool(step.tool_name)
        if tool is None:
            available = '' if local else f' Available: {[t.name for t in self._tool_registry.list_tools()]}'
            errors.append(PlanValidationError(
                step.id,
                f"Unknown tool '{step.tool_name}'.{available}"
            ))
            return errors  # can't validate further

        # --- Check tool is enabled ---
        if not tool.enabled:
            errors.append(PlanValidationError(
                step.id,
                f"Tool '{step.tool_name}' is disabled"
            ))
            return errors

        # --- Normalize arguments before Pydantic validation ---
        if step.arguments:
            if step.tool_name == "artifact_verifier":
                if "relative_path" not in step.arguments and "filename" in step.arguments:
                    step.arguments["relative_path"] = step.arguments["filename"]
                elif "relative_path" not in step.arguments and "filepath" in step.arguments:
                    step.arguments["relative_path"] = step.arguments["filepath"]
            elif step.tool_name == "file_read":
                if "relative_path" not in step.arguments and "filename" in step.arguments:
                    step.arguments["relative_path"] = step.arguments["filename"]

        arguments_to_validate = dict(step.arguments)
        if step.tool_name == 'calculator' and isinstance(arguments_to_validate.get('expression'), str):
            arguments_to_validate['expression'] = re.sub(r'@[A-Za-z0-9_]+\.result', '1', arguments_to_validate['expression'])
        try:
            if step.bindings:
                supplied = set(arguments_to_validate) | set(step.bindings)
                missing = [name for name, field in tool.input_schema.model_fields.items() if field.is_required() and name not in supplied]
                if missing:
                    raise ValueError('Required tool arguments missing')
                from pydantic import TypeAdapter
                for name, value in arguments_to_validate.items():
                    if name not in tool.input_schema.model_fields:
                        raise ValueError('Unknown tool argument')
                    TypeAdapter(tool.input_schema.model_fields[name].annotation).validate_python(value)
            else:
                tool.input_schema(**arguments_to_validate)
        except ValidationError as exc:
            error_details = "; ".join(
                f"{e.get('loc', ['?'])}: {e.get('msg', 'invalid')}"
                for e in exc.errors()
            )
            errors.append(PlanValidationError(step.id, f"Invalid arguments for '{step.tool_name}': {error_details}"))
        except Exception:
            errors.append(PlanValidationError(step.id, f"Argument validation failed for '{step.tool_name}'"))

        # --- Reject placeholder/fabricated paths for file_read ---
        if step.tool_name == "file_read" and step.arguments:
            from backend.agent.planner import is_placeholder_path
            path_val = step.arguments.get("relative_path") or step.arguments.get("filename") or ""
            if is_placeholder_path(str(path_val)):
                errors.append(PlanValidationError(
                    step.id,
                    f"Invalid file_read path '{path_val}': fabricated or placeholder document paths are not permitted. Use document_search for document retrieval."
                ))

        # --- Enforce approval requirements based on tool risk ---
        risk_level = getattr(tool, "risk_level", "low")
        tool_requires_approval = getattr(tool, "requires_approval", False)

        if tool_requires_approval:
            step.requires_approval = True
        else:
            # Read-only or safe tools (e.g. artifact_verifier, file_read, file_list, document_search)
            # must NEVER require approval, even if LLM hallucinated requires_approval: true
            step.requires_approval = False

        return errors

    def enforce_approval_requirements(self, plan: AgentPlan, *, tool_lookup=None) -> None:
        """
        Post-validation pass to ensure approval requirements strictly mirror tool definitions:
        high-risk / mutating tools require approval; safe / read-only tools never require approval.

        Mutates the plan in-place.
        """
        get_tool = self._tool_registry.get if tool_lookup is None else tool_lookup
        for step in plan.steps:
            if step.tool_name is None:
                step.requires_approval = False
                continue

            tool = get_tool(step.tool_name)
            if tool is None:
                continue

            step.requires_approval = bool(getattr(tool, "requires_approval", False))
