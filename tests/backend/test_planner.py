"""
tests/backend/test_planner.py
------------------------------
Phase 6 tests for AgentPlanner and deterministic complexity heuristic.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.agent.planner import (
    AgentPlanner,
    PlanStep,
    AgentPlan,
    StepStatus,
    PlanStatus,
    should_use_planning,
)
from backend.models.base import ChatResponse


class TestComplexityHeuristic:
    """Test deterministic complexity heuristic (runs BEFORE LLM execution)."""

    def test_disabled_planning_returns_false(self):
        assert not should_use_planning("Create a report from data", planning_enabled=False)

    def test_disabled_tools_returns_false(self):
        assert not should_use_planning("Create a report from data", tools_enabled=False)

    def test_simple_greetings_bypass_planning(self):
        assert not should_use_planning("hello there")
        assert not should_use_planning("hi")
        assert not should_use_planning("good morning assistant")

    def test_simple_questions_bypass_planning(self):
        assert not should_use_planning("What is quantum computing?")
        assert not should_use_planning("Explain photosynthesis in simple terms")

    def test_simple_calculator_bypasses_planning(self):
        assert not should_use_planning("calculate 125 * 840")

    def test_short_messages_bypass_planning(self):
        assert not should_use_planning("find files")
        assert not should_use_planning("search for test")

    def test_multi_step_keyword_triggers_planning(self):
        assert should_use_planning("search the documents and then calculate the average score")
        assert should_use_planning("first read the budget file and then summarize it")
        assert should_use_planning("find all sales files and then compare the totals")

    def test_write_operations_trigger_planning(self):
        assert should_use_planning("calculate the totals and save the result to report.txt")
        assert should_use_planning("create a report summarizing the Q3 performance")
        assert should_use_planning("write a file with the calculated summary")


class TestAgentPlanner:
    """Test LLM-based plan generation and parsing."""

    @pytest.mark.asyncio
    async def test_create_plan_valid_json(self):
        planner = AgentPlanner(max_plan_steps=5)

        mock_provider = AsyncMock()
        mock_provider.chat.return_value = ChatResponse(
            content="""[
                {"description": "Search document", "tool_name": "document_search", "arguments": {"query": "revenue"}, "requires_approval": false},
                {"description": "Calculate growth", "tool_name": "calculator", "arguments": {"expression": "100 * 1.1"}, "requires_approval": false},
                {"description": "Save report", "tool_name": "file_write", "arguments": {"filename": "rev.txt", "content": "110"}, "requires_approval": true}
            ]""",
            model="qwen2.5:7b",
            provider="ollama",
        )

        mock_registry = MagicMock()
        mock_registry.list_enabled_tools.return_value = []

        plan = await planner.create_plan(
            task_id="task_123",
            objective="Analyze revenue and write report",
            tool_registry=mock_registry,
            provider=mock_provider,
            model_name="qwen2.5:7b",
        )

        assert plan.task_id == "task_123"
        assert len(plan.steps) == 3
        assert plan.steps[0].tool_name == "document_search"
        assert plan.steps[1].tool_name == "calculator"
        assert plan.steps[2].tool_name == "file_write"
        assert plan.steps[2].requires_approval is True

    @pytest.mark.asyncio
    async def test_create_plan_markdown_wrapped_json(self):
        planner = AgentPlanner(max_plan_steps=5)

        mock_provider = AsyncMock()
        mock_provider.chat.return_value = ChatResponse(
            content="""Here is the execution plan:
```json
[
    {"description": "Find files", "tool_name": "file_list", "arguments": {}}
]
```""",
            model="qwen2.5:7b",
            provider="ollama",
        )

        mock_registry = MagicMock()
        mock_registry.list_enabled_tools.return_value = []

        plan = await planner.create_plan(
            task_id="task_456",
            objective="List all files",
            tool_registry=mock_registry,
            provider=mock_provider,
            model_name="qwen2.5:7b",
        )

        assert plan.task_id == "task_456"
        assert len(plan.steps) == 1
        assert plan.steps[0].tool_name == "file_list"

    @pytest.mark.asyncio
    async def test_create_plan_invalid_json_fallback(self):
        planner = AgentPlanner(max_plan_steps=5)

        mock_provider = AsyncMock()
        mock_provider.chat.return_value = ChatResponse(
            content="I am not able to output JSON for this request.",
            model="qwen2.5:7b",
            provider="ollama",
        )

        mock_registry = MagicMock()
        mock_registry.list_enabled_tools.return_value = []

        plan = await planner.create_plan(
            task_id="task_789",
            objective="Invalid JSON test",
            tool_registry=mock_registry,
            provider=mock_provider,
            model_name="qwen2.5:7b",
        )

        assert plan.task_id == "task_789"
        assert len(plan.steps) == 1
        assert plan.steps[0].tool_name is None  # fallback reasoning step
