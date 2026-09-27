"""
tests/backend/test_verifier_and_planner_fixes.py
------------------------------------------------
Regression tests for:
1. DOCX artifact verification (narrative documents vs tabular workbooks)
2. Planner artifact pruning (preventing unexpected docx creation on summary requests)
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from backend.agent.planner import AgentPlanner, PlanStep
from backend.models.base import ChatResponse
from backend.tools.artifact_verifier import (
    ArtifactVerifierInput,
    create_artifact_verifier,
)
from backend.tools.docx_create import (
    DocxCreateInput,
    create_docx_create,
)


@pytest.mark.asyncio
async def test_narrative_docx_verification(tmp_path: Path):
    """
    Test that a narrative DOCX (with headings and paragraphs, but NO spreadsheet tables)
    is verified cleanly by artifact_verifier without requiring spreadsheet table columns.
    """
    docx_tool = create_docx_create(tmp_path)
    verifier_tool = create_artifact_verifier(tmp_path)

    # 1. Create a narrative inspection summary Word document
    await docx_tool(DocxCreateInput(
        filename="K-101_Inspection_Summary.docx",
        title="Compressor K-101 Inspection Summary",
        content=(
            "## Visual Inspection\n"
            "Visual inspection of Compressor K-101 completed.\n"
            "## Bearing Vibration\n"
            "Vibration analysis on K-101 showed mild non-critical harmonics.\n"
            "## Lube Oil Analysis\n"
            "Lube oil levels on K-101 are within acceptable operating limits."
        ),
        overwrite=True,
    ))

    # 2. Verify with artifact_verifier
    result = await verifier_tool(ArtifactVerifierInput(
        relative_path="K-101_Inspection_Summary.docx",
        min_paragraph_count=3,
        expected_content=["K-101", "Vibration", "Inspection"],
    ))

    assert result["verified"] is True
    assert result["format"] == "docx"
    assert result["sha256_hash"] is not None
    assert result["paragraph_count"] >= 3


@pytest.mark.asyncio
async def test_tabular_docx_verification(tmp_path: Path):
    """
    Test that a DOCX with structured tables verifies detected columns and rows.
    """
    docx_tool = create_docx_create(tmp_path)
    verifier_tool = create_artifact_verifier(tmp_path)

    await docx_tool(DocxCreateInput(
        filename="K-101_Table_Report.docx",
        title="Equipment Report",
        tables=[{
            "headers": ["Equipment Tag", "Component", "Condition"],
            "rows": [
                ["K-101", "Bearing DE", "Normal"],
                ["K-101", "Thrust Bearing", "Satisfactory"],
            ]
        }],
        overwrite=True,
    ))

    result = await verifier_tool(ArtifactVerifierInput(
        relative_path="K-101_Table_Report.docx",
        min_row_count=2,
        expected_columns=["Equipment Tag", "Component", "Condition"],
        expected_content=["K-101", "Bearing"],
    ))

    assert result["verified"] is True
    assert result["format"] == "docx"
    assert result["row_count"] >= 2


@pytest.mark.asyncio
async def test_planner_prunes_docx_on_summary_request():
    """
    Test that when user only asks for a summary of inspection findings,
    the planner does NOT include docx_create or artifact_verifier in the plan steps.
    """
    planner = AgentPlanner(max_plan_steps=6)

    # Mock provider that proposes docx_create hallucination
    mock_provider = MagicMock()
    mock_llm_json = """
    [
      {"description": "Search for compressor K-101 inspection", "tool_name": "document_search", "arguments": {"query": "compressor K-101"}},
      {"description": "Synthesize findings", "tool_name": null, "arguments": {}},
      {"description": "Create Word document", "tool_name": "docx_create", "arguments": {"filename": "k101_summary.docx"}, "requires_approval": true},
      {"description": "Verify artifact", "tool_name": "artifact_verifier", "arguments": {"filename": "k101_summary.docx"}}
    ]
    """
    mock_provider.chat = AsyncMock(return_value=ChatResponse(content=mock_llm_json, model="test_model", provider="mock"))

    plan = await planner.create_plan(
        task_id="task_test_summary",
        objective="Summarize the findings from compressor K-101 inspection",
        tool_registry=None,
        provider=mock_provider,
        model_name="test_model",
    )

    tool_names = [s.tool_name for s in plan.steps]
    # docx_create and artifact_verifier MUST be pruned because user did not ask to create a document!
    assert "docx_create" not in tool_names
    assert "artifact_verifier" not in tool_names
    assert "document_search" in tool_names


@pytest.mark.asyncio
async def test_planner_keeps_docx_on_explicit_document_request():
    """
    Test that when user explicitly requests generating a Word document,
    the planner preserves docx_create and artifact_verifier.
    """
    planner = AgentPlanner(max_plan_steps=6)

    mock_provider = MagicMock()
    mock_llm_json = """
    [
      {"description": "Search for compressor K-101 inspection", "tool_name": "document_search", "arguments": {"query": "compressor K-101"}},
      {"description": "Create Word document", "tool_name": "docx_create", "arguments": {"filename": "k101_report.docx"}, "requires_approval": true},
      {"description": "Verify artifact", "tool_name": "artifact_verifier", "arguments": {"filename": "k101_report.docx"}}
    ]
    """
    mock_provider.chat = AsyncMock(return_value=ChatResponse(content=mock_llm_json, model="test_model", provider="mock"))

    plan = await planner.create_plan(
        task_id="task_test_gen_doc",
        objective="Generate a Word document summarizing K-101 inspection findings",
        tool_registry=None,
        provider=mock_provider,
        model_name="test_model",
    )

    tool_names = [s.tool_name for s in plan.steps]
    assert "docx_create" in tool_names
    assert "artifact_verifier" in tool_names
