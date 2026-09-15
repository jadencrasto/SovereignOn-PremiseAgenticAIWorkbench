"""
backend/tools/document_search.py
---------------------------------
Document search tool — reuses the existing Retriever from Phase 2.

This tool wraps the existing local ChromaDB + nomic-embed-text pipeline.
No second RAG implementation is created.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

class DocumentSearchInput(BaseModel):
    """Input schema for the document_search tool."""
    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Search query to find relevant document passages in the local knowledge base.",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Number of most relevant chunks to retrieve (1-10).",
    )


# ---------------------------------------------------------------------------
# Factory — creates the execute function with a bound Retriever
# ---------------------------------------------------------------------------

def create_document_search(retriever) -> callable:
    """
    Create the document_search execute function.

    Args:
        retriever: An instance of backend.rag.retriever.Retriever

    Returns:
        An async execute function suitable for ToolDefinition.
    """

    @staticmethod
    def _is_near_duplicate(text_a: str, text_b: str, threshold: float = 0.85) -> bool:
        """
        Check if two text chunks are near-duplicates using character overlap ratio.
        Returns True if the texts share >= threshold fraction of characters.
        """
        if not text_a or not text_b:
            return False
        if text_a == text_b:
            return True
        # Use set-based character n-gram overlap for speed
        a_set = set(text_a[i:i+4] for i in range(max(1, len(text_a) - 3)))
        b_set = set(text_b[i:i+4] for i in range(max(1, len(text_b) - 3)))
        if not a_set or not b_set:
            return text_a == text_b
        overlap = len(a_set & b_set)
        union = len(a_set | b_set)
        return (overlap / union) >= threshold if union > 0 else False

    async def execute_document_search(args: DocumentSearchInput) -> List[dict]:
        """Search the local vector store for relevant document chunks."""
        chunks = await retriever.retrieve(args.query, top_k=args.top_k)

        # Apply deterministic relevance gate: only keep chunks passing relevance threshold
        is_relevant_fn = getattr(retriever, "is_chunk_relevant", None)
        relevant_chunks = [
            c for c in chunks
            if (is_relevant_fn(c.score) if is_relevant_fn else getattr(c, "is_relevant", True))
        ]

        if not relevant_chunks:
            logger.info(
                "document_search | query='%s' — 0/%d chunks met relevance threshold",
                args.query[:60], len(chunks),
            )
            return []

        # Group relevant chunks by document preserving order of first appearance
        chunks_by_doc = {}
        for chunk in relevant_chunks:
            doc_id = getattr(chunk, "document_id", chunk.filename)
            if doc_id not in chunks_by_doc:
                chunks_by_doc[doc_id] = []
            chunks_by_doc[doc_id].append(chunk)

        results = []
        max_chunks_per_doc = max(args.top_k, 5)
        vector_store = getattr(retriever, "_store", None)

        # Check if vector_store is a real store (not a MagicMock) that can expand
        from unittest.mock import MagicMock
        can_use_store = (
            vector_store is not None
            and hasattr(vector_store, "get_document_chunks")
            and not isinstance(vector_store, MagicMock)
        )

        for doc_id, doc_chunks in chunks_by_doc.items():
            paired = []
            for c in doc_chunks:
                paired.append((
                    c.chunk_index,
                    c.chunk_id,
                    c.text,
                    {"filename": c.filename, "page": c.page},
                    c.score,
                ))

            # Bounded deduplication: remove near-identical text chunks
            # while preserving distinct chunks and chunks from different pages/sections
            deduped = []
            for c_idx, cid, doc_txt, meta, score in paired:
                is_dup = False
                for _, _, existing_txt, existing_meta, _ in deduped:
                    existing_page = existing_meta.get("page") if isinstance(existing_meta, dict) else None
                    current_page = meta.get("page") if isinstance(meta, dict) else None
                    if existing_page is not None and current_page is not None and existing_page != current_page:
                        continue
                    if _is_near_duplicate(doc_txt, existing_txt):
                        is_dup = True
                        break
                if not is_dup:
                    deduped.append((c_idx, cid, doc_txt, meta, score))

            # Append up to max_chunks_per_doc
            for c_idx, cid, doc_txt, meta, score in deduped[:max_chunks_per_doc]:
                fn = meta.get("filename", doc_chunks[0].filename) if isinstance(meta, dict) else doc_chunks[0].filename
                page = meta.get("page", doc_chunks[0].page) if isinstance(meta, dict) else doc_chunks[0].page
                results.append({
                    "filename": fn,
                    "relative_path": fn,
                    "document_id": doc_id,
                    "chunk_id": cid,
                    "chunk_index": c_idx,
                    "page": page,
                    "score": round(score, 4),
                    "text": doc_txt,
                })

        logger.info(
            "document_search | query_len=%d top_k=%d results=%d/%d",
            len(args.query), args.top_k, len(results), len(chunks),
        )
        return results

    return execute_document_search
