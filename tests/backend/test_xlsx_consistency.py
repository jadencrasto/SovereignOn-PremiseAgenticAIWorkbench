"""
tests/backend/test_xlsx_consistency.py
---------------------------------------
Targeted verification tests for:
1. Intended semantic XLSX column structure (exact headers: Equipment ID, Maintenance Findings,
   Operating Observations, Recommended Actions) even when LLM produces 5-column / split variations.
2. Prevention of fake Python/openpyxl artifact-generation code and narrative.
3. Removal of contradictory post-completion proceed language ("Would you like me to proceed with any further steps?").
4. Deterministic task completion response with actual tool pipeline description.
5. Rigorous disk workbook verification via artifact_verifier validating grounded P-204 content,
   exact headers, non-empty cells, absence of boilerplate / 'Not stated' / placeholder text,
   and equipment tag isolation (no P-101/K-101 contamination).
6. Preservation of generic XLSX behavior when no explicit schema is requested.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import openpyxl
import pytest

from backend.agent.engine import AgentEngine
from backend.agent.memory import ConversationMemory
from backend.config import settings
from backend.tools.xlsx_report import create_xlsx_report, XlsxReportInput
from backend.tools.artifact_verifier import create_artifact_verifier, ArtifactVerifierInput


def _make_engine():
    memory = ConversationMemory()
    router = MagicMock()
    return AgentEngine(settings=settings, router=router, memory=memory)


# ===========================================================================
# 1. XLSX Column / Schema Consistency & Normalization
# ===========================================================================

@pytest.mark.asyncio
async def test_xlsx_schema_normalizes_split_columns_to_exact_semantic_headers():
    """
    When the LLM hallucinates a 5-column breakdown ('ID', 'Description', 'Finding',
    'Observation', 'Recommended Action'), _synthesize_xlsx_data normalizes them back
    to the exact 4 semantic headers requested by the user:
    ['Equipment ID', 'Maintenance Findings', 'Operating Observations', 'Recommended Actions'].
    """
    engine = _make_engine()
    doc_path = Path("data/demo/pump_p204_maintenance.md")
    doc_text = doc_path.read_text(encoding="utf-8")

    mock_provider = MagicMock()
    # LLM returns 5-column format with split ID and Description
    mock_provider.chat = AsyncMock(return_value=MagicMock(content=json.dumps({
        "headers": ["ID", "Description", "Finding", "Observation", "Recommended Action"],
        "rows": [
            [
                "P-204",
                "Boiler Feed Water Multi-stage Centrifugal Pump Train B",
                "DE radial bearing high-temperature alarm (peak 88.4°C vs 80°C limit); suction strainer clogged",
                "Audible cavitation noise; discharge pressure dropped from 68 bar to 54 bar",
                "Installed OEM 13Cr martensitic stainless steel impeller; daily delta-P logging",
            ]
        ]
    })))

    res = await engine._synthesize_xlsx_data(
        user_request=(
            "Create a real P-204 XLSX maintenance data report using the indexed documents. "
            "Include the relevant equipment ID, maintenance findings, operating observations, "
            "and recommended actions in a structured spreadsheet."
        ),
        filename="P204_Report.xlsx",
        step_description="Generate spreadsheet",
        executed_step_results=[
            {"tool": "document_search", "result": [{"filename": "pump_p204_maintenance.md", "text": doc_text}]}
        ],
        sources=[],
        provider=mock_provider,
        model_name="test-model",
    )

    # EXACT required headers
    assert res["headers"] == [
        "Equipment ID",
        "Maintenance Findings",
        "Operating Observations",
        "Recommended Actions",
    ]

    # Row 0: ID and Description were merged into Equipment ID
    row = res["rows"][0]
    assert len(row) == 4
    assert "P-204" in row[0]
    assert "Centrifugal Pump" in row[0]
    assert "bearing" in row[1].lower() or "strainer" in row[1].lower()
    assert "cavitation" in row[2].lower() or "54 bar" in row[2].lower() or "88.4" in row[2].lower()
    assert "impeller" in row[3].lower() or "logging" in row[3].lower()


@pytest.mark.asyncio
async def test_xlsx_generic_schema_preserved_when_no_explicit_columns():
    """
    When the user request does NOT request an explicit schema, the generic
    XLSX behavior is preserved without forcing the 4 maintenance headers.
    """
    engine = _make_engine()
    mock_provider = MagicMock()
    mock_provider.chat = AsyncMock(return_value=MagicMock(content=json.dumps({
        "headers": ["Project Name", "Budget", "Quarter", "Status"],
        "rows": [["Sovereign Workbench", "$120,000", "Q1", "COMPLIANT"]]
    })))

    res = await engine._synthesize_xlsx_data(
        user_request="Create an executive budget overview spreadsheet.",
        filename="Budget.xlsx",
        step_description="Generate budget report",
        executed_step_results=[],
        sources=[],
        provider=mock_provider,
        model_name="test-model",
    )

    assert res["headers"] == ["Project Name", "Budget", "Quarter", "Status"]
    assert res["rows"][0] == ["Sovereign Workbench", "$120,000", "Q1", "COMPLIANT"]


# ===========================================================================
# 2. Preventing Fake Python / openpyxl Code & Narrow Sanitization
# ===========================================================================

def test_clean_reasoning_response_removes_fake_openpyxl_code():
    """
    _clean_reasoning_response removes fake openpyxl script generation code blocks
    without altering legitimate technical facts and descriptions.
    """
    engine = _make_engine()
    raw_response = (
        "Based on document DOC-MAINT-2026-P204, Boiler Feed Water Pump P-204 experienced "
        "a peak DE radial bearing temperature of 88.4°C exceeding the 80°C alarm threshold.\n\n"
        "Here is the Python script to create the Excel workbook:\n"
        "```python\n"
        "import openpyxl\n"
        "wb = openpyxl.Workbook()\n"
        "ws = wb.active\n"
        "ws.append(['ID', 'Description', 'Finding', 'Observation', 'Recommended Action'])\n"
        "wb.save('data/sandbox/P204_Report.xlsx')\n"
        "```\n\n"
        "The Stage 1 closed impeller was replaced with an OEM 13Cr stainless steel component."
    )

    cleaned = engine._clean_reasoning_response(raw_response)
    assert "import openpyxl" not in cleaned
    assert "wb = openpyxl.Workbook()" not in cleaned
    assert "Here is the Python script" not in cleaned
    # Legitimate engineering content MUST be preserved
    assert "88.4°C" in cleaned
    assert "80°C alarm threshold" in cleaned
    assert "Stage 1 closed impeller" in cleaned
    assert "OEM 13Cr stainless steel" in cleaned


# ===========================================================================
# 3. Removing Contradictory Post-Completion Proceed Language
# ===========================================================================

def test_clean_reasoning_response_strips_contradictory_proceed_questions():
    """
    _clean_reasoning_response strips trailing 'Would you like me to proceed...' questions.
    """
    engine = _make_engine()
    text = (
        "The P-204 equipment inspection data has been compiled and verified against the knowledge base.\n\n"
        "Would you like me to proceed with any further steps?"
    )
    cleaned = engine._clean_reasoning_response(text)
    assert "Would you like me to proceed" not in cleaned
    assert "P-204 equipment inspection data has been compiled" in cleaned

    text_alt = (
        "Artifact generation complete.\n"
        "Please let me know if you would like me to proceed with generating another report."
    )
    cleaned_alt = engine._clean_reasoning_response(text_alt)
    assert "proceed with generating another report" not in cleaned_alt
    assert "Artifact generation complete." in cleaned_alt


# ===========================================================================
# 4. Task Completion Response Describes Actual Pipeline
# ===========================================================================

def test_synthesize_task_completion_response_describes_actual_pipeline():
    """
    _synthesize_task_completion_response describes the genuine workbench pipeline
    (searched indexed documents, synthesized data, generated via xlsx_report,
    verified via artifact_verifier) and states task completed without proceed prompts.
    """
    engine = _make_engine()
    executed_steps = [
        {"tool": "document_search", "summary": "Found P-204 document"},
        {"tool": "reasoning", "summary": "Synthesized grounded evidence"},
        {
            "tool": "xlsx_report",
            "arguments": {
                "filename": "P204_Equipment_Data.xlsx",
                "title": "P-204 Hydrocracker Charge Pump Equipment Data",
                "headers": ["Equipment ID", "Maintenance Findings", "Operating Observations", "Recommended Actions"],
                "rows": [["P-204", "Bearing alarm", "Cavitation", "Replaced impeller"]],
            },
            "result": {"filename": "P204_Equipment_Data.xlsx", "row_count": 1, "column_count": 4},
        },
        {
            "tool": "artifact_verifier",
            "arguments": {"relative_path": "P204_Equipment_Data.xlsx"},
            "result": {"filename": "P204_Equipment_Data.xlsx", "row_count": 1, "column_count": 4, "verified": True},
            "summary": "1 row(s), 4 column(s) verified",
        },
    ]

    resp = engine._synthesize_task_completion_response(
        user_request="Create P-204 report",
        executed_step_results=executed_steps,
    )

    # Required structure
    assert "### Execution Plan Completed" in resp
    assert "#### Execution Pipeline" in resp
    assert "Document Search" in resp
    assert "Data Synthesis" in resp
    assert "xlsx_report" in resp
    assert "artifact_verifier" in resp
    assert "#### Generated Artifacts" in resp
    assert "P204_Equipment_Data.xlsx" in resp
    assert "1 data row(s) across 4 column(s)" in resp
    assert "#### Verification & Integrity" in resp
    assert "Cryptographic Verification" in resp
    assert "Task completed" in resp
    # No fake code or proceed language
    assert "import openpyxl" not in resp
    assert "Would you like me to proceed" not in resp


# ===========================================================================
# 5. Full End-to-End P-204 Grounding, Disk Workbook & Equipment Isolation
# ===========================================================================

@pytest.mark.asyncio
async def test_p204_actual_disk_workbook_exact_headers_and_grounded_content(tmp_path: Path):
    """
    End-to-end verification of the actual generated XLSX workbook on disk:
    1. EXACT headers: Equipment ID, Maintenance Findings, Operating Observations, Recommended Actions
    2. Data row contains genuine grounded P-204 evidence in every cell
    3. NO 'Not stated in retrieved document.' in any cell
    4. NO generic invented maintenance boilerplate
    5. NO cross-equipment contamination (P-101, K-101, etc.)
    6. NO placeholder text
    7. Artifact verifier validates the physical workbook on disk.
    """
    engine = _make_engine()
    doc_path = Path("data/demo/pump_p204_maintenance.md")
    doc_text = doc_path.read_text(encoding="utf-8")

    execute_xlsx = create_xlsx_report(tmp_path)
    verify_fn = create_artifact_verifier(tmp_path)

    # 1. Synthesize XLSX data
    synthesized = await engine._synthesize_xlsx_data(
        user_request=(
            "Create a real P-204 XLSX maintenance data report using the indexed documents. "
            "Include the relevant equipment ID, maintenance findings, operating observations, "
            "and recommended actions in a structured spreadsheet. "
            "After generating it, verify the workbook and complete the task."
        ),
        filename="P204_Equipment_Data.xlsx",
        step_description="Synthesize structured P-204 maintenance data",
        executed_step_results=[
            {
                "tool": "document_search",
                "description": "Search P-204 equipment records",
                "result": [{"filename": "pump_p204_maintenance.md", "text": doc_text}],
            }
        ],
        sources=[],
        provider=MagicMock(chat=AsyncMock(side_effect=RuntimeError("Test fallback to grounded evidence"))),
        model_name="test-model",
    )

    # Assert EXACT headers (not merely 4 arbitrary columns)
    assert synthesized["headers"] == [
        "Equipment ID",
        "Maintenance Findings",
        "Operating Observations",
        "Recommended Actions",
    ]

    # 2. Write out actual physical XLSX workbook to disk via execute_xlsx
    report_res = await execute_xlsx(XlsxReportInput(
        filename="P204_Equipment_Data.xlsx",
        title="P-204 Boiler Feed Water Pump Maintenance Report",
        headers=synthesized["headers"],
        rows=synthesized["rows"],
    ))
    assert report_res["row_count"] == 1
    assert report_res["column_count"] == 4

    # 3. Verify actual disk file exists and inspect OOXML structure directly
    file_path = tmp_path / "P204_Equipment_Data.xlsx"
    assert file_path.exists()

    wb = openpyxl.load_workbook(file_path, data_only=True)
    ws = wb.active
    assert ws is not None

    # Verify headers in Row 4
    disk_headers = [ws.cell(row=4, column=c).value for c in range(1, 5)]
    assert disk_headers == [
        "Equipment ID",
        "Maintenance Findings",
        "Operating Observations",
        "Recommended Actions",
    ]

    # Verify data in Row 5
    disk_row = [str(ws.cell(row=5, column=c).value or "").strip() for c in range(1, 5)]
    wb.close()

    equip_cell = disk_row[0]
    finding_cell = disk_row[1]
    obs_cell = disk_row[2]
    action_cell = disk_row[3]

    # Cell 0: Equipment ID
    assert "P-204" in equip_cell
    assert "Centrifugal Pump" in equip_cell or "Boiler Feed Water" in equip_cell

    # Cell 1: Maintenance Findings
    assert any(term in finding_cell.lower() for term in ("strainer", "clog", "bearing", "alarm", "pitting", "scale", "spalling"))

    # Cell 2: Operating Observations
    assert any(term in obs_cell.lower() for term in ("88.4", "cavitation", "pressure", "bar", "temperature", "vibration"))

    # Cell 3: Recommended Actions
    assert any(term in action_cell.lower() for term in ("impeller", "13cr", "bearing", "seal", "strainer", "logging", "monitoring"))

    # Universal cell checks
    for idx, cell_content in enumerate(disk_row):
        lower = cell_content.lower()
        col_name = disk_headers[idx]

        # No 'Not stated'
        assert "not stated in retrieved document" not in lower, f"Cell in column '{col_name}' had 'Not stated'"

        # No boilerplate
        for bp in ("standard cleaning", "routine maintenance", "revealed no abnormalities", "within acceptable ranges"):
            assert bp not in lower, f"Cell in column '{col_name}' contained boilerplate '{bp}'"

        # No placeholders
        for ph in ("placeholder", "todo", "n/a", "sample text"):
            assert ph not in lower, f"Cell in column '{col_name}' contained placeholder '{ph}'"

        # Equipment isolation: NO P-101 or K-101
        assert "p-101" not in lower and "p101" not in lower, f"Cell in column '{col_name}' contained P-101"
        assert "k-101" not in lower and "k101" not in lower, f"Cell in column '{col_name}' contained K-101"

    # 4. Rigorous artifact verifier on the physical disk workbook
    v_res = await verify_fn(ArtifactVerifierInput(
        relative_path="P204_Equipment_Data.xlsx",
        expected_columns=["Equipment ID", "Maintenance Findings", "Operating Observations", "Recommended Actions"],
        expected_content=["P-204", "strainer", "temperature", "impeller"],
        min_row_count=1,
    ))
    assert v_res["verified"] is True
    assert v_res["row_count"] == 1
    assert v_res["column_count"] == 4
    assert v_res["detected_headers"] == [
        "Equipment ID",
        "Maintenance Findings",
        "Operating Observations",
        "Recommended Actions",
    ]
