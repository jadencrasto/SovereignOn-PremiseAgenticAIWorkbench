"""
tests/backend/test_tasks.py
----------------------------
Phase 6 tests for TaskStore (SQLite persistence) and TaskManager (state machine).
"""

import pytest
from pathlib import Path

from backend.agent.planner import AgentPlan, PlanStep, StepStatus
from backend.agent.task import TaskManager, TaskStatus, TaskStateError
from backend.agent.task_store import TaskStore


class TestTaskStore:
    """Test SQLite persistence layer."""

    @pytest.fixture
    def store(self, tmp_path: Path):
        db_file = tmp_path / "test_tasks.db"
        return TaskStore(db_path=db_file)

    def test_save_and_get_task(self, store):
        store.save_task({
            "task_id": "t_1",
            "session_id": "s_1",
            "user_request": "Analyze report",
            "status": "pending",
        })
        row = store.get_task("t_1")
        assert row is not None
        assert row["task_id"] == "t_1"
        assert row["user_request"] == "Analyze report"
        assert row["status"] == "pending"

    def test_list_tasks_and_filter(self, store):
        store.save_task({"task_id": "t_1", "session_id": "s_1", "user_request": "r1", "status": "pending"})
        store.save_task({"task_id": "t_2", "session_id": "s_1", "user_request": "r2", "status": "completed"})

        all_tasks = store.list_tasks()
        assert len(all_tasks) == 2

        pending_tasks = store.list_tasks(status="pending")
        assert len(pending_tasks) == 1
        assert pending_tasks[0]["task_id"] == "t_1"

    def test_update_task_status(self, store):
        store.save_task({"task_id": "t_1", "session_id": "s_1", "user_request": "r1", "status": "pending"})
        store.update_task_status("t_1", "completed", result="Done successfully")
        row = store.get_task("t_1")
        assert row["status"] == "completed"
        assert row["result"] == "Done successfully"

    def test_delete_task(self, store):
        store.save_task({"task_id": "t_1", "session_id": "s_1", "user_request": "r1", "status": "pending"})
        assert store.delete_task("t_1") is True
        assert store.get_task("t_1") is None

    def test_save_and_list_audit_events(self, store):
        store.save_task({"task_id": "t_1", "session_id": "s_1", "user_request": "r1", "status": "pending"})
        store.save_event({
            "task_id": "t_1",
            "step_id": "s_1",
            "event_type": "tool_executed",
            "tool_name": "calculator",
            "risk_level": "low",
            "success": True,
        })
        events = store.list_events_for_task("t_1")
        assert len(events) == 1
        assert events[0]["event_type"] == "tool_executed"


class TestTaskManager:
    """Test Task state machine and validation."""

    @pytest.fixture
    def manager(self, tmp_path: Path):
        db_file = tmp_path / "test_tasks.db"
        store = TaskStore(db_path=db_file)
        return TaskManager(store=store)

    def test_create_task(self, manager):
        task = manager.create_task(session_id="sess_1", user_request="Do planning")
        assert task.task_id.startswith("task_")
        assert task.status == TaskStatus.PENDING

    def test_valid_task_lifecycle(self, manager):
        task = manager.create_task(session_id="sess_1", user_request="Do planning")

        # pending -> planning
        t = manager.update_status(task.task_id, TaskStatus.PLANNING)
        assert t.status == TaskStatus.PLANNING

        # planning -> executing
        t = manager.update_status(task.task_id, TaskStatus.EXECUTING)
        assert t.status == TaskStatus.EXECUTING

        # executing -> completed
        t = manager.update_status(task.task_id, TaskStatus.COMPLETED, result="All done")
        assert t.status == TaskStatus.COMPLETED
        assert t.result == "All done"
        assert t.completed_at is not None

    def test_invalid_task_transition_raises_error(self, manager):
        task = manager.create_task(session_id="sess_1", user_request="Do planning")
        # pending -> completed directly is illegal
        with pytest.raises(TaskStateError):
            manager.update_status(task.task_id, TaskStatus.COMPLETED)

    def test_step_lifecycle(self, manager):
        task = manager.create_task(session_id="sess_1", user_request="Do planning")
        plan = AgentPlan(
            task_id=task.task_id,
            objective="Test",
            steps=[PlanStep(id="s1", description="Step 1", tool_name="calculator", arguments={})],
        )
        manager.set_plan(task.task_id, plan)

        # pending -> running -> completed
        manager.update_step_status(task.task_id, "s1", StepStatus.running.value)
        updated = manager.update_step_status(task.task_id, "s1", StepStatus.completed.value, result="42")
        assert updated.plan.steps[0].status == StepStatus.completed.value
        assert updated.plan.steps[0].result == "42"

    def test_cancel_task(self, manager):
        task = manager.create_task(session_id="sess_1", user_request="Do planning")
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        cancelled = manager.cancel_task(task.task_id)
        assert cancelled.status == TaskStatus.CANCELLED


class TestFileWriteSynthesis:
    """Test dynamic file content synthesis and placeholder detection."""

    def test_is_placeholder_content_detection(self):
        from backend.agent.engine import AgentEngine
        # Should be identified as placeholders
        assert AgentEngine._is_placeholder_content("") is True
        assert AgentEngine._is_placeholder_content("text") is True
        assert AgentEngine._is_placeholder_content("summary") is True
        assert AgentEngine._is_placeholder_content("Summary of compressor issues found in documents.") is True
        assert AgentEngine._is_placeholder_content("text\nSummary of compressor issues found in documents.") is True
        assert AgentEngine._is_placeholder_content("[insert summary here]") is True

        # Should be identified as real content
        real_content = (
            "# Equipment Inspection Report\n\n"
            "Compressor K-101 experienced 9.4 mm/s RMS vibration due to Stage 3 "
            "polymer fouling and unbalance. Seal flush pressure loss was observed."
        )
        assert AgentEngine._is_placeholder_content(real_content) is False


class TestTaskExecutionCorrectness:
    """Test A-E: Step dependency awareness, failure tracking, calculator resolution, and grounding."""

    @pytest.fixture
    def engine(self, tmp_path: Path):
        from unittest.mock import MagicMock
        from backend.config import Settings
        from backend.agent.engine import AgentEngine
        from backend.agent.memory import ConversationMemory

        settings = Settings(
            upload_dir=tmp_path / "uploads",
            agents_dir=Path("agents"),
            data_dir=tmp_path / "data",
        )
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        router = MagicMock()
        router.default_model_id = "qwen2.5:7b"
        memory = ConversationMemory()
        return AgentEngine(settings=settings, router=router, memory=memory)

    def test_test_a_invalid_document_reference_failure_handling(self, engine):
        """Test A: When file_read fails, failure is tracked in step state and task is marked FAILED."""
        executed_step_results = [
            {
                "step_id": "s1",
                "tool": "document_search",
                "description": "Search documents",
                "arguments": {"query": "compressor"},
                "success": True,
                "error": None,
                "result": [{"filename": "doc_a.txt", "text": "Some text"}],
            },
            {
                "step_id": "s2",
                "tool": "file_read",
                "description": "Read file",
                "arguments": {"relative_path": "nonexistent_file.txt"},
                "success": False,
                "error": "File not found: 'nonexistent_file.txt'",
                "result": None,
            },
        ]

        messages = engine._build_task_reasoning_messages(
            session_id="s1",
            user_message="Summarize compressor issues",
            executed_step_results=executed_step_results,
            sources=[],
        )
        prompt_content = "\n".join(m.content for m in messages)
        assert "Step 2 (file_read" in prompt_content
        assert "FAILED" in prompt_content
        assert "File not found: 'nonexistent_file.txt'" in prompt_content
        assert "CRITICAL FACTUAL GROUNDING RULES" in prompt_content

    def test_test_b_canonical_path_resolution(self, engine, tmp_path):
        """Test B: Resolves genuine paths in upload_dir without fabricating paths from RAG result indexes."""
        # Create a real file in upload_dir
        real_file = tmp_path / "compressor_k101_inspection.md"
        real_file.write_text("Vibration test findings", encoding="utf-8")

        executed_step_results = [
            {
                "step_id": "s1",
                "tool": "document_search",
                "success": True,
                "result": [
                    {"filename": "compressor_k101_inspection.md", "relative_path": "compressor_k101_inspection.md"}
                ],
            }
        ]

        # Real file existing on disk is resolved
        resolved_real = engine._resolve_canonical_file_path(
            "compressor_k101_inspection.md", executed_step_results, tmp_path
        )
        assert resolved_real == "compressor_k101_inspection.md"

        # Fabricated placeholders are NOT mapped from RAG result indexes
        resolved_placeholder = engine._resolve_canonical_file_path(
            "document_0.txt", executed_step_results, tmp_path
        )
        assert resolved_placeholder == "document_0.txt"

        resolved_placeholder1 = engine._resolve_canonical_file_path(
            "document_1.txt", executed_step_results, tmp_path
        )
        assert resolved_placeholder1 == "document_1.txt"

    @pytest.mark.asyncio
    async def test_test_c_calculator_expression_resolution_numeric(self, engine):
        """Test C: Valid pure arithmetic expressions are accepted directly."""
        expr = "4 + 3 + 2"
        resolved = await engine._resolve_calculator_expression(
            expression=expr,
            step_description="Calculate total",
            user_request="calculate total",
            executed_step_results=[],
            provider=None,
            model_name="mock",
        )
        assert resolved == "4 + 3 + 2"

    @pytest.mark.asyncio
    async def test_test_d_calculator_missing_values_fails_cleanly(self, engine):
        """Test D: Calculator rejects symbolic names without observations and returns None."""
        resolved = await engine._resolve_calculator_expression(
            expression="issues_doc1 + issues_doc2",
            step_description="Calculate total",
            user_request="calculate total",
            executed_step_results=[],
            provider=None,
            model_name="mock",
        )
        assert resolved is None

    def test_test_e_no_hallucinated_filenames_in_planner_prompt(self):
        """Test E: Planner prompt explicitly instructs not to invent generic filenames or call file_read after document_search."""
        from backend.agent.planner import _PLAN_SYSTEM_PROMPT

        assert "document_0.txt" in _PLAN_SYSTEM_PROMPT
        assert "document_search: Searches and retrieves text passages directly" in _PLAN_SYSTEM_PROMPT
        assert "Do NOT follow document_search with file_read" in _PLAN_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_test_g_planner_prunes_fabricated_file_read_after_document_search(self):
        """Test G: Planner automatically prunes fabricated file_read calls (e.g. document_0.txt)."""
        from unittest.mock import AsyncMock, MagicMock
        from backend.agent.planner import AgentPlanner
        from backend.models.base import ChatResponse

        planner = AgentPlanner(max_plan_steps=5)
        mock_provider = AsyncMock()
        mock_provider.chat.return_value = ChatResponse(
            content="""[
                {"description": "Search compressor issues in docs", "tool_name": "document_search", "arguments": {"query": "compressor issues"}, "requires_approval": false},
                {"description": "Read document 0", "tool_name": "file_read", "arguments": {"relative_path": "document_0.txt"}, "requires_approval": false},
                {"description": "Save compressor summary", "tool_name": "file_write", "arguments": {"filename": "compressor_summary.txt", "content": "summary"}, "requires_approval": true}
            ]""",
            model="qwen2.5:7b",
            provider="ollama",
        )
        mock_registry = MagicMock()
        mock_registry.list_enabled_tools.return_value = []

        plan = await planner.create_plan(
            task_id="t_compressor",
            objective="Create a file named compressor_summary.txt containing a short summary of the recurring compressor issues found in the local documents.",
            tool_registry=mock_registry,
            provider=mock_provider,
            model_name="qwen2.5:7b",
        )

        # file_read with document_0.txt must be pruned
        tool_names = [s.tool_name for s in plan.steps]
        assert "file_read" not in tool_names
        assert tool_names == ["document_search", "file_write"]
        assert plan.steps[1].requires_approval is True
        assert plan.steps[1].arguments["filename"] == "compressor_summary.txt"

    def test_test_f_unrelated_query_grounding_relevance_gate(self, engine):
        """Test F: When document_search yields 0 relevant results, reasoning prompt enforces strict refusal to fabricate."""
        executed_step_results = [
            {
                "step_id": "s1",
                "tool": "document_search",
                "description": "Search local documents for aircraft engine failures",
                "arguments": {"query": "aircraft engine failures", "top_k": 5},
                "success": True,
                "result": [],
                "summary": "0 results returned",
            }
        ]

        messages = engine._build_task_reasoning_messages(
            session_id="s_aircraft",
            user_message="Search the local documents for information about aircraft engine failures and summarize the findings.",
            executed_step_results=executed_step_results,
            sources=[],
        )

        prompt_content = "\n".join(m.content for m in messages)
        assert "Step 1 (document_search" in prompt_content
        assert "SUCCESS" in prompt_content
        assert "[]" in prompt_content
        assert "If document search or retrieval returned 0 results, or if no sufficiently relevant local evidence was found" in prompt_content
        assert "NEVER fabricate information, invent facts, or reinterpret/transfer facts from unrelated equipment" in prompt_content

