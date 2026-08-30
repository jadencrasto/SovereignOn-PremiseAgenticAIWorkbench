"""
eval/report_eval.py
-------------------
Phase 8: End-to-End Industrial Report Generation Workflow Evaluation.

Demonstrates and evaluates the full sovereign AI industrial workflow:
  1. Document ingestion & RAG retrieval
  2. Agent planning & multi-step execution
  3. Human-in-the-loop approval gating for high-risk write
  4. Sandboxed atomic write of structured report
  5. Audit log attribution to authenticated operator
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

from backend.agent.approval import ApprovalManager
from backend.agent.planner import AgentPlan, PlanStep, StepStatus
from backend.agent.task import TaskManager, TaskStatus
from backend.agent.task_store import TaskStore
from backend.audit.logger import AuditLogger
from backend.auth.models import UserRole
from backend.rag.service import DocumentService
from backend.tools.calculator import CalculatorInput, execute_calculator
from backend.tools.document_search import DocumentSearchInput, create_document_search
from backend.tools.file_write import FileWriteInput, create_file_write
from backend.tools.registry import ToolDefinition, ToolRegistry
from eval.common import (
    DEMO_DATA_DIR,
    EvalStatus,
    EvaluationSuiteResult,
    TestCaseResult,
    save_evaluation_results,
)

logger = logging.getLogger("eval.report")


class ReportWorkflowEvaluator:
    """Evaluates the primary SIH industrial maintenance analysis & report workflow."""

    def __init__(self) -> None:
        pass

    async def run(self) -> EvaluationSuiteResult:
        t0 = time.monotonic()
        test_cases: List[TestCaseResult] = []
        summary_metrics: Dict[str, Any] = {}

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            db_path = tmp_path / "report_workflow.db"
            audit_db = tmp_path / "audit.db"
            sandbox_dir = tmp_path / "sandbox"
            sandbox_dir.mkdir(parents=True, exist_ok=True)

            task_store = TaskStore(db_path=db_path)
            audit_logger = AuditLogger(db_path=audit_db)
            task_mgr = TaskManager(store=task_store)
            approval_mgr = ApprovalManager(store=task_store)
            approval_mgr.set_audit_logger(audit_logger)

            # Setup tools
            mock_retriever = MagicMock()
            mock_chunks = [
                MagicMock(
                    text="Compressor K-101: 9.4 mm/s vibration due to Stage 3 polymer unbalance.",
                    filename="compressor_k101_inspection.md", score=0.92, chunk_index=0, page=1,
                ),
                MagicMock(
                    text="Pump P-204: 88.4°C DE bearing overheating caused by 65% clogged suction strainer.",
                    filename="pump_p204_maintenance.md", score=0.89, chunk_index=0, page=1,
                ),
                MagicMock(
                    text="Exchanger E-302: 2.45 bar shell delta-P due to asphaltene fouling.",
                    filename="heat_exchanger_e302_report.md", score=0.85, chunk_index=0, page=1,
                ),
            ]
            mock_retriever.retrieve = AsyncMock(return_value=mock_chunks)

            tool_reg = ToolRegistry()
            tool_reg.set_audit_logger(audit_logger)
            tool_reg.register(ToolDefinition(
                name="document_search",
                description="Search documents",
                input_schema=DocumentSearchInput,
                execute_fn=create_document_search(mock_retriever),
                category="Information Retrieval",
                read_only=True,
            ))
            tool_reg.register(ToolDefinition(
                name="file_write",
                description="Write file",
                input_schema=FileWriteInput,
                execute_fn=create_file_write(sandbox_dir),
                category="File Operations",
                read_only=False,
                requires_approval=True,
                risk_level="high",
            ))
            tool_reg.register(ToolDefinition(
                name="calculator",
                description="Calculator",
                input_schema=CalculatorInput,
                execute_fn=execute_calculator,
                category="Utilities",
                read_only=True,
            ))

            # -------------------------------------------------------------
            # Stage 1: Operator Initiates Multi-Step Task
            # -------------------------------------------------------------
            t_case = time.monotonic()
            try:
                user_req = (
                    "Analyze maintenance reports for K-101, P-204, and E-302. "
                    "Synthesize equipment failure modes and save a formal summary report to the workspace."
                )
                task = task_mgr.create_task("session_industrial_01", user_req)
                task_mgr.update_status(task.task_id, TaskStatus.PLANNING)

                plan = AgentPlan(
                    task_id=task.task_id,
                    objective="Synthesize equipment reliability report and save to workspace",
                    steps=[
                        PlanStep(
                            id="step_01",
                            description="Retrieve maintenance records for K-101, P-204, and E-302",
                            tool_name="document_search",
                            arguments={"query": "equipment failure K-101 P-204 E-302", "top_k": 3},
                            requires_approval=False,
                        ),
                        PlanStep(
                            id="step_02",
                            description="Calculate total affected equipment count",
                            tool_name="calculator",
                            arguments={"expression": "1 + 1 + 1"},
                            requires_approval=False,
                        ),
                        PlanStep(
                            id="step_03",
                            description="Write synthesized reliability report to sandbox",
                            tool_name="file_write",
                            arguments={
                                "filename": "equipment_reliability_report_2026.md",
                                "content": (
                                    "# SYNTHETIC EQUIPMENT RELIABILITY & MAINTENANCE REPORT (2026)\n"
                                    f"**Generated for Task:** {task.task_id}\n"
                                    f"**Timestamp:** 2026-03-05T12:00:00Z\n"
                                    f"**Author / Operator:** operator_verma\n\n"
                                    "## 1. Executive Summary\n"
                                    "Analysis across FCCU, Utilities, and CDU indicates critical rotating and static asset vulnerabilities.\n\n"
                                    "## 2. Key Findings & Citations\n"
                                    "- **K-101 Compressor:** 9.4 mm/s RMS vibration caused by Stage 3 polymer unbalance (Source: `compressor_k101_inspection.md`).\n"
                                    "- **P-204 Pump:** 88.4°C bearing overheat due to 65% suction strainer clogging (Source: `pump_p204_maintenance.md`).\n"
                                    "- **E-302 Exchanger:** 2.45 bar shell delta-P caused by heavy asphaltene fouling (Source: `heat_exchanger_e302_report.md`).\n\n"
                                    "## 3. Reliability Mitigations\n"
                                    "1. Dynamic balancing of K-101 rotor completed.\n"
                                    "2. Replaced P-204 impeller with 13Cr martensitic stainless steel.\n"
                                    "3. High-pressure hydro-milling (1,400 bar) on E-302 bundle with 14 tubes plugged.\n"
                                ),
                            },
                            requires_approval=True,  # High risk mutating write
                        ),
                    ],
                )
                task_mgr.set_plan(task.task_id, plan)
                task_mgr.update_status(task.task_id, TaskStatus.EXECUTING)

                test_cases.append(TestCaseResult(
                    test_id="RPT-01",
                    name="Task Planning & DAG Construction",
                    category="workflow_orchestration",
                    status=EvalStatus.PASS,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    details=f"Task {task.task_id} generated 3-step execution plan with approval requirement on step 3",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="RPT-01",
                    name="Task Planning & DAG Construction",
                    category="workflow_orchestration",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

            # -------------------------------------------------------------
            # Stage 2: Step 1 & Step 2 Execution (Safe Steps)
            # -------------------------------------------------------------
            t_case = time.monotonic()
            try:
                # Execute step 1: document search
                task_mgr.update_step_status(task.task_id, "step_01", StepStatus.running.value)
                res1 = await tool_reg.execute(
                    "document_search",
                    plan.steps[0].arguments,
                    session_id=task.session_id,
                    user_role=UserRole.OPERATOR.value,
                )
                assert res1.success is True
                task_mgr.update_step_status(task.task_id, "step_01", StepStatus.completed.value, result="Retrieved 3 records")

                # Execute step 2: calculation
                task_mgr.update_step_status(task.task_id, "step_02", StepStatus.running.value)
                res2 = await tool_reg.execute(
                    "calculator",
                    plan.steps[1].arguments,
                    session_id=task.session_id,
                    user_role=UserRole.OPERATOR.value,
                )
                assert res2.success is True
                task_mgr.update_step_status(task.task_id, "step_02", StepStatus.completed.value, result=str(res2.result))

                test_cases.append(TestCaseResult(
                    test_id="RPT-02",
                    name="Automated Read Step Execution",
                    category="workflow_orchestration",
                    status=EvalStatus.PASS,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    details="RAG context retrieval and arithmetic calculation steps completed cleanly",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="RPT-02",
                    name="Automated Read Step Execution",
                    category="workflow_orchestration",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

            # -------------------------------------------------------------
            # Stage 3: Step 3 Approval Gate & Operator Grant
            # -------------------------------------------------------------
            t_case = time.monotonic()
            try:
                task_mgr.update_step_status(task.task_id, "step_03", StepStatus.awaiting_approval.value)
                task_mgr.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)

                # Request approval
                appr = approval_mgr.request_approval(
                    task_id=task.task_id,
                    step_id="step_03",
                    tool_name="file_write",
                    arguments=plan.steps[2].arguments,
                    risk_level="high",
                    reason="Writing final reliability report to workspace",
                )
                assert appr.status == "pending"

                # Operator approves
                approval_mgr.approve(appr.approval_id)
                persisted_appr = approval_mgr._store.get_approval(appr.approval_id)
                assert persisted_appr["status"] == "approved"

                # Verify approval binding
                binding_ok = approval_mgr.verify_approval_for_execution(
                    approval_id=appr.approval_id,
                    task_id=task.task_id,
                    step_id="step_03",
                    tool_name="file_write",
                    arguments=plan.steps[2].arguments,
                    tool_registry=tool_reg,
                )
                assert binding_ok is True

                task_mgr.update_step_status(task.task_id, "step_03", StepStatus.approved.value)
                task_mgr.update_status(task.task_id, TaskStatus.EXECUTING)

                test_cases.append(TestCaseResult(
                    test_id="RPT-03",
                    name="Human Approval Binding & Verification",
                    category="human_in_the_loop",
                    status=EvalStatus.PASS,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    details="Approval request created, approved by operator, and SHA-256 bound arguments verified",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="RPT-03",
                    name="Human Approval Binding & Verification",
                    category="human_in_the_loop",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

            # -------------------------------------------------------------
            # Stage 4: Atomic Sandboxed Report Write & File Verification
            # -------------------------------------------------------------
            t_case = time.monotonic()
            try:
                task_mgr.update_step_status(task.task_id, "step_03", StepStatus.running.value)
                res3 = await tool_reg.execute(
                    "file_write",
                    plan.steps[2].arguments,
                    session_id=task.session_id,
                    user_role=UserRole.OPERATOR.value,
                )
                assert res3.success is True
                task_mgr.update_step_status(task.task_id, "step_03", StepStatus.completed.value, result="Report saved")
                task_mgr.update_status(task.task_id, TaskStatus.COMPLETED, result="Report generation workflow completed")

                # Verify written report file
                report_file = sandbox_dir / "equipment_reliability_report_2026.md"
                assert report_file.exists()
                report_text = report_file.read_text(encoding="utf-8")

                assert "SYNTHETIC EQUIPMENT RELIABILITY" in report_text
                assert "K-101 Compressor" in report_text
                assert "P-204 Pump" in report_text
                assert "E-302 Exchanger" in report_text
                assert "compressor_k101_inspection.md" in report_text
                assert task.task_id in report_text

                test_cases.append(TestCaseResult(
                    test_id="RPT-04",
                    name="Sandboxed Report Output & Citation Verification",
                    category="data_integrity",
                    status=EvalStatus.PASS,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    details="Generated Markdown report contains structured sections, findings, and verified document citations",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="RPT-04",
                    name="Sandboxed Report Output & Citation Verification",
                    category="data_integrity",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

            # -------------------------------------------------------------
            # Stage 5: Audit Trail Verification
            # -------------------------------------------------------------
            t_case = time.monotonic()
            try:
                audit_res = audit_logger.query_events(limit=50)
                audit_events = audit_res["events"]
                tool_events = [e for e in audit_events if "tool" in e["event_type"]]
                approval_events = [e for e in audit_events if "approval" in e["event_type"]]

                assert len(tool_events) >= 3, "Expected audit events for all tool executions"
                assert len(approval_events) >= 1, "Expected audit event for approval lifecycle"

                test_cases.append(TestCaseResult(
                    test_id="RPT-05",
                    name="Audit Trail Complete Attribution",
                    category="compliance_audit",
                    status=EvalStatus.PASS,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    details=f"Verified {len(tool_events)} tool execution events and {len(approval_events)} approval events in SQLite audit log",
                ))
            except Exception as exc:
                test_cases.append(TestCaseResult(
                    test_id="RPT-05",
                    name="Audit Trail Complete Attribution",
                    category="compliance_audit",
                    status=EvalStatus.FAIL,
                    duration_ms=(time.monotonic() - t_case) * 1000,
                    error=str(exc),
                ))

        duration = time.monotonic() - t0
        passed_count = sum(1 for tc in test_cases if tc.status == EvalStatus.PASS)
        failed_count = sum(1 for tc in test_cases if tc.status == EvalStatus.FAIL)

        suite = EvaluationSuiteResult(
            suite_name="Industrial Report Generation Workflow Benchmark",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            environment="Air-Gapped Sovereign Workflow",
            total_cases=len(test_cases),
            passed=passed_count,
            failed=failed_count,
            environment_unavailable=0,
            duration_seconds=duration,
            summary_metrics={
                "Workflow_Stages_Completed": passed_count,
                "Total_Workflow_Stages": len(test_cases),
                "Workflow_Success_Rate": 1.0 if passed_count == len(test_cases) else 0.0,
            },
            test_cases=test_cases,
        )

        save_evaluation_results(suite, "report_eval")
        return suite


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    evaluator = ReportWorkflowEvaluator()
    res = asyncio.run(evaluator.run())
    print(f"Report Workflow Evaluation: {res.passed}/{res.total_cases} Passed")
