import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.config import settings
from backend.agent.memory import ConversationMemory
from backend.rag.retriever import Retriever, RetrievedChunk
from backend.agent.engine import AgentEngine
from backend.agent.planner import is_general_knowledge_query, should_use_planning, AgentPlan, PlanStep, StepStatus


def _make_engine():
    memory = ConversationMemory()
    router = MagicMock()
    return AgentEngine(settings=settings, router=router, memory=memory)


def _make_chunk(doc_id: str, fname: str, text: str, score: float, page: int = 1, idx: int = 0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"{doc_id}_c{idx}",
        document_id=doc_id,
        filename=fname,
        file_type="txt",
        text=text,
        chunk_index=idx,
        score=score,
        page=page,
    )


# ===========================================================================
# ISSUE 1: Final Response Synthesis on Plan Completion
# ===========================================================================

@pytest.mark.asyncio
async def test_agent_synthesizes_response_when_plan_has_no_reasoning_step():
    """Verify that a task with xlsx_report and artifact_verifier generates a deterministic final response."""
    engine = _make_engine()
    executed_step_results = [
        {
            "step_id": "step_1",
            "tool": "xlsx_report",
            "arguments": {
                "filename": "P204_Equipment_Data.xlsx",
                "title": "P-204 Hydrocracker Charge Pump Equipment Data",
                "headers": ["Equipment Tag", "Discharge Pressure", "Status"],
                "rows": [["P-204", "42 bar", "OPERATIONAL"]],
            },
            "success": True,
            "result": "Created P204_Equipment_Data.xlsx",
            "summary": "Generated Excel report with 1 rows",
        },
        {
            "step_id": "step_2",
            "tool": "artifact_verifier",
            "arguments": {
                "relative_path": "P204_Equipment_Data.xlsx",
            },
            "success": True,
            "result": "SHA256 verified",
            "summary": "SHA256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        },
    ]

    response = engine._synthesize_task_completion_response(
        user_request="Create an XLSX containing the P-204 maintenance data",
        executed_step_results=executed_step_results,
    )

    assert "P204_Equipment_Data.xlsx" in response
    assert "Execution Plan Completed" in response
    assert "Cryptographic Verification" in response
    assert "Artifacts" in response


# ===========================================================================
# ISSUE 3: General Knowledge vs Document / Artifact Queries
# ===========================================================================

def test_general_knowledge_classification():
    """Test A: Generic informational question -> no tools / artifacts."""
    generic_query = "What is a centrifugal pump? Explain its purpose, basic working principle, and the main components."
    assert is_general_knowledge_query(generic_query) is True
    assert should_use_planning(generic_query) is False

    cavitation_query = "What is cavitation in pumps?"
    assert is_general_knowledge_query(cavitation_query) is True
    assert should_use_planning(cavitation_query) is False

    heat_exchanger_query = "Explain how a heat exchanger works."
    assert is_general_knowledge_query(heat_exchanger_query) is True
    assert should_use_planning(heat_exchanger_query) is False


def test_document_specific_classification():
    """Test B: Document-specific question -> RAG allowed, not general knowledge."""
    p204_query = "Explain P-204"
    assert is_general_knowledge_query(p204_query) is False

    report_query = "Summarize the uploaded pump maintenance report"
    assert is_general_knowledge_query(report_query) is False

    indexed_query = "According to the indexed documents, what maintenance information is available for equipment P-204?"
    assert is_general_knowledge_query(indexed_query) is False


def test_explicit_artifact_request_classification():
    """Test C: Explicit artifact request -> artifact tools allowed, planning allowed."""
    xlsx_query = "Create an XLSX containing the P-204 maintenance data"
    assert is_general_knowledge_query(xlsx_query) is False
    assert should_use_planning(xlsx_query) is True

    docx_query = "Generate a docx report on P-204 inspection findings"
    assert is_general_knowledge_query(docx_query) is False
    assert should_use_planning(docx_query) is True


# ===========================================================================
# ISSUE 4: Equipment Tag Isolation & Grounding
# ===========================================================================

@pytest.mark.asyncio
async def test_equipment_query_isolates_matching_equipment_chunks():
    """
    Verify that when user asks about P-204, chunks about P-101 are strictly excluded,
    and only P-204 chunks are retained.
    """
    engine = _make_engine()
    mock_doc_service = MagicMock()
    mock_doc_service.has_documents.return_value = True

    # Chunk 1 is about P-204 (target)
    c_p204 = _make_chunk("doc_p204", "pump_p204_maintenance.md", "Equipment P-204: Boiler feed pump cavitation on Stage 1 impeller.", 0.2)
    # Chunk 2 is about P-101 (unrelated equipment from demo_test.pdf)
    c_p101 = _make_chunk("doc_p101", "demo_test.pdf", "Pump P-101 operates at 85C and requires inspection every 500 hours.", 0.25)
    # Chunk 3 is about K-101
    c_k101 = _make_chunk("doc_k101", "compressor_k101.md", "Compressor K-101 vibration overhaul.", 0.3)

    mock_doc_service.retrieve = AsyncMock(return_value=[c_p204, c_p101, c_k101])
    mock_doc_service._retriever = Retriever(MagicMock(), MagicMock(), max_distance=0.385)
    engine.set_doc_service(mock_doc_service)

    sources = await engine._retrieve_context(
        "According to the indexed documents, what maintenance information is available for equipment P-204?"
    )

    # Must contain ONLY the P-204 chunk; P-101 and K-101 must be filtered out
    assert len(sources) == 1
    assert "P-204" in sources[0].text
    assert "P-101" not in sources[0].text


def test_build_messages_injects_strict_equipment_grounding_directive():
    """Verify that equipment queries receive strict isolation directives in system message."""
    engine = _make_engine()
    c_p204 = _make_chunk("doc_p204", "pump_p204_maintenance.md", "P-204 cavitation repair.", 0.2)

    messages = engine._build_messages(
        session_id="test_sess",
        user_message="According to the indexed documents, what maintenance information is available for equipment P-204?",
        sources=[c_p204],
    )

    rag_msg = [m for m in messages if "STRICT GROUNDING DIRECTIVES FOR P-204" in m.content]
    assert len(rag_msg) == 1
    content = rag_msg[0].content
    assert "STRICT ISOLATION" in content
    assert "P-101" in content
    assert "The indexed documents do not provide enough information to establish this." in content


# ===========================================================================
# ISSUE 5: Evidence Deduplication
# ===========================================================================

@pytest.mark.asyncio
async def test_evidence_bounded_deduplication_caps_same_doc_to_two():
    """
    Verify that multiple chunks from the same document (e.g. equipment_recurring_issues_summary.md)
    are bounded to at most 2 in evidence.
    """
    engine = _make_engine()
    mock_doc_service = MagicMock()
    mock_doc_service.has_documents.return_value = True

    # 4 distinct chunks from the SAME file
    c1 = _make_chunk("doc_summary", "equipment_recurring_issues_summary.md", "Section 1: P-204 overview.", 0.2, idx=0)
    c2 = _make_chunk("doc_summary", "equipment_recurring_issues_summary.md", "Section 2: P-204 root cause analysis.", 0.22, idx=1)
    c3 = _make_chunk("doc_summary", "equipment_recurring_issues_summary.md", "Section 3: P-204 parts replaced.", 0.24, idx=2)
    c4 = _make_chunk("doc_summary", "equipment_recurring_issues_summary.md", "Section 4: P-204 recommendations.", 0.26, idx=3)

    mock_doc_service.retrieve = AsyncMock(return_value=[c1, c2, c3, c4])
    mock_doc_service._retriever = Retriever(MagicMock(), MagicMock(), max_distance=0.385)
    engine.set_doc_service(mock_doc_service)

    sources = await engine._retrieve_context("P-204 maintenance")
    # Must be capped at at most 2 chunks for this document
    assert len(sources) <= 2


# ===========================================================================
# ISSUE 2: PDF Table Row/Column Relationship Preservation
# ===========================================================================

def test_pdf_table_heuristic_preserves_row_column_alignment_with_blank_cells():
    """Verify that the positional table heuristic preserves row/column alignment when middle cells are blank."""
    from backend.rag.ingest import DocumentParser
    raw_text = (
        "Operating Data:\n"
        "Tag        Flow        Pressure    Status\n"
        "P-204      120 m3/h    42 bar      ACTIVE\n"
        "P-101      85 m3/h                 STANDBY\n"
    )
    result = DocumentParser._apply_table_heuristic(raw_text)
    assert "| Tag | Flow | Pressure | Status |" in result
    assert "| --- | --- | --- | --- |" in result
    assert "| P-204 | 120 m3/h | 42 bar | ACTIVE |" in result
    # Column 3 (Pressure) is blank, Status remains in column 4
    assert "| P-101 | 85 m3/h |  | STANDBY |" in result


def test_polite_general_knowledge_query_classification():
    """Verify that polite phrasing (please, can you) still correctly classifies as general knowledge."""
    q1 = "Please explain how a centrifugal pump works."
    assert is_general_knowledge_query(q1) is True
    assert should_use_planning(q1) is False

    q2 = "Can you tell me about cavitation in pumps?"
    assert is_general_knowledge_query(q2) is True
    assert should_use_planning(q2) is False

