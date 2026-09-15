"""
tests/backend/test_xlsx_regression_cluster.py
---------------------------------------------
Targeted verification tests for the Phase A final regression cluster:
1. NLP approval/rejection routing in chat endpoint
2. Robust XLSX evidence extraction for chunks without markdown headers
3. Generic boilerplate detection and rejection during XLSX synthesis
4. Exact manual prompt execution (synthesis -> xlsx_report -> artifact_verifier)
5. Strict artifact verification preventing "Not stated" or empty findings workbooks
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import openpyxl
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.agent.engine import AgentEngine
from backend.agent.memory import ConversationMemory
from backend.agent.task import TaskStatus
from backend.config import settings
from backend.tools.xlsx_report import create_xlsx_report, XlsxReportInput
from backend.tools.artifact_verifier import create_artifact_verifier, ArtifactVerifierInput


def _make_engine():
    memory = ConversationMemory()
    router = MagicMock()
    return AgentEngine(settings=settings, router=router, memory=memory)


# ===========================================================================
# 1. Approval Routing via Chat Endpoint
# ===========================================================================

def test_chat_pending_approval_yes_approve_resumes_task():
    """When a task is awaiting approval, chatting 'yes' or 'approve' resumes the task."""
    with TestClient(app) as client:
        engine = client.app.state.engine
        session_id = "test_sess_approve"

        # Create a pending task in task manager with an active approval
        task = engine._task_manager.create_task(
            session_id=session_id,
            user_request="Create P-204 report",
        )
        engine._task_manager.update_status(task.task_id, TaskStatus.PLANNING)
        engine._task_manager.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)
        engine._approval_manager.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="xlsx_report",
            arguments={"filename": "test.xlsx"},
            risk_level="high",
            reason="Generate report",
        )

        # Mock resume_agent_task to yield resumption events
        async def mock_resume(*args, **kwargs):
            yield {"type": "task_resumed", "task_id": task.task_id}
            yield {"type": "plan_step", "task_id": task.task_id, "step_id": "step_1", "status": "running"}
            yield "Resuming report generation."

        with patch.object(engine, "resume_agent_task", side_effect=mock_resume):
            resp = client.post(
                "/api/chat",
                json={"message": "yes, please proceed", "session_id": session_id, "stream": True},
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            body = resp.text
            assert "task_resumed" in body or "Resuming report generation" in body


def test_chat_pending_approval_reject_cancels_task():
    """When a task is awaiting approval, chatting 'no' or 'reject' cancels the task."""
    with TestClient(app) as client:
        engine = client.app.state.engine
        session_id = "test_sess_reject"

        task = engine._task_manager.create_task(
            session_id=session_id,
            user_request="Create P-204 report",
        )
        engine._task_manager.update_status(task.task_id, TaskStatus.PLANNING)
        from backend.agent.planner import AgentPlan, PlanStep
        plan = AgentPlan(
            task_id=task.task_id,
            objective="Create P-204 report",
            steps=[
                PlanStep(
                    id="step_1",
                    step_number=1,
                    tool_name="xlsx_report",
                    description="Create spreadsheet",
                    arguments={"filename": "test.xlsx"},
                    requires_approval=True,
                    status="awaiting_approval",
                )
            ]
        )
        engine._task_manager.set_plan(task.task_id, plan)
        engine._task_manager.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)
        engine._approval_manager.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="xlsx_report",
            arguments={"filename": "test.xlsx"},
            risk_level="high",
            reason="Generate report",
        )

        resp = client.post(
            "/api/chat",
            json={"message": "no, cancel it", "session_id": session_id, "stream": True},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "task_cancelled" in body or "approval_rejected" in body

        # Check task state is cancelled
        updated_task = engine._task_manager.get_task(task.task_id)
        assert updated_task.status == TaskStatus.CANCELLED


def test_chat_no_pending_approval_normal_chat():
    """When no task is awaiting approval, 'yes' goes through regular chat."""
    with TestClient(app) as client:
        engine = client.app.state.engine
        mock_provider = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "Certainly! How can I assist you further?"
        mock_provider.chat = AsyncMock(return_value=mock_resp)
        engine._router.get_provider_for_model = MagicMock(return_value=(mock_provider, "test-model"))

        resp = client.post(
            "/api/chat",
            json={"message": "yes", "session_id": "fresh_session_123", "stream": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "Certainly" in data["message"]["content"]


# ===========================================================================
# 2. Robust XLSX Evidence Extraction Without Markdown Headers
# ===========================================================================

def test_extract_evidence_without_markdown_headers():
    """
    Evidence extraction must succeed even when retrieved text has NO markdown '#' headers
    (e.g., plain-text chunks, PDF extractions, or flattened document sections).
    """
    engine = _make_engine()
    # Plain text without any '#' headers
    plain_context = (
        "Document ID: DOC-MAINT-2026-P204\n"
        "Date of Maintenance: 2026-03-02\n"
        "Equipment Tag: P-204 (Boiler Feed Water Multi-stage Centrifugal Pump Train B)\n"
        "Boiler Feed Water Pump P-204 was taken offline following high temperature alarms on the DE radial bearing "
        "(recorded peak temperature: 88.4°C, Alarm limit: 80°C, Trip limit: 90°C) combined with audible high-frequency "
        "cavitation noise and intermittent discharge pressure drops from 68 bar to 54 bar.\n"
        "Failure mechanisms:\n"
        "Suction strainer S-204 was found 65% clogged with magnetite scale causing Net Positive Suction Head starvation.\n"
        "Impeller Stage 1 suction eye exhibited severe honeycomb pitting erosion across 40% of the blade edges.\n"
        "DE cylindrical roller bearing inner ring raceway showed severe micro-spalling and brown lacquer discoloration from thermal oxidation.\n"
        "Corrective actions executed:\n"
        "Replaced Stage 1 closed impeller with OEM 13Cr martensitic stainless steel impeller.\n"
        "Installed new paired angular contact thrust bearings SKF 7318 BECBM and cylindrical roller radial bearing.\n"
        "Fitted new cartridge mechanical seal John Crane Type 8648VRS.\n"
        "Cleaned and pressure tested 40-mesh stainless steel basket strainer.\n"
        "Preventative maintenance:\n"
        "Implement daily delta-P logging across suction strainer S-204.\n"
        "Perform ultrasonic bearing acoustic monitoring every 14 days.\n"
        "Post overhaul telemetry:\n"
        "Suction Pressure: 4.6 bar\n"
        "Discharge Pressure: 68.2 bar at rated flow of 185 m³/h.\n"
        "DE Bearing Temperature: 58.5°C steady state after 12 hours run.\n"
    )

    # 1. Equipment Tag
    tag = engine._extract_evidence_for_column("Equipment ID", plain_context)
    assert tag is not None
    assert "P-204" in tag

    # 2. Maintenance Findings
    findings = engine._extract_evidence_for_column("Maintenance Findings", plain_context)
    assert findings is not None
    assert "not stated" not in findings.lower()
    assert any(term in findings.lower() for term in ("clogged", "scale", "pitting", "erosion", "bearing", "spalling"))

    # 3. Operating Observations
    observations = engine._extract_evidence_for_column("Operating Observations", plain_context)
    assert observations is not None
    assert "not stated" not in observations.lower()
    assert any(term in observations.lower() for term in ("temperature", "cavitation", "pressure", "bar", "88.4"))

    # 4. Recommended Actions
    actions = engine._extract_evidence_for_column("Recommended Actions", plain_context)
    assert actions is not None
    assert "not stated" not in actions.lower()
    assert any(term in actions.lower() for term in ("impeller", "bearing", "seal", "strainer", "logging", "monitoring"))


# ===========================================================================
# 3. Generic Boilerplate Rejection in XLSX Synthesis
# ===========================================================================

@pytest.mark.asyncio
async def test_synthesize_xlsx_data_rejects_generic_boilerplate():
    """
    If the LLM returns generic boilerplate (e.g. 'Standard cleaning and lubrication procedures',
    'Routine maintenance schedule'), _synthesize_xlsx_data() detects it and replaces it with
    real grounded evidence from context.
    """
    engine = _make_engine()
    doc_path = Path("data/demo/pump_p204_maintenance.md")
    doc_text = doc_path.read_text(encoding="utf-8")

    mock_provider = MagicMock()
    # LLM emits boilerplate instead of real findings
    mock_provider.chat = AsyncMock(return_value=MagicMock(content=json.dumps({
        "headers": ["Equipment ID", "Maintenance Findings", "Operating Observations", "Recommended Actions"],
        "rows": [
            [
                "P-204 (Boiler Feed Water Multi-stage Centrifugal Pump Train B)",
                "Standard cleaning and lubrication procedures were performed.",
                "Pressure and temperature checks were within acceptable ranges.",
                "Routine maintenance schedule should be maintained.",
            ]
        ]
    })))

    res = await engine._synthesize_xlsx_data(
        user_request="Create P-204 maintenance report",
        filename="P204_Report.xlsx",
        step_description="Generate spreadsheet",
        executed_step_results=[
            {"tool": "document_search", "result": [{"filename": "pump_p204_maintenance.md", "text": doc_text}]}
        ],
        sources=[],
        provider=mock_provider,
        model_name="test-model",
    )

    row = res["rows"][0]
    findings = row[1]
    observations = row[2]
    actions = row[3]

    # Boilerplate must have been detected and replaced
    assert "standard cleaning" not in findings.lower()
    assert any(k in findings.lower() for k in ("clog", "bearing", "alarm", "pitting", "scale", "spalling"))

    assert "within acceptable ranges" not in observations.lower()
    assert any(k in observations.lower() for k in ("bar", "temperature", "pressure", "cavitation", "88.4"))

    assert "routine maintenance schedule" not in actions.lower()
    assert any(k in actions.lower() for k in ("impeller", "bearing", "seal", "strainer", "logging"))


# ===========================================================================
# 4. Strict Artifact Verification (Detects 'Not stated' and Validates Content)
# ===========================================================================

@pytest.mark.asyncio
async def test_artifact_verifier_rejects_all_not_stated_findings(tmp_path: Path):
    """
    A workbook containing 'Not stated in retrieved document.' across all findings,
    observations, and actions MUST FAIL verification.
    """
    execute_xlsx = create_xlsx_report(tmp_path)
    verify_fn = create_artifact_verifier(tmp_path)

    await execute_xlsx(XlsxReportInput(
        filename="p204_not_stated.xlsx",
        title="P-204 Maintenance Report",
        headers=["Equipment ID", "Maintenance Findings", "Operating Observations", "Recommended Actions"],
        rows=[
            [
                "P-204 (Boiler Feed Water Multi-stage Centrifugal Pump Train B)",
                "Not stated in retrieved document.",
                "Not stated in retrieved document.",
                "Not stated in retrieved document.",
            ]
        ],
    ))

    with pytest.raises(ValueError, match="contains no substantive evidence in maintenance data columns"):
        await verify_fn(ArtifactVerifierInput(
            relative_path="p204_not_stated.xlsx",
            expected_columns=["Equipment ID", "Maintenance Findings"],
            min_row_count=1,
        ))


@pytest.mark.asyncio
async def test_artifact_verifier_validates_real_grounded_workbook(tmp_path: Path):
    """
    A populated workbook with genuine P-204 findings and observations MUST PASS verification,
    and expected_content validation must verify all expected tokens.
    """
    execute_xlsx = create_xlsx_report(tmp_path)
    verify_fn = create_artifact_verifier(tmp_path)

    await execute_xlsx(XlsxReportInput(
        filename="p204_grounded.xlsx",
        title="P-204 Maintenance Inspection Report",
        headers=["Equipment ID", "Maintenance Findings", "Operating Observations", "Recommended Actions"],
        rows=[
            [
                "P-204 (Boiler Feed Water Multi-stage Centrifugal Pump Train B)",
                "Suction strainer S-204 65% clogged with magnetite scale; Stage 1 impeller severe pitting",
                "Peak bearing temperature 88.4°C (alarm limit 80°C); Discharge pressure drop to 54 bar",
                "Replaced Stage 1 impeller with OEM 13Cr stainless steel; Daily delta-P logging",
            ]
        ],
    ))

    res = await verify_fn(ArtifactVerifierInput(
        relative_path="p204_grounded.xlsx",
        expected_columns=["Equipment ID", "Maintenance Findings", "Operating Observations", "Recommended Actions"],
        expected_content=["P-204", "strainer", "temperature", "impeller"],
        min_row_count=1,
    ))
    assert res["verified"] is True
    assert res["row_count"] == 1
    assert "all_cell_texts" in res
    assert len(res["all_cell_texts"]) == 4


# ===========================================================================
# 5. Exact Manual Prompt End-to-End Pipeline
# ===========================================================================

@pytest.mark.asyncio
async def test_exact_manual_prompt_pipeline(tmp_path: Path):
    """
    Verifies the entire pipeline for the exact user prompt:
    'Create a real P-204 XLSX maintenance data report using the indexed documents.
    Include the relevant equipment ID, maintenance findings, operating observations,
    and recommended actions in a structured spreadsheet.
    After generating it, verify the workbook and complete the task.'
    """
    engine = _make_engine()
    doc_path = Path("data/demo/pump_p204_maintenance.md")
    doc_text = doc_path.read_text(encoding="utf-8")

    execute_xlsx = create_xlsx_report(tmp_path)
    verify_fn = create_artifact_verifier(tmp_path)

    manual_prompt = (
        "Create a real P-204 XLSX maintenance data report using the indexed documents. "
        "Include the relevant equipment ID, maintenance findings, operating observations, "
        "and recommended actions in a structured spreadsheet. "
        "After generating it, verify the workbook and complete the task."
    )

    # 1. Synthesize XLSX data from retrieved document
    synthesized = await engine._synthesize_xlsx_data(
        user_request=manual_prompt,
        filename="P204_Maintenance_Data_Report.xlsx",
        step_description="Synthesize structured maintenance data spreadsheet",
        executed_step_results=[
            {
                "tool": "document_search",
                "description": "Search P-204 maintenance findings and recommendations",
                "result": [{"filename": "pump_p204_maintenance.md", "text": doc_text}],
            }
        ],
        sources=[],
        provider=MagicMock(chat=AsyncMock(side_effect=RuntimeError("Fallback to evidence extraction"))),
        model_name="test-model",
    )

    assert "headers" in synthesized
    assert "rows" in synthesized
    headers = synthesized["headers"]
    row = synthesized["rows"][0]

    # Every single requested column must contain substantive P-204 facts
    for idx, col in enumerate(headers):
        val = str(row[idx])
        assert "not stated in retrieved document" not in val.lower(), f"Column '{col}' had 'Not stated'"
        assert len(val) > 5

    # 2. Generate actual XLSX file
    report_res = await execute_xlsx(XlsxReportInput(
        filename="P204_Maintenance_Data_Report.xlsx",
        title="P-204 Boiler Feed Water Pump Maintenance Report",
        headers=headers,
        rows=[row],
    ))
    assert report_res["row_count"] == 1

    # 3. Verify artifact with artifact_verifier
    v_res = await verify_fn(ArtifactVerifierInput(
        relative_path="P204_Maintenance_Data_Report.xlsx",
        expected_columns=["Equipment ID", "Maintenance Findings", "Operating Observations", "Recommended Actions"],
        expected_content=["P-204", "bearing", "temperature"],
        min_row_count=1,
    ))
    assert v_res["verified"] is True
    assert v_res["row_count"] == 1
