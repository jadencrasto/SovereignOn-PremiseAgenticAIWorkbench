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

        results = []
        for chunk in chunks:
            results.append({
                "filename": chunk.filename,
                "chunk_id": chunk.chunk_id,
                "chunk_index": chunk.chunk_index,
                "page": chunk.page,
                "score": round(chunk.score, 4),
                "text": chunk.text[:1000],  # Cap text length for tool result
            })

        logger.info(
            "document_search | query_len=%d top_k=%d results=%d",
            len(args.query), args.top_k, len(results),
        )
        return results

    return execute_document_search
