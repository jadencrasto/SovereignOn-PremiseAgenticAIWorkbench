"""
backend/rag/retriever.py
------------------------
Semantic retrieval from the ChromaDB vector store.

The retriever is a thin layer that:
  1. Takes a user query string.
  2. Embeds it using EmbeddingService.
  3. Queries VectorStore for the top-k most similar chunks.
  4. Returns structured RetrievedChunk results.

The agent engine calls the retriever — it does not call ChromaDB directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from backend.rag.embeddings import EmbeddingService
from backend.rag.store import VectorStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------

@dataclass
class RetrievedChunk:
    """A single retrieved document chunk with relevance metadata."""
    text: str
    document_id: str
    filename: str
    chunk_id: str
    chunk_index: int
    page: Optional[int]
    score: float            # cosine distance (lower = more similar)
    file_type: str = ""


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

class Retriever:
    """
    Semantic retrieval over the local ChromaDB collection.

    Usage:
        retriever = Retriever(embedding_service, vector_store, top_k=5)
        chunks = await retriever.retrieve("What is the refund policy?")
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_store: VectorStore,
        top_k: int = 5,
    ) -> None:
        self._embedder = embedding_service
        self._store = vector_store
        self._top_k = top_k

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> List[RetrievedChunk]:
        """
        Retrieve the most relevant chunks for a query.

        Args:
            query  : the user's question or search string
            top_k  : override the default top_k for this call

        Returns:
            List of RetrievedChunk, ordered by relevance (most relevant first).
            Returns [] if the collection is empty or embedding fails.
        """
        k = top_k or self._top_k

        # Nothing indexed yet — return empty gracefully
        if self._store.count() == 0:
            logger.debug("Retriever: collection is empty, skipping retrieval")
            return []

        # Embed the query
        try:
            query_embedding = await self._embedder.embed(query)
        except Exception as exc:
            logger.error("Retriever: failed to embed query: %s", exc)
            return []

        # Query the store
        try:
            raw = self._store.query(query_embedding=query_embedding, top_k=k)
        except Exception as exc:
            logger.error("Retriever: ChromaDB query failed: %s", exc)
            return []

        # Parse results
        results: List[RetrievedChunk] = []
        ids = raw.get("ids", [[]])[0]
        documents = raw.get("documents", [[]])[0]
        metadatas = raw.get("metadatas", [[]])[0]
        distances = raw.get("distances", [[]])[0]

        for chunk_id, text, meta, dist in zip(ids, documents, metadatas, distances):
            if not text or not meta:
                continue
            results.append(RetrievedChunk(
                text=text,
                document_id=meta.get("document_id", ""),
                filename=meta.get("filename", "unknown"),
                chunk_id=chunk_id,
                chunk_index=int(meta.get("chunk_index", 0)),
                page=int(meta["page"]) if "page" in meta else None,
                score=float(dist),
                file_type=meta.get("file_type", ""),
            ))

        logger.info(
            "Retrieved %d chunks for query (len=%d) top_k=%d",
            len(results), len(query), k,
        )
        return results
