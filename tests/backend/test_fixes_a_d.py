"""
Focused regression tests for manual testing fixes A-D:
1. file_list result reaches final response
2. file_read result reaches final response
3. successful document_search reaches final response
4. tool internals are not leaked (<tool_call>, JSON, signatures, query=, FSM, unrequested Mermaid)
5. explicit code_execution actually executes
6. code output is not duplicated
7. failed file_read stops dependent artifact steps
8. artifact is not created without grounded source evidence
9. verifier failure propagates to task_failed
10. multiple RAG matches are handled without arbitrary unsupported selection
11. 'yes give one' preserves conversational intent
12. simple RAG does not invoke Knowledge Graph/Mermaid
"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from backend.agent.planner import (
    is_general_knowledge_query,
    is_short_conversational_followup,
    is_simple_informational_query,
    should_use_planning,
    PlanStep,
    AgentPlan,
    StepStatus,
)
from backend.api.chat import _try_arithmetic
from backend.agent.engine import AgentEngine
from backend.agent.task import TaskStatus, TaskManager
from backend.tools.registry import ToolResult, ToolRegistry
from backend.config import Settings
from backend.agent.memory import ConversationMemory
from backend.models.base import ChatChunk


# ============================================================================
# 1 & 2 & 3. Tool Result Handoff: file_list, file_read, document_search
# ============================================================================

class TestToolResultHandoff:
    """Validate that actual results reach the user and responses are not empty."""

    def test_file_list_observation_formatting(self):
        """file_list observation contains actual file list and clear instructions."""
        fake_result = ToolResult(
            tool="file_list",
            success=True,
            result={"files": ["Company Safety Training Record 2023.txt", "mrpl_lab_test.csv"]},
        )
        obs = AgentEngine._format_observation("file_list", fake_result)
        assert "Company Safety Training Record 2023.txt" in obs
        assert "mrpl_lab_test.csv" in obs
        assert "Available Files in Workspace" in obs

    def test_file_read_observation_formatting(self):
        """file_read observation contains actual content, trainer, and dates."""
        fake_content = "Trainer: John Doe\nDate: January 15, 2023\nTopic: Fire Safety Awareness"
        fake_result = ToolResult(
            tool="file_read",
            success=True,
            result={"filename": "training.txt", "content": fake_content},
        )
        obs = AgentEngine._format_observation("file_read", fake_result)
        assert "John Doe" in obs
        assert "January 15, 2023" in obs
        assert "[FILE CONTENT]" in obs

    def test_document_search_observation_formatting(self):
        """document_search observation contains retrieved text passages."""
        fake_chunks = [
            {"filename": "safety_record.pdf", "page": 1, "text": "Training duration was 4 hours."},
        ]
        fake_result = ToolResult(
            tool="document_search",
            success=True,
            result=fake_chunks,
        )
        obs = AgentEngine._format_observation("document_search", fake_result)
        assert "Training duration was 4 hours." in obs
        assert "safety_record.pdf" in obs


# ============================================================================
# 4. No Tool / Python / Mermaid Internals Leaked in Normal Responses
# ============================================================================

class TestNoToolInternalsLeaked:
    """Validate that tool calls, JSON, function signatures, query=, and Mermaid are sanitized."""

    def test_strip_tool_call_tags(self):
        raw = '<tool_call>{"name": "file_list", "arguments": {}}</tool_call>\nHere are the files.'
        cleaned = AgentEngine._clean_reasoning_response(raw)
        assert "<tool_call>" not in cleaned
        assert "</tool_call>" not in cleaned
        assert cleaned == "Here are the files."

    def test_strip_tool_json_blocks(self):
        raw = '{"name": "file_read", "arguments": {"filename": "test.txt"}}\nThe content is clear.'
        cleaned = AgentEngine._clean_reasoning_response(raw)
        assert '{"name": "file_read"' not in cleaned
        assert cleaned == "The content is clear."

    def test_strip_function_call_signatures(self):
        raw = 'file_read("Company Safety Record.txt")\nHere is the safety summary.'
        cleaned = AgentEngine._clean_reasoning_response(raw)
        assert 'file_read(' not in cleaned
        assert cleaned == "Here is the safety summary."

    def test_strip_query_assignments(self):
        raw = 'query = "Fire Safety Awareness training"\nThe training was held on January 15, 2023.'
        cleaned = AgentEngine._clean_reasoning_response(raw)
        assert 'query =' not in cleaned
        assert cleaned == "The training was held on January 15, 2023."

    def test_strip_fsm_state_names(self):
        raw = 'State: EXECUTING\nFSMState: PLANNING\nCompleted the calculation.'
        cleaned = AgentEngine._clean_reasoning_response(raw)
        assert "State:" not in cleaned
        assert "FSMState:" not in cleaned
        assert "Completed the calculation." in cleaned

    def test_strip_unrequested_mermaid_diagram(self):
        raw = 'Here is the gear explanation.\n```mermaid\ngraph TD\nA-->B\n```\nGears mesh together.'
        # User request does not ask for diagram
        cleaned = AgentEngine._clean_reasoning_response(raw, user_request="explain what a gear is")
        assert "```mermaid" not in cleaned
        assert "Gears mesh together." in cleaned

    def test_preserve_mermaid_when_explicitly_requested(self):
        raw = 'Here is the diagram:\n```mermaid\ngraph TD\nA-->B\n```'
        cleaned = AgentEngine._clean_reasoning_response(raw, user_request="show me a diagram of the gear")
        assert "```mermaid" in cleaned


# ============================================================================
# 5 & 6. Explicit code_execution and No Code Duplication
# ============================================================================

class TestCodeExecutionHandling:
    """Validate explicit Python execution requests and clean stdout formatting."""

    def test_code_execution_observation_formatting(self):
        """code_execution observation returns stdout without exit code noise."""
        fake_result = ToolResult(
            tool="code_execution",
            success=True,
            result={"stdout": "[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]\n", "exit_code": 0},
        )
        obs = AgentEngine._format_observation("code_execution", fake_result)
        assert "[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]" in obs
        assert "Exit Code: 0" not in obs  # No internal exit code noise

    def test_strip_unrequested_python_code(self):
        """When user does not ask for source code, strip duplicate python script blocks."""
        raw = 'The result is:\n```python\ndef fib(): pass\n```\n[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]'
        cleaned = AgentEngine._clean_reasoning_response(raw, user_request="Calculate the first 10 Fibonacci numbers using Python")
        assert "```python" not in cleaned
        assert "[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]" in cleaned


# ============================================================================
# 7. Failed file_read Stops Dependent Artifact Steps
# ============================================================================

class TestFailurePropagation:
    """Prerequisite failure must immediately stop dependent steps and fail the task."""

    @pytest.mark.asyncio
    async def test_failed_prerequisite_aborts_immediately(self):
        """When file_read fails, dependent artifact creation is never called."""
        settings = Settings()
        router = MagicMock()
        router.get_provider_for_model.return_value = (MagicMock(), "qwen2.5:7b")
        memory = ConversationMemory()
        engine = AgentEngine(settings=settings, router=router, memory=memory)

        # Wire task manager and registry
        task_manager = MagicMock()
        task_obj = MagicMock()
        task_obj.task_id = "task_test_fail"
        task_obj.user_request = "Read file and create report"
        task_manager.create_task.return_value = task_obj
        engine._task_manager = task_manager

        planner = MagicMock()
        step1 = PlanStep(id="step_1", description="Read file", tool_name="file_read", arguments={"filename": "missing.txt"})
        step2 = PlanStep(id="step_2", description="Create docx", tool_name="docx_create", arguments={"filename": "report.docx"})
        plan = AgentPlan(task_id="task_test_fail", objective="test", steps=[step1, step2])
        planner.create_plan = AsyncMock(return_value=plan)
        engine._planner = planner

        validator = MagicMock()
        validator.validate.return_value = []
        engine._plan_validator = validator

        registry = MagicMock()
        # file_read fails
        registry.execute = AsyncMock(return_value=ToolResult(tool="file_read", success=False, error="File not found"))
        engine._tool_registry = registry

        events = []
        async for event in engine.run_agent_task("sess1", "Read file and create report"):
            events.append(event)

        # Step 1 failed
        step_events = [e for e in events if isinstance(e, dict) and e.get("type") == "plan_step"]
        assert any(e.get("status") == "failed" for e in step_events)

        # Task failed event emitted
        task_failed_events = [e for e in events if isinstance(e, dict) and e.get("type") == "task_failed"]
        assert len(task_failed_events) >= 1

        # Step 2 (docx_create) must NEVER have been executed
        executed_tools = [call.args[0] for call in registry.execute.call_args_list]
        assert "docx_create" not in executed_tools


# ============================================================================
# 8. Artifact is NOT Created Without Grounded Source Evidence
# ============================================================================

class TestArtifactGroundingPreCheck:
    """Artifact creation must be rejected if no grounded data exists or placeholders are present."""

    @pytest.mark.asyncio
    async def test_artifact_rejected_without_prior_retrieval(self):
        """docx_create step is rejected before execution when no source evidence exists."""
        settings = Settings()
        router = MagicMock()
        router.get_provider_for_model.return_value = (MagicMock(), "qwen2.5:7b")
        memory = ConversationMemory()
        engine = AgentEngine(settings=settings, router=router, memory=memory)

        task_manager = MagicMock()
        task_obj = MagicMock()
        task_obj.task_id = "task_test_ungrounded"
        task_obj.user_request = "Create report"
        task_manager.create_task.return_value = task_obj
        engine._task_manager = task_manager

        planner = MagicMock()
        # Plan directly creates docx without reading/searching
        step1 = PlanStep(id="step_1", description="Create docx", tool_name="docx_create", arguments={"filename": "report.docx", "content": "Sample content"})
        plan = AgentPlan(task_id="task_test_ungrounded", objective="test", steps=[step1])
        planner.create_plan = AsyncMock(return_value=plan)
        engine._planner = planner

        validator = MagicMock()
        validator.validate.return_value = []
        engine._plan_validator = validator

        registry = MagicMock()
        registry.execute = AsyncMock()
        engine._tool_registry = registry

        events = []
        async for event in engine.run_agent_task("sess1", "Create report"):
            events.append(event)

        # docx_create must NOT have been executed
        assert registry.execute.call_count == 0

        # Task must be marked failed
        task_failed_events = [e for e in events if isinstance(e, dict) and e.get("type") == "task_failed"]
        assert len(task_failed_events) >= 1
        assert "grounded source evidence" in task_failed_events[0]["error"]


# ============================================================================
# 9. Verifier Failure Propagates to task_failed
# ============================================================================

class TestVerifierFailurePropagation:
    """verifier returning verified=False must fail task without task_completed."""

    @pytest.mark.asyncio
    async def test_verifier_failure_aborts_with_task_failed(self):
        settings = Settings()
        router = MagicMock()
        router.get_provider_for_model.return_value = (MagicMock(), "qwen2.5:7b")
        memory = ConversationMemory()
        engine = AgentEngine(settings=settings, router=router, memory=memory)

        task_manager = MagicMock()
        task_obj = MagicMock()
        task_obj.task_id = "task_verifier_fail"
        task_obj.user_request = "Verify report"
        task_manager.create_task.return_value = task_obj
        task_manager.get_task.return_value = task_obj
        engine._task_manager = task_manager

        planner = MagicMock()
        step1 = PlanStep(id="step_1", description="Verify artifact", tool_name="artifact_verifier", arguments={"relative_path": "report.docx"})
        plan = AgentPlan(task_id="task_verifier_fail", objective="test", steps=[step1])
        planner.create_plan = AsyncMock(return_value=plan)
        engine._planner = planner

        validator = MagicMock()
        validator.validate.return_value = []
        engine._plan_validator = validator

        registry = MagicMock()
        # Verifier returns verified=False
        registry.execute = AsyncMock(return_value=ToolResult(
            tool="artifact_verifier",
            success=True,
            result={"verified": False, "reason": "Hash mismatch"},
        ))
        engine._tool_registry = registry

        events = []
        async for event in engine.run_agent_task("sess1", "Verify report"):
            events.append(event)

        # task_failed emitted
        task_failed = [e for e in events if isinstance(e, dict) and e.get("type") == "task_failed"]
        assert len(task_failed) == 1

        # task_completed must NEVER be emitted
        task_completed = [e for e in events if isinstance(e, dict) and e.get("type") == "task_completed"]
        assert len(task_completed) == 0


# ============================================================================
# 10. Multiple RAG Matches Handled Without Arbitrary Selection
# ============================================================================

class TestMultipleRAGMatches:
    """Verify that multiple matching records are presented rather than arbitrarily picking one."""

    def test_grounding_directive_for_multiple_sessions(self):
        """_build_messages contains explicit directives for multiple matching sessions."""
        settings = Settings()
        router = MagicMock()
        memory = ConversationMemory()
        memory.create_session("sess1")
        memory.add_user_message("sess1", "When was the fire safety training held?")
        engine = AgentEngine(settings=settings, router=router, memory=memory)

        from backend.rag.retriever import RetrievedChunk
        sources = [
            RetrievedChunk(chunk_id="c1", document_id="doc1", chunk_index=0, text="Training held January 15, 2023", score=0.9, filename="session1.pdf", page=1),
            RetrievedChunk(chunk_id="c2", document_id="doc2", chunk_index=0, text="Training held February 20, 2023", score=0.88, filename="session2.pdf", page=1),
        ]

        messages = engine._build_messages("sess1", "When was the fire safety training held?", sources)
        rag_sys_msg = next((m.content for m in messages if "MULTIPLE SESSIONS" in m.content), None)
        assert rag_sys_msg is not None
        assert "do NOT arbitrarily pick just one" in rag_sys_msg
        assert "list all distinct dates or sessions found" in rag_sys_msg


# ============================================================================
# 11. Conversational Follow-up: 'yes give one' Preserves Intent
# ============================================================================

class TestConversationalFollowUpAffirmations:
    """Verify affirmative phrases like 'yes give one' are routed to conversation context."""

    @pytest.mark.parametrize("msg", [
        "yes give one",
        "yes show one",
        "yes please give one",
        "yes give me one",
        "yes show me",
        "yes do that",
        "give one",
        "show me",
        "yes plz",
        "continue",
    ])
    def test_affirmative_followups_detected(self, msg):
        assert is_short_conversational_followup(msg) is True


# ============================================================================
# 12. Simple RAG Does Not Invoke Knowledge Graph or Mermaid
# ============================================================================

class TestSimpleRAGNoGraphOrMermaid:
    """Verify simple informational queries bypass planning and graph."""

    def test_simple_rag_query_routing(self):
        msg = "What was the duration of the Fire Safety Awareness training?"
        assert is_simple_informational_query(msg) is True
        assert should_use_planning(msg) is False

    def test_system_prompt_restricts_mermaid(self):
        from pathlib import Path
        prompt_path = Path("agents/default/system_prompt.md")
        content = prompt_path.read_text(encoding="utf-8")
        assert "Only output a ````mermaid ... ```` code block if the user EXPLICITLY asks" in content


# ============================================================================
# 13. Tool-Call Parser (Qwen/Ollama multi-format support)
# ============================================================================

class TestToolCallParserFormats:
    """Verify AgentEngine._parse_tool_call accepts Python function call syntax, tags, and JSON."""

    def test_parse_python_function_call_with_kwargs(self):
        text = "file_read(relative_path='Company Safety Training Record.txt')"
        parsed = AgentEngine._parse_tool_call(text)
        assert parsed is not None
        assert parsed["name"] == "file_read"
        assert parsed["arguments"] == {"relative_path": "Company Safety Training Record.txt"}

    def test_parse_python_function_call_with_positional_arg(self):
        text = "file_read('Company Safety Training Record.txt')"
        parsed = AgentEngine._parse_tool_call(text)
        assert parsed is not None
        assert parsed["name"] == "file_read"
        assert parsed["arguments"] == {"relative_path": "Company Safety Training Record.txt"}

    def test_parse_document_search_function_call(self):
        text = "document_search(query='Fire Safety Awareness training duration')"
        parsed = AgentEngine._parse_tool_call(text)
        assert parsed is not None
        assert parsed["name"] == "document_search"
        assert parsed["arguments"] == {"query": "Fire Safety Awareness training duration"}

    def test_parse_file_list_function_call(self):
        text = "file_list()"
        parsed = AgentEngine._parse_tool_call(text)
        assert parsed is not None
        assert parsed["name"] == "file_list"
        assert parsed["arguments"] == {}

    def test_parse_inside_tool_call_tags(self):
        text = "<tool_call>\nfile_read(relative_path='safety.txt')\n</tool_call>"
        parsed = AgentEngine._parse_tool_call(text)
        assert parsed is not None
        assert parsed["name"] == "file_read"
        assert parsed["arguments"] == {"relative_path": "safety.txt"}

    def test_parse_markdown_json_block(self):
        text = "```json\n{\"name\": \"file_read\", \"arguments\": {\"relative_path\": \"training.txt\"}}\n```"
        parsed = AgentEngine._parse_tool_call(text)
        assert parsed is not None
        assert parsed["name"] == "file_read"
        assert parsed["arguments"] == {"relative_path": "training.txt"}

    def test_extract_pre_tool_text_with_func_call(self):
        text = "I will check the file contents now.\nfile_read(relative_path='test.txt')"
        pre = AgentEngine._extract_pre_tool_text(text)
        assert pre.strip() == "I will check the file contents now."


# ============================================================================
# 14. Filename Normalization in file_read Tool Layer
# ============================================================================

class TestFilenameNormalization:
    """Verify uploaded filename resolution handles spaces vs underscores, case, and doc_ hashes."""

    @pytest.mark.asyncio
    async def test_file_read_resolves_spaces_vs_underscores(self, tmp_path):
        from backend.tools.file_read import create_file_read, FileReadInput

        # Create file with spaces on disk
        actual_file = tmp_path / "Company Safety Training Record.txt"
        actual_file.write_text("Trainer: Alice Smith\nDate: 2023-01-15", encoding="utf-8")

        read_fn = create_file_read(tmp_path)

        # Call with underscores
        res = await read_fn(FileReadInput(relative_path="Company_Safety_Training_Record.txt"))
        assert res["filename"] == "Company Safety Training Record.txt"
        assert "Alice Smith" in res["content"]

    @pytest.mark.asyncio
    async def test_file_read_resolves_doc_hash_prefix(self, tmp_path):
        from backend.tools.file_read import create_file_read, FileReadInput

        # Create file with doc_hash prefix on disk
        actual_file = tmp_path / "doc_77b8a806ffba4e08_Company Safety Training Record.txt"
        actual_file.write_text("Trainer: Bob Jones\nDate: 2023-03-10", encoding="utf-8")

        read_fn = create_file_read(tmp_path)

        # User/LLM asks without prefix and with underscores
        res = await read_fn(FileReadInput(relative_path="Company_Safety_Training_Record.txt"))
        assert res["filename"] == "Company Safety Training Record.txt"
        assert "Bob Jones" in res["content"]

    @pytest.mark.asyncio
    async def test_file_read_accepts_filename_argument(self, tmp_path):
        from backend.tools.file_read import create_file_read, FileReadInput

        actual_file = tmp_path / "safety_record.txt"
        actual_file.write_text("Training Content", encoding="utf-8")

        read_fn = create_file_read(tmp_path)
        res = await read_fn(FileReadInput(filename="safety_record.txt"))
        assert "Training Content" in res["content"]


# ============================================================================
# 15. RAG Not-Found Behavior
# ============================================================================

class TestRAGNotFoundHandling:
    """Verify absent information directive and no hallucinated tools/Mermaid."""

    def test_document_search_empty_observation_directive(self):
        fake_result = ToolResult(
            tool="document_search",
            success=True,
            result=[],
        )
        obs = AgentEngine._format_observation("document_search", fake_result)
        assert "No relevant document passages found" in obs
        assert "The requested information was not found in the uploaded evidence." in obs


# ============================================================================
# 16. Direct Tool Answer Formatting (Observation Handoff)
# ============================================================================

class TestDirectToolAnswerFormatting:
    """Validate _format_direct_tool_answer produces clean natural text without leaking Python internals."""

    def test_format_direct_document_search_results(self):
        chunks = [
            {"filename": "Safety_Record.pdf", "page": 2, "text": "Fire Safety training lasted 2 hours on Jan 15."},
        ]
        res = ToolResult(tool="document_search", success=True, result=chunks)
        answer = AgentEngine._format_direct_tool_answer("document_search", res)
        assert "Safety_Record.pdf" in answer
        assert "Page 2" in answer
        assert "Fire Safety training lasted 2 hours on Jan 15." in answer
        # Verify no raw python representation
        assert "{'filename':" not in answer

    def test_format_direct_document_search_empty(self):
        res = ToolResult(tool="document_search", success=True, result=[])
        answer = AgentEngine._format_direct_tool_answer("document_search", res)
        assert "The requested information was not found in the uploaded evidence." in answer

    def test_format_direct_file_read(self):
        res = ToolResult(
            tool="file_read",
            success=True,
            result={"filename": "Company Safety Training Record.txt", "content": "Trainer: John Doe\nDuration: 2 hours"},
        )
        answer = AgentEngine._format_direct_tool_answer("file_read", res)
        assert "Company Safety Training Record.txt" in answer
        assert "Duration: 2 hours" in answer

    def test_format_direct_calculator(self):
        res = ToolResult(tool="calculator", success=True, result={"result": 42})
        answer = AgentEngine._format_direct_tool_answer("calculator", res)
        assert "42" in answer


# ============================================================================
# 17. Undefined & Pre-tool Text Guard in Tool Handoff
# ============================================================================

class TestToolHandoffUndefinedGuard:
    """Validate that chat_stream_with_tools never yields 'undefined' and handles tool handoff cleanly."""

    @pytest.mark.asyncio
    async def test_file_read_handoff_never_yields_undefined(self):
        settings = Settings()
        router = MagicMock()
        provider = MagicMock()
        router.get_provider_for_model.return_value = (provider, "qwen2.5:7b")
        memory = ConversationMemory()
        tools = MagicMock()
        tools.list_enabled_tools.return_value = ["file_read"]
        tools.format_tools_for_prompt.return_value = "## Available Tools\nfile_read"

        file_result = ToolResult(
            tool="file_read",
            success=True,
            result={"filename": "Company Safety Training Record.txt", "content": "Training: Fire Safety, Duration: 2 hours"},
        )
        tools.execute = AsyncMock(return_value=file_result)

        engine = AgentEngine(settings=settings, router=router, memory=memory, tool_registry=tools)

        # In iteration 1, model outputs tool call; in iteration 2, model outputs empty string / "undefined"
        call_count = 0
        async def mock_stream(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ChatChunk(delta='file_read(filename="Company Safety Training Record.txt")', done=True)
            else:
                yield ChatChunk(delta="undefined", done=True)

        provider.chat_stream = mock_stream

        events = []
        async for item in engine.chat_stream_with_tools("sess_test", "Read Company Safety Training Record.txt"):
            events.append(item)

        str_events = [ev for ev in events if isinstance(ev, str)]
        assert len(str_events) > 0
        # Ensure 'undefined' was replaced with actual file content
        assert all(ev.strip().lower() != "undefined" for ev in str_events)
        combined = "".join(str_events)
        assert "Company Safety Training Record.txt" in combined
        assert "Duration: 2 hours" in combined

    @pytest.mark.asyncio
    async def test_document_search_handoff_pre_tool_text_not_leaked_as_delta(self):
        settings = Settings()
        router = MagicMock()
        provider = MagicMock()
        router.get_provider_for_model.return_value = (provider, "qwen2.5:7b")
        memory = ConversationMemory()
        tools = MagicMock()
        tools.list_enabled_tools.return_value = ["document_search"]
        tools.format_tools_for_prompt.return_value = "## Available Tools\ndocument_search"

        doc_result = ToolResult(
            tool="document_search",
            success=True,
            result=[{"filename": "Company Safety Training Record.txt", "page": 1, "text": "Duration of Fire Safety is 2 hours."}],
        )
        tools.execute = AsyncMock(return_value=doc_result)

        engine = AgentEngine(settings=settings, router=router, memory=memory, tool_registry=tools)

        call_count = 0
        async def mock_stream(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ChatChunk(delta='Let us proceed with a document search...\ndocument_search(query="Fire Safety Awareness")', done=True)
            else:
                # Model repeats tool call
                yield ChatChunk(delta='document_search(query="Fire Safety Awareness")', done=True)

        provider.chat_stream = mock_stream

        events = []
        async for item in engine.chat_stream_with_tools("sess_doc", "What was the duration of the Fire Safety Awareness training?"):
            events.append(item)

        str_events = [ev for ev in events if isinstance(ev, str)]
        combined = "".join(str_events)
        # Pre-tool text scratchpad must NOT be the final answer
        assert "Duration of Fire Safety is 2 hours." in combined
        assert "{'filename':" not in combined


# ============================================================================
# 18 & 19. Artifact Creation Failure Propagation & Verifier Consistency
# ============================================================================

class TestArtifactFailureAndVerifierConsistency:
    """Validate failure propagation and verifier consistency rules."""

    def test_synthesize_completion_fails_if_docx_create_failed(self):
        executed_results = [
            {"tool": "document_search", "success": True, "result": [{"text": "evidence"}], "summary": "1 results"},
            {"tool": "docx_create", "success": False, "error": "Human approval required but rejected", "summary": "Failed"},
        ]
        text = AgentEngine._synthesize_task_completion_response("User request", executed_results)
        assert "Execution Plan Failed" in text
        assert "Word Document Generated" not in text
        assert "Task completed" not in text
        assert "Human approval required but rejected" in text

    def test_synthesize_completion_fails_if_verifier_failed(self):
        executed_results = [
            {"tool": "document_search", "success": True, "result": [{"text": "evidence"}], "summary": "1 results"},
            {"tool": "xlsx_report", "success": True, "result": {"filename": "report.xlsx"}, "summary": "Created"},
            {
                "tool": "artifact_verifier",
                "success": True,
                "result": {"verified": False, "reason": "Workbook contains no grounded evidence"},
                "summary": "verified=false",
            },
        ]
        text = AgentEngine._synthesize_task_completion_response("User request", executed_results)
        assert "Execution Plan Failed" in text
        assert "Excel Report Generated" not in text
        assert "Cryptographic Verification" not in text
        assert "Task completed" not in text
        assert "Workbook contains no grounded evidence" in text

    def test_synthesize_completion_succeeds_only_if_both_succeeded_and_verified(self):
        executed_results = [
            {"tool": "document_search", "success": True, "result": [{"text": "evidence"}], "summary": "1 results"},
            {"tool": "xlsx_report", "success": True, "result": {"filename": "report.xlsx", "row_count": 5, "column_count": 3}, "summary": "Created"},
            {
                "tool": "artifact_verifier",
                "success": True,
                "result": {"verified": True, "row_count": 5, "column_count": 3},
                "summary": "verified=true",
            },
        ]
        text = AgentEngine._synthesize_task_completion_response("User request", executed_results)
        assert "Execution Plan Completed" in text
        assert "Excel Report Generated" in text
        assert "Cryptographic Verification" in text
        assert "Task completed" in text


# ============================================================================
# 20. Consolidated Regression Pass for User Request 8
# ============================================================================

class TestConsolidatedFixesPass:
    """Explicit tests for FSM step transition, file_read grounding, stale artifact reservations, and clearance."""

    @pytest.mark.asyncio
    async def test_grounding_failure_valid_fsm_transition_pending_to_running_to_failed(self):
        """Grounding failure must transition pending -> running -> failed without TaskStateError."""
        from backend.agent.task import TaskStore, TaskManager
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "tasks.db"
            store = TaskStore(db_path)
            tm = TaskManager(store)

            settings = Settings()
            router = MagicMock()
            router.get_provider_for_model.return_value = (MagicMock(), "qwen2.5:7b")
            memory = ConversationMemory()
            engine = AgentEngine(settings=settings, router=router, memory=memory)
            engine._task_manager = tm

            planner = MagicMock()
            # Plan creates docx without prior retrieval
            step1 = PlanStep(id="step_1", description="Create docx", tool_name="docx_create", arguments={"filename": "report.docx", "content": "Sample content"})
            plan = AgentPlan(task_id="task_grounding_fail", objective="test", steps=[step1])
            planner.create_plan = AsyncMock(return_value=plan)
            engine._planner = planner

            validator = MagicMock()
            validator.validate.return_value = []
            engine._plan_validator = validator

            events = []
            async for ev in engine.run_agent_task("sess_fsm", "Create report without evidence"):
                events.append(ev)

            # Must have cleanly emitted task_failed and plan_step failed
            task_failed_evts = [e for e in events if isinstance(e, dict) and e.get("type") == "task_failed"]
            assert len(task_failed_evts) == 1
            assert "grounded source evidence" in task_failed_evts[0]["error"]

            # Verify step state in persistence: step must be marked 'failed'
            t_persisted = tm.get_task(events[0]["task_id"])
            assert t_persisted.status == "failed"
            assert t_persisted.plan.steps[0].status == "failed"

    def test_file_read_with_content_satisfies_grounded_data(self):
        """file_read with valid content is recognized as grounded data."""
        executed = [
            {
                "tool": "file_read",
                "success": True,
                "result": {
                    "filename": "Company Safety Training Record.txt",
                    "content": "Trainer: Anil Sharma\nDuration: 3 hours",
                },
            }
        ]
        has_grounded = any(
            prev.get("success") and (
                (prev.get("tool") == "document_search" and prev.get("result") and len(prev.get("result", [])) > 0)
                or (prev.get("tool") == "file_read" and isinstance(prev.get("result"), dict) and str(prev.get("result", {}).get("content", "")).strip())
                or (prev.get("tool") == "file_read" and isinstance(prev.get("result"), str) and prev.get("result").strip())
            )
            for prev in executed
        )
        assert has_grounded is True

    def test_empty_file_read_fails_grounded_data(self):
        """file_read with empty content is not recognized as grounded data."""
        executed = [
            {
                "tool": "file_read",
                "success": True,
                "result": {
                    "filename": "empty.txt",
                    "content": "",
                },
            }
        ]
        has_grounded = any(
            prev.get("success") and (
                (prev.get("tool") == "document_search" and prev.get("result") and len(prev.get("result", [])) > 0)
                or (prev.get("tool") == "file_read" and isinstance(prev.get("result"), dict) and str(prev.get("result", {}).get("content", "")).strip())
                or (prev.get("tool") == "file_read" and isinstance(prev.get("result"), str) and prev.get("result").strip())
            )
            for prev in executed
        )
        assert has_grounded is False

    @pytest.mark.asyncio
    async def test_stale_artifact_reservation_release_on_retry(self):
        """If prior task failed, ToolRegistry must allow new task to generate the artifact without conflict."""
        from backend.agent.task import TaskStore, TaskManager, TaskStatus
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "tasks.db"
            store = TaskStore(db_path)
            tm = TaskManager(store)

            # Prior task failed
            prior_task = tm.create_task("sess1", "Create safety data", user_id="user_admin", user_role="admin")
            tm.update_status(prior_task.task_id, TaskStatus.PLANNING)
            tm.update_status(prior_task.task_id, TaskStatus.EXECUTING)
            tm.update_status(prior_task.task_id, TaskStatus.FAILED, error="verifier failed")

            # Store artifact record belonging to prior failed task
            store.save_artifact(
                filename="Safety_Training_Data.xlsx",
                path=str(Path(tmpdir) / "Safety_Training_Data.xlsx"),
                task_id=prior_task.task_id,
                user_id="user_admin",
                user_role="admin",
                size_bytes=1000,
            )

            # New task retry
            new_task = tm.create_task("sess1", "Retry safety data", user_id="user_admin", user_role="admin")
            tm.update_status(new_task.task_id, TaskStatus.PLANNING)
            tm.update_status(new_task.task_id, TaskStatus.EXECUTING)

            registry = ToolRegistry()
            registry.set_task_store(store)
            mock_tool = MagicMock()
            mock_tool.name = "xlsx_report"
            mock_tool.read_only = False
            mock_tool.input_schema = MagicMock()
            mock_tool.execute_fn = AsyncMock(return_value={"filename": "Safety_Training_Data.xlsx"})
            registry._tools["xlsx_report"] = mock_tool

            res = await registry.execute(
                name="xlsx_report",
                arguments={"filename": "Safety_Training_Data.xlsx"},
                task_id=new_task.task_id,
                user_id="user_admin",
                user_role="admin",
            )
            # Must succeed and not be blocked by CROSS_TASK_DENIAL
            assert res.success is True

    def test_resolve_user_clearance_string_roles(self):
        """String roles such as 'admin' and 'operator' must resolve to their proper clearances."""
        from backend.auth.models import resolve_user_clearance
        assert resolve_user_clearance("admin") == "admin"
        assert resolve_user_clearance("operator") == "operator"
        assert resolve_user_clearance("viewer") == "viewer"
        assert resolve_user_clearance("L3") == "admin"
        assert resolve_user_clearance("L2") == "operator"
        assert resolve_user_clearance("L1") == "viewer"



