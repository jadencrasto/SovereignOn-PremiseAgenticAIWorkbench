"""
tests/backend/test_plan_validator.py
------------------------------------
Phase 6 tests for deterministic PlanValidator.
"""

import pytest
from pydantic import BaseModel, Field

from backend.agent.planner import AgentPlan, PlanStep
from backend.agent.plan_validator import PlanValidator
from backend.tools.registry import ToolRegistry, ToolDefinition


class SampleInput(BaseModel):
    query: str = Field(..., description="Query string")


class TestPlanValidator:
    """Test deterministic plan validation rules."""

    @pytest.fixture
    def registry(self):
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="search",
            description="Search tool",
            input_schema=SampleInput,
            execute_fn=lambda **kw: "results",
            risk_level="low",
            requires_approval=False,
            enabled=True,
        ))
        reg.register(ToolDefinition(
            name="delete_data",
            description="High risk delete",
            input_schema=SampleInput,
            execute_fn=lambda **kw: "deleted",
            risk_level="high",
            requires_approval=True,
            enabled=True,
        ))
        reg.register(ToolDefinition(
            name="disabled_tool",
            description="Disabled",
            input_schema=SampleInput,
            execute_fn=lambda **kw: "ok",
            risk_level="low",
            requires_approval=False,
            enabled=False,
        ))
        return reg

    def test_empty_plan_fails(self, registry):
        validator = PlanValidator(tool_registry=registry, max_plan_steps=5)
        plan = AgentPlan(task_id="t1", objective="Empty", steps=[])
        errors = validator.validate(plan)
        assert len(errors) > 0
        assert "no steps" in errors[0].message

    def test_plan_exceeding_max_steps_fails(self, registry):
        validator = PlanValidator(tool_registry=registry, max_plan_steps=2)
        steps = [
            PlanStep(id=f"step_{i}", description=f"s{i}", tool_name="search", arguments={"query": "a"})
            for i in range(3)
        ]
        plan = AgentPlan(task_id="t1", objective="Too long", steps=steps)
        errors = validator.validate(plan)
        assert any("maximum" in e.message for e in errors)

    def test_unknown_tool_fails(self, registry):
        validator = PlanValidator(tool_registry=registry, max_plan_steps=5)
        plan = AgentPlan(task_id="t1", objective="Unknown", steps=[
            PlanStep(id="step_1", description="s1", tool_name="nonexistent_tool", arguments={})
        ])
        errors = validator.validate(plan)
        assert any("Unknown tool" in e.message for e in errors)

    def test_disabled_tool_fails(self, registry):
        validator = PlanValidator(tool_registry=registry, max_plan_steps=5)
        plan = AgentPlan(task_id="t1", objective="Disabled", steps=[
            PlanStep(id="step_1", description="s1", tool_name="disabled_tool", arguments={"query": "test"})
        ])
        errors = validator.validate(plan)
        assert any("disabled" in e.message for e in errors)

    def test_invalid_argument_schema_fails(self, registry):
        validator = PlanValidator(tool_registry=registry, max_plan_steps=5)
        plan = AgentPlan(task_id="t1", objective="Bad schema", steps=[
            PlanStep(id="step_1", description="s1", tool_name="search", arguments={"wrong_arg": 123})
        ])
        errors = validator.validate(plan)
        assert any("Invalid arguments" in e.message for e in errors)

    def test_duplicate_step_id_fails(self, registry):
        validator = PlanValidator(tool_registry=registry, max_plan_steps=5)
        plan = AgentPlan(task_id="t1", objective="Dup", steps=[
            PlanStep(id="step_1", description="s1", tool_name="search", arguments={"query": "a"}),
            PlanStep(id="step_1", description="s2", tool_name="search", arguments={"query": "b"}),
        ])
        errors = validator.validate(plan)
        assert any("Duplicate step ID" in e.message for e in errors)

    def test_recursive_identical_tool_calls_fails(self, registry):
        validator = PlanValidator(tool_registry=registry, max_plan_steps=5)
        plan = AgentPlan(task_id="t1", objective="Loop", steps=[
            PlanStep(id="step_1", description="s1", tool_name="search", arguments={"query": "same"}),
            PlanStep(id="step_2", description="s2", tool_name="search", arguments={"query": "same"}),
        ])
        errors = validator.validate(plan)
        assert any("Recursive pattern" in e.message for e in errors)

    def test_forced_approval_on_high_risk_tool(self, registry):
        validator = PlanValidator(tool_registry=registry, max_plan_steps=5)
        step = PlanStep(
            id="step_1",
            description="Delete",
            tool_name="delete_data",
            arguments={"query": "old"},
            requires_approval=False,  # LLM forgot to set it
        )
        plan = AgentPlan(task_id="t1", objective="Delete", steps=[step])
        errors = validator.validate(plan)
        assert len(errors) == 0
        # Validator must force requires_approval = True
        assert step.requires_approval is True

    def test_file_read_placeholder_path_fails(self, registry):
        from backend.tools.file_read import FileReadInput
        registry.register(ToolDefinition(
            name="file_read",
            description="Read file",
            input_schema=FileReadInput,
            execute_fn=lambda **kw: "content",
            risk_level="medium",
            requires_approval=False,
            enabled=True,
        ))
        validator = PlanValidator(tool_registry=registry, max_plan_steps=5)

        for placeholder in ["document_0.txt", "document_1.txt", "doc_0.txt", "chunk_1"]:
            plan = AgentPlan(task_id="t1", objective="Read placeholder", steps=[
                PlanStep(id="step_1", description="s1", tool_name="file_read", arguments={"relative_path": placeholder})
            ])
            errors = validator.validate(plan)
            assert any("fabricated or placeholder document paths are not permitted" in e.message for e in errors)

