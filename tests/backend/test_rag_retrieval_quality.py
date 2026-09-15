import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.config import settings
from backend.agent.memory import ConversationMemory
from backend.rag.retriever import Retriever, RetrievedChunk
from backend.tools.document_search import create_document_search, DocumentSearchInput
from backend.agent.engine import AgentEngine


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


def test_relevant_content_passes_threshold():
    retriever = Retriever(MagicMock(), MagicMock(), max_distance=0.385)
    assert retriever.is_chunk_relevant(0.1) is True
    assert retriever.is_chunk_relevant(0.385) is True


def test_irrelevant_content_fails_threshold():
    retriever = Retriever(MagicMock(), MagicMock(), max_distance=0.385)
    assert retriever.is_chunk_relevant(0.386) is False
    assert retriever.is_chunk_relevant(0.615) is False
    assert retriever.is_chunk_relevant(0.85) is False


def test_threshold_boundary():
    retriever = Retriever(MagicMock(), MagicMock(), max_distance=0.385)
    assert retriever.is_chunk_relevant(0.385000) is True
    assert retriever.is_chunk_relevant(0.385001) is False


@pytest.mark.asyncio
async def test_general_knowledge_query_skips_rag():
    engine = _make_engine()
    mock_doc_service = MagicMock()
    mock_doc_service.has_documents.return_value = True
    mock_doc_service.retrieve = AsyncMock()
    engine.set_doc_service(mock_doc_service)

    sources = await engine._retrieve_context("what is a centrifugal pump?")
    assert sources == []
    mock_doc_service.retrieve.assert_not_called()


@pytest.mark.asyncio
async def test_equipment_query_uses_rag():
    engine = _make_engine()
    mock_doc_service = MagicMock()
    mock_doc_service.has_documents.return_value = True
    mock_chunk = _make_chunk("doc1", "p204.txt", "P-204 vibration data", 0.2)
    mock_doc_service.retrieve = AsyncMock(return_value=[mock_chunk])
    mock_doc_service._retriever = Retriever(MagicMock(), MagicMock(), max_distance=0.385)
    engine.set_doc_service(mock_doc_service)

    sources = await engine._retrieve_context("P-204 maintenance history")
    assert len(sources) == 1
    mock_doc_service.retrieve.assert_called_once()


@pytest.mark.asyncio
async def test_document_specific_query_uses_rag():
    engine = _make_engine()
    mock_doc_service = MagicMock()
    mock_doc_service.has_documents.return_value = True
    mock_chunk = _make_chunk("doc2", "report.pdf", "Executive summary of plant inspection", 0.15)
    mock_doc_service.retrieve = AsyncMock(return_value=[mock_chunk])
    mock_doc_service._retriever = Retriever(MagicMock(), MagicMock(), max_distance=0.385)
    engine.set_doc_service(mock_doc_service)

    sources = await engine._retrieve_context("summarize uploaded document")
    assert len(sources) == 1
    mock_doc_service.retrieve.assert_called_once()


@pytest.mark.asyncio
async def test_bounded_dedup_multiple_relevant_chunks_preserved():
    """
    CRITICAL USER REQUIREMENT:
    Do NOT reduce every document to a single retrieved chunk.
    Multiple genuinely relevant chunks from the same document (e.g. different pages/sections)
    MUST be preserved.
    """
    mock_retriever = MagicMock()
    mock_retriever.is_chunk_relevant = lambda s: s <= 0.385

    # 3 distinct chunks from the SAME document across different pages
    c1 = _make_chunk("doc1", "p204_manual.pdf", "Page 1: Hydrocracker P-204 general specs and motor rating 1500 kW.", 0.1, page=1, idx=0)
    c2 = _make_chunk("doc1", "p204_manual.pdf", "Page 2: Mechanical seal flush plan API Plan 53B operating pressure 45 bar.", 0.15, page=2, idx=1)
    c3 = _make_chunk("doc1", "p204_manual.pdf", "Page 3: Vibration shutdown trip point 7.5 mm/s RMS at bearing housing.", 0.2, page=3, idx=2)

    mock_retriever.retrieve = AsyncMock(return_value=[c1, c2, c3])
    mock_retriever.expand_document_chunks = AsyncMock(return_value=[c1, c2, c3])

    search_fn = create_document_search(mock_retriever)
    res = await search_fn(DocumentSearchInput(query="P-204 specifications and vibration limits", top_k=5))

    assert len(res) == 3
    pages_returned = [r.get("page") for r in res]
    assert 1 in pages_returned
    assert 2 in pages_returned
    assert 3 in pages_returned


@pytest.mark.asyncio
async def test_bounded_dedup_near_identical_chunks_removed():
    """
    Duplicate/flooding results from the same document with nearly identical text MUST be deduplicated.
    """
    mock_retriever = MagicMock()
    mock_retriever.is_chunk_relevant = lambda s: s <= 0.385

    # 3 identical text chunks from the same document
    c1 = _make_chunk("doc1", "runbook.pdf", "Emergency shutdown procedure: press ESD button on console.", 0.1, page=1, idx=0)
    c2 = _make_chunk("doc1", "runbook.pdf", "Emergency shutdown procedure: press ESD button on console.", 0.12, page=1, idx=1)
    c3 = _make_chunk("doc1", "runbook.pdf", "Emergency shutdown procedure: press ESD button on console.", 0.14, page=1, idx=2)

    mock_retriever.retrieve = AsyncMock(return_value=[c1, c2, c3])
    mock_retriever.expand_document_chunks = AsyncMock(return_value=[c1, c2, c3])

    search_fn = create_document_search(mock_retriever)
    res = await search_fn(DocumentSearchInput(query="emergency shutdown", top_k=5))

    assert len(res) == 1


@pytest.mark.asyncio
async def test_bounded_dedup_mixed_docs():
    """
    Chunks from multiple documents: duplicate chunks within each document are deduplicated,
    while distinct chunks from each document remain.
    """
    mock_retriever = MagicMock()
    mock_retriever.is_chunk_relevant = lambda s: s <= 0.385

    # Doc 1 has 2 duplicates
    d1_c1 = _make_chunk("doc1", "doc1.txt", "Identical content in doc1", 0.1, idx=0)
    d1_c2 = _make_chunk("doc1", "doc1.txt", "Identical content in doc1", 0.11, idx=1)
    # Doc 2 has 2 distinct chunks
    d2_c1 = _make_chunk("doc2", "doc2.txt", "Unique content part A", 0.15, page=1, idx=0)
    d2_c2 = _make_chunk("doc2", "doc2.txt", "Unique content part B", 0.18, page=2, idx=1)

    mock_retriever.retrieve = AsyncMock(return_value=[d1_c1, d1_c2, d2_c1, d2_c2])
    mock_retriever.expand_document_chunks = AsyncMock(return_value=[d1_c1, d1_c2, d2_c1, d2_c2])

    search_fn = create_document_search(mock_retriever)
    res = await search_fn(DocumentSearchInput(query="content", top_k=5))

    assert len(res) == 3
    doc_ids = [r["document_id"] for r in res]
    assert doc_ids.count("doc1") == 1
    assert doc_ids.count("doc2") == 2


@pytest.mark.asyncio
async def test_nonexistent_document_query_returns_empty():
    mock_retriever = MagicMock()
    mock_retriever.is_chunk_relevant = lambda s: s <= 0.385
    mock_retriever.retrieve = AsyncMock(return_value=[])

    search_fn = create_document_search(mock_retriever)
    res = await search_fn(DocumentSearchInput(query="nonexistent query term not in index", top_k=5))
    assert res == []
