"""
tests/backend/test_tools.py
----------------------------
Comprehensive tests for Phase 4 — Tool Registry, Calculator, File Tools,
Agent Parser, and API endpoints.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.tools.registry import ToolRegistry, ToolDefinition, ToolResult
from backend.tools.calculator import (
    CalculatorInput, safe_calculate, execute_calculator
)
from backend.tools.safety import validate_path_within, sanitize_filename, check_file_size
from backend.tools.file_list import FileListInput, create_file_list
from backend.tools.file_read import FileReadInput, create_file_read
from backend.tools.file_write import FileWriteInput, create_file_write
from backend.tools.document_search import DocumentSearchInput, create_document_search
from backend.agent.engine import AgentEngine


# ===================================================================
# CALCULATOR TESTS
# ===================================================================

class TestCalculator:
    """Test the AST-based safe arithmetic evaluator."""

    def test_addition(self):
        assert safe_calculate("2 + 3") == 5

    def test_subtraction(self):
        assert safe_calculate("10 - 4") == 6

    def test_multiplication(self):
        assert safe_calculate("6 * 7") == 42

    def test_division(self):
        assert safe_calculate("20 / 4") == 5.0

    def test_floor_division(self):
        assert safe_calculate("7 // 2") == 3

    def test_modulo(self):
        assert safe_calculate("10 % 3") == 1

    def test_exponentiation(self):
        assert safe_calculate("2 ** 10") == 1024

    def test_parentheses(self):
        assert safe_calculate("(2 + 3) * 4") == 20

    def test_nested_parentheses(self):
        assert safe_calculate("((2 + 3) * (4 - 1))") == 15

    def test_unary_minus(self):
        assert safe_calculate("-5 + 3") == -2

    def test_unary_plus(self):
        assert safe_calculate("+5") == 5

    def test_float_literal(self):
        assert abs(safe_calculate("3.14 * 2") - 6.28) < 0.001

    def test_complex_expression(self):
        result = safe_calculate("125 * 840 * 1.18")
        assert abs(result - 123900.0) < 0.01

    def test_division_by_zero(self):
        with pytest.raises(ValueError, match="Division by zero"):
            safe_calculate("10 / 0")

    def test_floor_div_by_zero(self):
        with pytest.raises(ValueError, match="Division by zero"):
            safe_calculate("10 // 0")

    def test_modulo_by_zero(self):
        with pytest.raises(ValueError, match="Division by zero"):
            safe_calculate("10 % 0")

    def test_invalid_syntax(self):
        with pytest.raises(ValueError):
            safe_calculate("2 +")

    def test_empty_expression(self):
        with pytest.raises(ValueError):
            safe_calculate("")

    def test_whitespace_only(self):
        with pytest.raises(ValueError):
            safe_calculate("   ")

    # --- Security: Rejected expressions ---

    def test_reject_function_call(self):
        with pytest.raises(ValueError, match="Disallowed"):
            safe_calculate("print(5)")

    def test_reject_import(self):
        with pytest.raises(ValueError):
            safe_calculate("__import__('os')")

    def test_reject_attribute_access(self):
        with pytest.raises(ValueError):
            safe_calculate("os.system('ls')")

    def test_reject_variable(self):
        with pytest.raises(ValueError, match="Disallowed"):
            safe_calculate("x + 5")

    def test_reject_string(self):
        with pytest.raises(ValueError, match="Non-numeric"):
            safe_calculate("'hello'")

    def test_reject_lambda(self):
        with pytest.raises(ValueError):
            safe_calculate("lambda: 5")

    def test_reject_dunder_import(self):
        with pytest.raises(ValueError):
            safe_calculate("__import__('subprocess').call('ls')")

    def test_reject_open(self):
        with pytest.raises(ValueError, match="Disallowed"):
            safe_calculate("open('file.txt')")

    def test_reject_excessive_exponent(self):
        with pytest.raises(ValueError, match="Exponent too large"):
            safe_calculate("2 ** 10000")

    def test_reject_list(self):
        with pytest.raises(ValueError):
            safe_calculate("[1, 2, 3]")

    @pytest.mark.asyncio
    async def test_execute_calculator(self):
        result = await execute_calculator(CalculatorInput(expression="2 + 3"))
        assert result == {"expression": "2 + 3", "result": 5}


# ===================================================================
# SAFETY TESTS
# ===================================================================

class TestSafety:
    """Test central security functions."""

    def test_valid_relative_path(self, tmp_path):
        target = validate_path_within("file.txt", tmp_path)
        assert str(target).startswith(str(tmp_path.resolve()))

    def test_reject_traversal(self, tmp_path):
        with pytest.raises(ValueError, match="traversal"):
            validate_path_within("../../../etc/passwd", tmp_path)

    def test_reject_absolute_path(self, tmp_path):
        with pytest.raises(ValueError):
            validate_path_within("/etc/passwd", tmp_path)

    def test_reject_windows_absolute(self, tmp_path):
        with pytest.raises(ValueError, match="Absolute"):
            validate_path_within("C:\\Windows\\System32\\cmd.exe", tmp_path)

    def test_reject_null_bytes(self, tmp_path):
        with pytest.raises(ValueError, match="null"):
            validate_path_within("file\x00.txt", tmp_path)

    def test_nested_relative(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        target = validate_path_within("sub/file.txt", tmp_path)
        assert str(target).startswith(str(tmp_path.resolve()))

    def test_sanitize_filename_simple(self):
        assert sanitize_filename("report.txt") == "report.txt"

    def test_sanitize_filename_strips_dirs(self):
        name = sanitize_filename("../../etc/passwd")
        assert "/" not in name
        assert "\\" not in name
        assert ".." not in name

    def test_sanitize_filename_removes_unsafe_chars(self):
        name = sanitize_filename('file<>:"|?*.txt')
        assert "<" not in name
        assert ">" not in name

    def test_sanitize_filename_empty(self):
        with pytest.raises(ValueError, match="empty"):
            sanitize_filename("")

    def test_sanitize_filename_null_bytes(self):
        with pytest.raises(ValueError, match="null"):
            sanitize_filename("file\x00.txt")

    def test_check_file_size(self, tmp_path):
        f = tmp_path / "small.txt"
        f.write_text("hello")
        check_file_size(f, 1024)  # Should not raise

    def test_check_file_size_too_large(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 1000)
        with pytest.raises(ValueError, match="too large"):
            check_file_size(f, 100)

    def test_check_file_size_nonexistent(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            check_file_size(tmp_path / "nope.txt", 1024)


# ===================================================================
# TOOL REGISTRY TESTS
# ===================================================================

class TestToolRegistry:
    """Test the ToolRegistry."""

    def _make_registry(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="test_tool",
            description="A test tool",
            input_schema=CalculatorInput,
            execute_fn=execute_calculator,
            category="test",
        ))
        return registry

    def test_register_and_get(self):
        registry = self._make_registry()
        tool = registry.get("test_tool")
        assert tool is not None
        assert tool.name == "test_tool"

    def test_list_tools(self):
        registry = self._make_registry()
        tools = registry.list_tools()
        assert len(tools) == 1

    def test_list_enabled(self):
        registry = self._make_registry()
        assert len(registry.list_enabled_tools()) == 1

    def test_unknown_tool(self):
        registry = self._make_registry()
        assert registry.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        registry = self._make_registry()
        result = await registry.execute("nonexistent", {})
        assert not result.success
        assert "Unknown tool" in result.error

    @pytest.mark.asyncio
    async def test_execute_disabled_tool(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="disabled_tool",
            description="A disabled tool",
            input_schema=CalculatorInput,
            execute_fn=execute_calculator,
            category="test",
            enabled=False,
        ))
        result = await registry.execute("disabled_tool", {"expression": "2+2"})
        assert not result.success
        assert "disabled" in result.error

    @pytest.mark.asyncio
    async def test_execute_invalid_args(self):
        registry = self._make_registry()
        result = await registry.execute("test_tool", {"expression": ""})
        assert not result.success
        assert "Invalid arguments" in result.error

    @pytest.mark.asyncio
    async def test_execute_success(self):
        registry = self._make_registry()
        result = await registry.execute("test_tool", {"expression": "2 + 3"})
        assert result.success
        assert result.result == {"expression": "2 + 3", "result": 5}
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_execute_tool_exception(self):
        async def failing_fn(args):
            raise RuntimeError("Tool crashed")

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="failing",
            description="Fails",
            input_schema=CalculatorInput,
            execute_fn=failing_fn,
            category="test",
        ))
        result = await registry.execute("failing", {"expression": "2+2"})
        assert not result.success
        assert "crashed" in result.error

    def test_tool_schemas_for_llm(self):
        registry = self._make_registry()
        schemas = registry.get_tool_schemas_for_llm()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "test_tool"
        assert "parameters" in schemas[0]

    def test_format_tools_for_prompt(self):
        registry = self._make_registry()
        prompt = registry.format_tools_for_prompt()
        assert "test_tool" in prompt
        assert "expression" in prompt

    def test_unregister(self):
        registry = self._make_registry()
        assert registry.unregister("test_tool") is True
        assert registry.get("test_tool") is None
        assert registry.unregister("test_tool") is False


# ===================================================================
# FILE LIST TOOL TESTS
# ===================================================================

class TestFileList:
    """Test the file_list tool."""

    @pytest.mark.asyncio
    async def test_list_files(self, tmp_path):
        # Create test files
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.csv").write_text("1,2,3")

        fn = create_file_list(tmp_path)
        result = await fn(FileListInput())
        assert len(result) == 2
        names = [f["filename"] for f in result]
        assert "a.txt" in names
        assert "b.csv" in names

    @pytest.mark.asyncio
    async def test_list_empty_dir(self, tmp_path):
        fn = create_file_list(tmp_path)
        result = await fn(FileListInput())
        assert result == []

    @pytest.mark.asyncio
    async def test_list_subdirectory(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "c.txt").write_text("test")

        fn = create_file_list(tmp_path)
        result = await fn(FileListInput(directory="subdir"))
        assert len(result) == 1
        assert result[0]["filename"] == "c.txt"

    @pytest.mark.asyncio
    async def test_list_traversal_rejected(self, tmp_path):
        fn = create_file_list(tmp_path)
        with pytest.raises(ValueError, match="traversal"):
            await fn(FileListInput(directory="../../"))


# ===================================================================
# FILE READ TOOL TESTS
# ===================================================================

class TestFileRead:
    """Test the file_read tool."""

    @pytest.mark.asyncio
    async def test_read_file(self, tmp_path):
        (tmp_path / "test.txt").write_text("Hello World")
        fn = create_file_read(tmp_path)
        result = await fn(FileReadInput(relative_path="test.txt"))
        assert result["content"] == "Hello World"
        assert result["filename"] == "test.txt"

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, tmp_path):
        fn = create_file_read(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            await fn(FileReadInput(relative_path="nope.txt"))

    @pytest.mark.asyncio
    async def test_read_traversal_rejected(self, tmp_path):
        fn = create_file_read(tmp_path)
        with pytest.raises(ValueError, match="traversal"):
            await fn(FileReadInput(relative_path="../../../etc/passwd"))

    @pytest.mark.asyncio
    async def test_read_bad_extension(self, tmp_path):
        (tmp_path / "test.exe").write_bytes(b"\x00\x01\x02")
        fn = create_file_read(tmp_path)
        with pytest.raises(ValueError, match="not supported"):
            await fn(FileReadInput(relative_path="test.exe"))

    @pytest.mark.asyncio
    async def test_read_oversized(self, tmp_path):
        big = tmp_path / "big.txt"
        big.write_text("x" * (2 * 1024 * 1024))  # 2 MB
        fn = create_file_read(tmp_path)
        with pytest.raises(ValueError, match="too large"):
            await fn(FileReadInput(relative_path="big.txt"))


# ===================================================================
# FILE WRITE TOOL TESTS
# ===================================================================

class TestFileWrite:
    """Test the file_write tool."""

    @pytest.mark.asyncio
    async def test_write_file(self, tmp_path):
        fn = create_file_write(tmp_path)
        result = await fn(FileWriteInput(filename="output.txt", content="Hello!"))
        assert result["filename"] == "output.txt"
        assert result["size_bytes"] == 6
        assert (tmp_path / "output.txt").read_text() == "Hello!"

    @pytest.mark.asyncio
    async def test_write_no_overwrite(self, tmp_path):
        (tmp_path / "existing.txt").write_text("original")
        fn = create_file_write(tmp_path)
        result = await fn(FileWriteInput(filename="existing.txt", content="new"))
        # Should have a unique name, not overwrite
        assert result["filename"] != "existing.txt"
        assert (tmp_path / "existing.txt").read_text() == "original"

    @pytest.mark.asyncio
    async def test_write_traversal_sanitized(self, tmp_path):
        """Traversal paths are sanitized — directory components stripped."""
        fn = create_file_write(tmp_path)
        result = await fn(FileWriteInput(filename="../../evil.txt", content="safe"))
        # sanitize_filename strips path components, so the file is created safely
        assert ".." not in result["filename"]
        assert result["filename"] == "evil.txt"

    @pytest.mark.asyncio
    async def test_write_size_limit(self, tmp_path):
        fn = create_file_write(tmp_path)
        with pytest.raises(ValueError, match="too large"):
            await fn(FileWriteInput(filename="big.txt", content="x" * (2 * 1024 * 1024)))

    @pytest.mark.asyncio
    async def test_reject_placeholder_tokens(self, tmp_path):
        fn = create_file_write(tmp_path)
        for bad in ["text", "summary", "placeholder", "TODO", "   test   "]:
            with pytest.raises(ValueError, match="placeholder"):
                await fn(FileWriteInput(filename="out.txt", content=bad))

    @pytest.mark.asyncio
    async def test_reject_template_placeholders(self, tmp_path):
        fn = create_file_write(tmp_path)
        with pytest.raises(ValueError, match="placeholder template"):
            await fn(FileWriteInput(
                filename="out.txt",
                content="Summary of compressor issues found in documents."
            ))
        with pytest.raises(ValueError, match="unfilled template marker"):
            await fn(FileWriteInput(
                filename="out.txt",
                content="[insert findings here]"
            ))


# ===================================================================
# DOCUMENT SEARCH TOOL TESTS
# ===================================================================

class TestDocumentSearch:
    """Test the document_search tool."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        mock_retriever = MagicMock()
        mock_chunk = MagicMock()
        mock_chunk.filename = "test.pdf"
        mock_chunk.chunk_id = "chunk_1"
        mock_chunk.chunk_index = 0
        mock_chunk.page = 1
        mock_chunk.score = 0.123
        mock_chunk.text = "Sample text"
        mock_retriever.retrieve = AsyncMock(return_value=[mock_chunk])

        fn = create_document_search(mock_retriever)
        result = await fn(DocumentSearchInput(query="test query", top_k=3))
        assert len(result) == 1
        assert result[0]["filename"] == "test.pdf"
        assert result[0]["score"] == 0.123

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(return_value=[])

        fn = create_document_search(mock_retriever)
        result = await fn(DocumentSearchInput(query="test"))
        assert result == []


# ===================================================================
# TOOL CALL PARSER TESTS
# ===================================================================

class TestToolCallParser:
    """Test the tool call parser in AgentEngine."""

    def test_no_tool_call(self):
        text = "This is a normal response without any tool calls."
        result = AgentEngine._parse_tool_call(text)
        assert result is None

    def test_valid_tool_call(self):
        text = (
            "I'll calculate that for you.\n\n"
            '<tool_call>\n'
            '{"name": "calculator", "arguments": {"expression": "2 + 3"}}\n'
            '</tool_call>'
        )
        result = AgentEngine._parse_tool_call(text)
        assert result is not None
        assert result["name"] == "calculator"
        assert result["arguments"]["expression"] == "2 + 3"

    def test_malformed_json(self):
        text = '<tool_call>\n{this is not json}\n</tool_call>'
        result = AgentEngine._parse_tool_call(text)
        assert result is None

    def test_missing_name(self):
        text = '<tool_call>\n{"arguments": {"x": 1}}\n</tool_call>'
        result = AgentEngine._parse_tool_call(text)
        assert result is None

    def test_missing_arguments_defaults(self):
        text = '<tool_call>\n{"name": "calculator"}\n</tool_call>'
        result = AgentEngine._parse_tool_call(text)
        assert result is not None
        assert result["arguments"] == {}

    def test_trailing_comma_recovery(self):
        text = '<tool_call>\n{"name": "calculator", "arguments": {"expression": "2+2",}}\n</tool_call>'
        result = AgentEngine._parse_tool_call(text)
        assert result is not None
        assert result["name"] == "calculator"

    def test_extract_pre_tool_text(self):
        text = "I'll help with that.\n\n<tool_call>\n{\"name\":\"calc\"}\n</tool_call>"
        pre = AgentEngine._extract_pre_tool_text(text)
        assert pre.strip() == "I'll help with that."

    def test_extract_pre_tool_text_no_tool(self):
        text = "Just a normal response."
        pre = AgentEngine._extract_pre_tool_text(text)
        assert pre == text


# ===================================================================
# API TESTS
# ===================================================================

class TestToolsAPI:
    """Test the GET /api/tools endpoint."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from backend.main import app

        # Ensure tool_registry exists on app state
        with TestClient(app) as c:
            yield c

    def test_get_tools(self, client):
        resp = client.get("/api/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert "total" in data
        assert data["total"] == 13
        names = [t["name"] for t in data["tools"]]
        expected_names = [
            "document_search",
            "file_list",
            "file_read",
            "calculator",
            "file_write",
            "xlsx_report",
            "docx_create",
            "code_execution",
            "artifact_verifier",
            "knowledge_graph_query",
            "hardware_status",
            "model_scan",
            "security_diagnostics",
        ]
        assert len(names) == 13
        for name in expected_names:
            assert name in names

    def test_tools_have_schema(self, client):
        resp = client.get("/api/tools")
        data = resp.json()
        for tool in data["tools"]:
            assert "input_schema" in tool
            assert "name" in tool
            assert "description" in tool
            assert "category" in tool
            assert "read_only" in tool
            assert "enabled" in tool

    @pytest.mark.asyncio
    async def test_knowledge_graph_execution_through_registry(self):
        """Verify knowledge_graph_query tool executes through ToolRegistry.execute()."""
        from backend.tools.knowledge_graph import KnowledgeGraphQueryInput, create_knowledge_graph_query
        from backend.graph.store import KnowledgeGraphStore
        from backend.graph.service import KnowledgeGraphService
        import tempfile
        from pathlib import Path

        tmp_db = Path(tempfile.mkdtemp()) / "test_kg_exec.db"
        store = KnowledgeGraphStore(db_path=tmp_db)
        store.seed_static_topology()
        service = KnowledgeGraphService(store=store)

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="knowledge_graph_query",
            description="Knowledge Graph tool",
            input_schema=KnowledgeGraphQueryInput,
            execute_fn=create_knowledge_graph_query(service),
        ))

        res = await registry.execute("knowledge_graph_query", {"query": "UNIT_HC04", "max_depth": 1})
        assert res.success is True
        assert res.result["found"] is True
        assert res.result["query"] == "UNIT_HC04"
        assert res.result["target_entity"] == "UNIT_HC04"
        assert res.result["node_count"] >= 1



class TestChatAPIToolsEnabled:
    """Test chat API with tools_enabled parameter."""

    @pytest.fixture
    def client(self):
        from unittest.mock import AsyncMock, MagicMock
        from fastapi.testclient import TestClient
        from backend.main import app

        mock_engine = MagicMock()
        mock_engine.chat = AsyncMock(return_value=("Hello from mock assistant!", []))
        mock_engine.chat_stream_with_tools = AsyncMock()
        mock_engine.chat_stream = AsyncMock()

        with TestClient(app) as c:
            c.app.state.engine = mock_engine
            c.app.state.ollama_ok = True
            yield c

    def test_chat_with_tools_disabled(self, client):
        """When tools_enabled=false, should use plain streaming."""
        resp = client.post("/api/chat", json={
            "message": "Hello",
            "stream": False,
            "tools_enabled": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert "message" in data

    def test_chat_request_accepts_tools_enabled(self, client):
        """Verify the tools_enabled field is accepted without error."""
        resp = client.post("/api/chat", json={
            "message": "What is 2+2?",
            "stream": False,
            "tools_enabled": True,
        })
        assert resp.status_code == 200


class TestToolsConfigRegistration:
    """Test configuration application, overrides, and fail-closed defaults in _register_tools()."""

    def test_register_tools_reads_config_and_applies_overrides(self):
        from backend.main import _register_tools
        from backend.config import settings

        reg = ToolRegistry()
        mock_retriever = MagicMock()
        mock_graph = MagicMock()

        custom_cfg = {
            "document_search": {
                "enabled": True,
                "risk_level": "medium",
                "requires_approval": True,
            },
            "file_read": {
                "enabled": False,
                "risk_level": "low",
                "requires_approval": False,
            },
            "file_write": {
                "enabled": True,
                "risk_level": "high",
                "requires_approval": True,
            },
        }

        _register_tools(reg, mock_retriever, tools_config=custom_cfg, graph_service=mock_graph, cfg=settings)

        doc_tool = reg.get("document_search")
        assert doc_tool is not None
        assert doc_tool.risk_level == "medium"
        assert doc_tool.requires_approval is True
        assert doc_tool.enabled is True

        read_tool = reg.get("file_read")
        assert read_tool is not None
        assert read_tool.risk_level == "low"
        assert read_tool.requires_approval is False
        assert read_tool.enabled is False

    def test_register_tools_uses_safe_defaults_on_invalid_or_missing_config(self):
        from backend.main import _register_tools
        from backend.config import settings

        reg = ToolRegistry()
        mock_retriever = MagicMock()
        mock_graph = MagicMock()

        invalid_cfg = {
            "file_write": {
                "enabled": "not_a_bool",
                "risk_level": "UNKNOWN_RISK",
                "requires_approval": "not_a_bool",
            },
            "calculator": {
                "enabled": True,
                "risk_level": 12345,
                "requires_approval": None,
            },
        }

        _register_tools(reg, mock_retriever, tools_config=invalid_cfg, graph_service=mock_graph, cfg=settings)

        # file_write must fail-safe to high risk and requires_approval=True
        fw_tool = reg.get("file_write")
        assert fw_tool.risk_level == "high"
        assert fw_tool.requires_approval is True
        assert fw_tool.enabled is True

        calc_tool = reg.get("calculator")
        assert calc_tool.risk_level == "low"
        assert calc_tool.requires_approval is False

    def test_mandatory_approval_cannot_be_disabled_by_config(self):
        """
        SEC-02: Enforce that mutating tools (read_only=False with default=True)
        cannot have requires_approval disabled via configuration.
        """
        from backend.main import _register_tools
        from backend.config import settings

        reg = ToolRegistry()
        mock_retriever = MagicMock()
        mock_graph = MagicMock()

        bypass_cfg = {
            "file_write": {"requires_approval": False},
            "docx_create": {"requires_approval": False},
            "xlsx_report": {"requires_approval": False},
            # Read-only tool configured with false remains false
            "document_search": {"requires_approval": False},
            # Optional approval read-only tool configured with true becomes true
            "file_read": {"requires_approval": True},
            # Mutating tool configured with true stays true
            "code_execution": {"requires_approval": True},
        }

        _register_tools(reg, mock_retriever, tools_config=bypass_cfg, graph_service=mock_graph, cfg=settings)

        # Mutating tools must reject/override the false setting
        assert reg.get("file_write").requires_approval is True
        assert reg.get("docx_create").requires_approval is True
        assert reg.get("xlsx_report").requires_approval is True

        # Read-only tool with false remains false
        assert reg.get("document_search").requires_approval is False

        # Configured true on read-only and mutating tool works
        assert reg.get("file_read").requires_approval is True
        assert reg.get("code_execution").requires_approval is True

    def test_malformed_scalar_and_null_config_entries_handled_safely(self):
        """
        DEF-01: Safely handle tool configuration entries that are scalars or null.
        Must not raise AttributeError and must fall back to safe defaults.
        """
        from backend.main import _register_tools
        from backend.config import settings

        reg = ToolRegistry()
        mock_retriever = MagicMock()
        mock_graph = MagicMock()

        malformed_cfg = {
            "file_write": "disabled",
            "docx_create": None,
            "xlsx_report": 123,
            "calculator": ["not", "a", "dict"],
            "document_search": True,
        }

        # Must execute cleanly without AttributeError or uncaught exceptions
        _register_tools(reg, mock_retriever, tools_config=malformed_cfg, graph_service=mock_graph, cfg=settings)

        # Fail-closed defaults must be preserved
        fw = reg.get("file_write")
        assert fw is not None
        assert fw.requires_approval is True
        assert fw.risk_level == "high"
        assert fw.enabled is True

        docx = reg.get("docx_create")
        assert docx.requires_approval is True
        assert docx.risk_level == "high"

        calc = reg.get("calculator")
        assert calc.risk_level == "low"
        assert calc.requires_approval is False

    @pytest.mark.asyncio
    async def test_consistent_registration_when_graph_service_is_none(self):
        """
        REG-01: Verify knowledge_graph_query remains represented in ToolRegistry (10 tools)
        even when graph_service is None. Executing it returns a safe service-unavailable result.
        """
        from backend.main import _register_tools
        from backend.config import settings

        reg = ToolRegistry()
        mock_retriever = MagicMock()

        # Call with graph_service=None
        _register_tools(reg, mock_retriever, tools_config={}, graph_service=None, cfg=settings)

        assert len(reg.list_tools()) == 13
        assert reg.has_tool("knowledge_graph_query")

        kg_tool = reg.get("knowledge_graph_query")
        assert kg_tool is not None
        assert kg_tool.read_only is True
        assert kg_tool.enabled is False

        # Direct execution of unavailable executor returns controlled error without crashing
        res = await kg_tool.execute_fn({"query": "UNIT_HC04"})
        assert res["success"] is False
        assert res["found"] is False
        assert "unavailable" in res["error"].lower()
