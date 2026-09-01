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

        # If vector_store is available, expand matched relevant documents to include all ordered chunks
        vector_store = getattr(retriever, "_store", None)
        seen_doc_ids = set()
        results = []

        if vector_store and hasattr(vector_store, "get_document_chunks"):
            for chunk in relevant_chunks:
                doc_id = getattr(chunk, "document_id", chunk.filename)
                if doc_id in seen_doc_ids:
                    continue
                try:
                    stored = vector_store.get_document_chunks(doc_id) if hasattr(vector_store, "get_document_chunks") else None
                    docs = stored.get("documents", []) if isinstance(stored, dict) else []
                    metas = stored.get("metadatas", []) if isinstance(stored, dict) else []
                    ids = stored.get("ids", []) if isinstance(stored, dict) else []
                    if isinstance(docs, list) and len(docs) > 0:
                        paired = []
                        for cid, doc_txt, meta in zip(ids, docs, metas):
                            c_idx = meta.get("chunk_index", 0) if isinstance(meta, dict) else 0
                            paired.append((c_idx, cid, doc_txt, meta))
                        paired.sort(key=lambda x: x[0])
                        for c_idx, cid, doc_txt, meta in paired:
                            fn = meta.get("filename", chunk.filename) if isinstance(meta, dict) else chunk.filename
                            page = meta.get("page") if isinstance(meta, dict) else chunk.page
                            results.append({
                                "filename": fn,
                                "relative_path": fn,
                                "document_id": doc_id,
                                "chunk_id": cid,
                                "chunk_index": c_idx,
                                "page": page,
                                "score": round(chunk.score, 4),
                                "text": doc_txt,
                            })
                    else:
                        results.append({
                            "filename": chunk.filename,
                            "relative_path": chunk.filename,
                            "document_id": doc_id,
                            "chunk_id": chunk.chunk_id,
                            "chunk_index": chunk.chunk_index,
                            "page": chunk.page,
                            "score": round(chunk.score, 4),
                            "text": chunk.text,
                        })
                except Exception as exc:
                    logger.warning("Failed to expand document chunks for doc_id=%s: %s", doc_id, exc)
                    results.append({
                        "filename": chunk.filename,
                        "relative_path": chunk.filename,
                        "document_id": doc_id,
                        "chunk_id": chunk.chunk_id,
                        "chunk_index": chunk.chunk_index,
                        "page": chunk.page,
                        "score": round(chunk.score, 4),
                        "text": chunk.text,
                    })
        else:
            for chunk in relevant_chunks:
                results.append({
                    "filename": chunk.filename,
                    "relative_path": chunk.filename,
                    "document_id": getattr(chunk, "document_id", chunk.filename),
                    "chunk_id": chunk.chunk_id,
                    "chunk_index": chunk.chunk_index,
                    "page": chunk.page,
                    "score": round(chunk.score, 4),
                    "text": chunk.text,
                })

        logger.info(
            "document_search | query_len=%d top_k=%d results=%d/%d",
            len(args.query), args.top_k, len(results), len(chunks),
        )
        return results

    return execute_document_search
