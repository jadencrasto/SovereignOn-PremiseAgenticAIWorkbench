"""
eval/tool_eval.py
-----------------
Phase 8: Tool Success, Authorization & Latency Evaluation Suite.

Evaluates only currently implemented tools:
  - document_search
  - file_list
  - file_read
  - calculator
  - file_write
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

from backend.auth.models import UserRole
from backend.config import Settings
from backend.tools.calculator import CalculatorInput, execute_calculator
from backend.tools.document_search import DocumentSearchInput, create_document_search
from backend.tools.file_list import FileListInput, create_file_list
from backend.tools.file_read import FileReadInput, create_file_read
from backend.tools.file_write import FileWriteInput, create_file_write
from backend.tools.registry import ToolDefinition, ToolRegistry, ToolResult
from eval.common import (
    EvalStatus,
    EvaluationSuiteResult,
    TestCaseResult,
    save_evaluation_results,
)

logger = logging.getLogger("eval.tool")


class ToolEvaluator:
    """Evaluates execution, RBAC gating, argument validation, and latency for all local tools."""

    def __init__(self) -> None:
        pass

    async def run(self) -> EvaluationSuiteResult:
        t0 = time.monotonic()
        test_cases: List[TestCaseResult] = []
        latencies_ms: List[float] = []

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            sandbox_dir = tmp_path / "sandbox"
            sandbox_dir.mkdir(parents=True, exist_ok=True)

            # Create test files in sandbox
            (sandbox_dir / "sample.txt").write_text("Sovereign AI Sandbox Sample File", encoding="utf-8")
            (sandbox_dir / "subfolder").mkdir(exist_ok=True)
            (sandbox_dir / "subfolder" / "nested.md").write_text("# Nested Header", encoding="utf-8")

            # Mock retriever for deterministic document_search evaluation
            mock_retriever = MagicMock()
            mock_chunk = MagicMock()
            mock_chunk.text = "Compressor K-101 overhaul inspection findings"
            mock_chunk.filename = "compressor_k101_inspection.md"
            mock_chunk.score = 0.88
            mock_chunk.chunk_index = 0
            mock_chunk.page = 1
            mock_retriever.retrieve = AsyncMock(return_value=[mock_chunk])

            # Register standard tools
            reg = ToolRegistry()
            reg.register(ToolDefinition(
                name="file_list",
                description="List files",
                input_schema=FileListInput,
                execute_fn=create_file_list(sandbox_dir),
                category="File Operations",
                read_only=True,
            ))
            reg.register(ToolDefinition(
                name="file_read",
                description="Read file",
                input_schema=FileReadInput,
                execute_fn=create_file_read(sandbox_dir),
                category="File Operations",
                read_only=True,
            ))
            reg.register(ToolDefinition(
                name="file_write",
                description="Write file",
                input_schema=FileWriteInput,
                execute_fn=create_file_write(sandbox_dir),
                category="File Operations",
                read_only=False,
                requires_approval=True,
                risk_level="high",
            ))
            reg.register(ToolDefinition(
                name="document_search",
                description="Search documents",
                input_schema=DocumentSearchInput,
                execute_fn=create_document_search(mock_retriever),
                category="Information Retrieval",
                read_only=True,
            ))
            reg.register(ToolDefinition(
                name="calculator",
                description="Perform mathematical calculations",
                input_schema=CalculatorInput,
                execute_fn=execute_calculator,
                category="Utilities",
                read_only=True,
                risk_level="low",
            ))

            # ---------------------------------------------------------
            # 1. Calculator: Valid execution
            # ---------------------------------------------------------
            t_case = time.monotonic()
            try:
                res = await reg.execute("calculator", {"expression": "(45 * 12) + 180 / 4"}, user_role=UserRole.OPERATOR.value)
                case_lat = (time.monotonic() - t_case) * 1000
                latencies_ms.append(case_lat)
                assert res.success is True
                assert res.result["result"] == 585.0
                test_cases.append(TestCaseResult(
                    test_id="TOOL-01",
                    name="Calculator: Complex Arithmetic",
                    category="execution",
                    status=EvalStatus.PASS,
                    duration_ms=case_lat,
                    details=f"Result={res.result['result']}",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="TOOL-01",
                    name="Calculator: Complex Arithmetic",
                    category="execution",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

            # ---------------------------------------------------------
            # 2. Calculator: Division by zero handling
            # ---------------------------------------------------------
            t_case = time.monotonic()
            try:
                res = await reg.execute("calculator", {"expression": "100 / 0"}, user_role=UserRole.OPERATOR.value)
                case_lat = (time.monotonic() - t_case) * 1000
                latencies_ms.append(case_lat)
                assert res.success is False
                assert "division by zero" in res.error.lower()
                test_cases.append(TestCaseResult(
                    test_id="TOOL-02",
                    name="Calculator: Division by Zero Safety",
                    category="error_handling",
                    status=EvalStatus.PASS,
                    duration_ms=case_lat,
                    details=f"Clean error returned: {res.error}",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="TOOL-02",
                    name="Calculator: Division by Zero Safety",
                    category="error_handling",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

            # ---------------------------------------------------------
            # 3. File List: Valid directory traversal in sandbox
            # ---------------------------------------------------------
            t_case = time.monotonic()
            try:
                res = await reg.execute("file_list", {}, user_role=UserRole.OPERATOR.value)
                case_lat = (time.monotonic() - t_case) * 1000
                latencies_ms.append(case_lat)
                assert res.success is True
                assert any(f["filename"] == "sample.txt" for f in res.result)
                test_cases.append(TestCaseResult(
                    test_id="TOOL-03",
                    name="File List: Sandbox Listing",
                    category="execution",
                    status=EvalStatus.PASS,
                    duration_ms=case_lat,
                    details=f"Listed {len(res.result)} items in sandbox root",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="TOOL-03",
                    name="File List: Sandbox Listing",
                    category="execution",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

            # ---------------------------------------------------------
            # 4. File Read: Valid read within sandbox
            # ---------------------------------------------------------
            t_case = time.monotonic()
            try:
                res = await reg.execute("file_read", {"relative_path": "sample.txt"}, user_role=UserRole.OPERATOR.value)
                case_lat = (time.monotonic() - t_case) * 1000
                latencies_ms.append(case_lat)
                assert res.success is True
                assert "Sovereign AI" in res.result["content"]
                test_cases.append(TestCaseResult(
                    test_id="TOOL-04",
                    name="File Read: Sandbox File Access",
                    category="execution",
                    status=EvalStatus.PASS,
                    duration_ms=case_lat,
                    details=f"Read {len(res.result['content'])} bytes",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="TOOL-04",
                    name="File Read: Sandbox File Access",
                    category="execution",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

            # ---------------------------------------------------------
            # 5. File Write: Safe Atomic Write
            # ---------------------------------------------------------
            t_case = time.monotonic()
            try:
                res = await reg.execute(
                    "file_write",
                    {"filename": "summary.md", "content": "# Generated Maintenance Summary"},
                    user_role=UserRole.OPERATOR.value,
                )
                case_lat = (time.monotonic() - t_case) * 1000
                latencies_ms.append(case_lat)
                assert res.success is True
                assert (sandbox_dir / "summary.md").exists()
                test_cases.append(TestCaseResult(
                    test_id="TOOL-05",
                    name="File Write: Atomic Sandbox Write",
                    category="execution",
                    status=EvalStatus.PASS,
                    duration_ms=case_lat,
                    details="Atomic write committed in sandbox workspace",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="TOOL-05",
                    name="File Write: Atomic Sandbox Write",
                    category="execution",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

            # ---------------------------------------------------------
            # 6. Document Search: Semantic Retrieval
            # ---------------------------------------------------------
            t_case = time.monotonic()
            try:
                res = await reg.execute(
                    "document_search",
                    {"query": "K-101 vibration", "top_k": 3},
                    user_role=UserRole.OPERATOR.value,
                )
                case_lat = (time.monotonic() - t_case) * 1000
                latencies_ms.append(case_lat)
                assert res.success is True
                assert len(res.result) >= 1
                test_cases.append(TestCaseResult(
                    test_id="TOOL-06",
                    name="Document Search: Semantic Search",
                    category="execution",
                    status=EvalStatus.PASS,
                    duration_ms=case_lat,
                    details=f"Retrieved {len(res.result)} chunks",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="TOOL-06",
                    name="Document Search: Semantic Search",
                    category="execution",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

            # ---------------------------------------------------------
            # 7. Authorization Denial: Viewer attempting mutating write
            # ---------------------------------------------------------
            t_case = time.monotonic()
            try:
                res = await reg.execute(
                    "file_write",
                    {"filename": "unauthorized.txt", "content": "blocked"},
                    user_role=UserRole.VIEWER.value,
                )
                case_lat = (time.monotonic() - t_case) * 1000
                latencies_ms.append(case_lat)
                assert res.success is False
                assert "Permission denied" in res.error
                test_cases.append(TestCaseResult(
                    test_id="TOOL-07",
                    name="RBAC: Viewer Write Rejection",
                    category="authorization",
                    status=EvalStatus.PASS,
                    duration_ms=case_lat,
                    details="Viewer role blocked at ToolRegistry boundary",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="TOOL-07",
                    name="RBAC: Viewer Write Rejection",
                    category="authorization",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

        duration = time.monotonic() - t0
        passed_count = sum(1 for tc in test_cases if tc.status == EvalStatus.PASS)
        failed_count = sum(1 for tc in test_cases if tc.status == EvalStatus.FAIL)
        avg_lat = sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0

        suite = EvaluationSuiteResult(
            suite_name="Tool Execution, Authorization & Latency Benchmark",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            environment="Air-Gapped Sandbox Environment",
            total_cases=len(test_cases),
            passed=passed_count,
            failed=failed_count,
            environment_unavailable=0,
            duration_seconds=duration,
            summary_metrics={
                "Tools_Evaluated": 5,
                "Passed_Tool_Operations": passed_count,
                "Average_Tool_Latency_ms": round(avg_lat, 2),
                "Success_Rate_Percent": round((passed_count / len(test_cases) * 100) if test_cases else 0.0, 1),
            },
            test_cases=test_cases,
        )

        save_evaluation_results(suite, "tool_eval")
        return suite


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    evaluator = ToolEvaluator()
    res = asyncio.run(evaluator.run())
    print(f"Tool Evaluation: {res.passed}/{res.total_cases} Passed")
