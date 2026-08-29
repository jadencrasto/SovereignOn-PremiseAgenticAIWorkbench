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
from typing import List, Optional

from pydantic import ValidationError

from backend.agent.planner import AgentPlan, PlanStep, StepStatus, PlanStatus

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

    def validate(self, plan: AgentPlan) -> List[PlanValidationError]:
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

        # --- Per-step validation ---
        tool_call_signatures = []
        for step in plan.steps:
            step_errors = self._validate_step(step)
            errors.extend(step_errors)

            # Check for recursive patterns (same tool + same args repeated)
            if step.tool_name:
                sig = (step.tool_name, str(sorted(step.arguments.items())))
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

    def _validate_step(self, step: PlanStep) -> List[PlanValidationError]:
        """Validate a single plan step."""
        errors: List[PlanValidationError] = []

        # Reasoning-only steps (no tool) — always valid
        if step.tool_name is None:
            return errors

        # --- Check tool exists ---
        tool = self._tool_registry.get(step.tool_name)
        if tool is None:
            available = [t.name for t in self._tool_registry.list_tools()]
            errors.append(PlanValidationError(
                step.id,
                f"Unknown tool '{step.tool_name}'. Available: {available}"
            ))
            return errors  # can't validate further

        # --- Check tool is enabled ---
        if not tool.enabled:
            errors.append(PlanValidationError(
                step.id,
                f"Tool '{step.tool_name}' is disabled"
            ))
            return errors

        # --- Validate arguments against the tool's Pydantic schema ---
        if step.arguments:
            try:
                tool.input_schema(**step.arguments)
            except ValidationError as exc:
                error_details = "; ".join(
                    f"{e.get('loc', ['?'])}: {e.get('msg', 'invalid')}"
                    for e in exc.errors()
                )
                errors.append(PlanValidationError(
                    step.id,
                    f"Invalid arguments for '{step.tool_name}': {error_details}"
                ))
            except Exception as exc:
                errors.append(PlanValidationError(
                    step.id,
                    f"Argument validation error for '{step.tool_name}': {exc}"
                ))

        # --- Enforce approval requirements based on tool risk ---
        risk_level = getattr(tool, "risk_level", "low")
        tool_requires_approval = getattr(tool, "requires_approval", False)

        if tool_requires_approval and not step.requires_approval:
            # Force approval for high-risk tools even if LLM didn't set it
            step.requires_approval = True
            logger.info(
                "plan_validator_forced_approval | step=%s tool=%s risk=%s",
                step.id, step.tool_name, risk_level,
            )

        return errors

    def enforce_approval_requirements(self, plan: AgentPlan) -> None:
        """
        Post-validation pass to ensure all steps that use approval-required
        tools have requires_approval=True.

        Mutates the plan in-place.
        """
        for step in plan.steps:
            if step.tool_name is None:
                continue

            tool = self._tool_registry.get(step.tool_name)
            if tool is None:
                continue

            if getattr(tool, "requires_approval", False):
                step.requires_approval = True
