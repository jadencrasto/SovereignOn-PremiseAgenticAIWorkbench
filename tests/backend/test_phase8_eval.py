"""
tests/backend/test_phase8_eval.py
---------------------------------
Unit and integration tests for Phase 8 Evaluation Suite.
"""

import pytest
import json
from pathlib import Path

from eval.common import (
    EvalStatus,
    EvaluationSuiteResult,
    TestCaseResult,
    save_evaluation_results,
    DATA_DIR,
    DEMO_DATA_DIR,
)
from eval.agent_reliability_eval import AgentReliabilityEvaluator
from eval.security_eval import SecurityEvaluator
from eval.tool_eval import ToolEvaluator
from eval.performance_eval import PerformanceEvaluator
from eval.baseline_eval import BaselineEvaluator
from eval.multimodal_eval import MultimodalEvaluator
from eval.report_eval import ReportWorkflowEvaluator


class TestPhase8EvaluationHarness:
    """Test evaluation harness suites and result persistence."""

    def test_demo_dataset_files_exist(self):
        demo_files = [
            "README.md",
            "compressor_k101_inspection.md",
            "pump_p204_maintenance.md",
            "heat_exchanger_e302_report.md",
            "valve_v401_failure_analysis.md",
            "pipeline_corrosion_survey.md",
            "equipment_recurring_issues_summary.md",
            "vibration_log_2026.csv",
            "pump_impeller_inspection.png",
        ]
        for f in demo_files:
            p = DEMO_DATA_DIR / f
            assert p.exists(), f"Demo file missing: {f}"

    def test_qa_dataset_valid_structure(self):
        qa_file = DATA_DIR / "qa_set.json"
        assert qa_file.exists()
        items = json.loads(qa_file.read_text(encoding="utf-8"))
        assert len(items) >= 10
        for item in items:
            assert "id" in item
            assert "question" in item
            assert "expected_facts" in item
            assert "relevant_documents" in item
            assert "category" in item

    @pytest.mark.asyncio
    async def test_agent_reliability_evaluator(self):
        evaluator = AgentReliabilityEvaluator()
        result = await evaluator.run()
        assert isinstance(result, EvaluationSuiteResult)
        assert result.total_cases >= 7
        assert result.failed == 0
        assert result.passed == result.total_cases

    @pytest.mark.asyncio
    async def test_security_evaluator(self):
        evaluator = SecurityEvaluator()
        result = await evaluator.run()
        assert isinstance(result, EvaluationSuiteResult)
        assert result.total_cases >= 10
        assert result.failed == 0
        assert result.passed == result.total_cases

    @pytest.mark.asyncio
    async def test_tool_evaluator(self):
        evaluator = ToolEvaluator()
        result = await evaluator.run()
        assert isinstance(result, EvaluationSuiteResult)
        assert result.total_cases >= 7
        assert result.failed == 0
        assert result.passed == result.total_cases

    @pytest.mark.asyncio
    async def test_performance_evaluator(self):
        evaluator = PerformanceEvaluator(iterations=3)
        result = await evaluator.run()
        assert isinstance(result, EvaluationSuiteResult)
        assert result.total_cases >= 4
        assert result.failed == 0

    @pytest.mark.asyncio
    async def test_baseline_evaluator(self):
        evaluator = BaselineEvaluator()
        result = await evaluator.run()
        assert isinstance(result, EvaluationSuiteResult)
        assert result.total_cases >= 2
        assert result.failed == 0

    @pytest.mark.asyncio
    async def test_multimodal_evaluator(self):
        evaluator = MultimodalEvaluator()
        result = await evaluator.run()
        assert isinstance(result, EvaluationSuiteResult)
        assert result.total_cases >= 3
        assert result.failed == 0

    @pytest.mark.asyncio
    async def test_report_evaluator(self):
        evaluator = ReportWorkflowEvaluator()
        result = await evaluator.run()
        assert isinstance(result, EvaluationSuiteResult)
        assert result.total_cases == 5
        assert result.failed == 0
        assert result.passed == 5
