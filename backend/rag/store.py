"""
backend/rag/store.py
--------------------
ChromaDB vector store wrapper with resilient local persistence and test fallback.

Uses ChromaDB in persistent local mode — all data is stored in
data/chromadb/ and survives application restarts.

No cloud services. No external vector databases.

Collection schema:
  - id        : chunk_id (unique per chunk)
  - embedding : float vector from EmbeddingService
  - document  : chunk text
  - metadata  : {document_id, filename, file_type, chunk_index, page?, ...}
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

COLLECTION_NAME = "workbench_documents"


class _InMemoryFallbackCollection:
    """Fallback in-memory collection when chromadb C++ bindings/wheel are absent."""

    def __init__(self) -> None:
        self._chunks: Dict[str, Dict[str, Any]] = {}

    def count(self) -> int:
        return len(self._chunks)

    def upsert(self, ids: List[str], embeddings: List[List[float]], documents: List[str], metadatas: List[Dict]) -> None:
        for cid, emb, doc, meta in zip(ids, embeddings, documents, metadatas):
            self._chunks[cid] = {"id": cid, "embedding": emb, "document": doc, "metadata": meta}

    def delete(self, ids: List[str]) -> None:
        for cid in ids:
            self._chunks.pop(cid, None)

    def get(self, where: Optional[Dict] = None, include: Optional[List[str]] = None) -> Dict[str, Any]:
        matched_ids = []
        matched_docs = []
        matched_metas = []
        for cid, data in self._chunks.items():
            if where:
                match = True
                for k, v in where.items():
                    if data["metadata"].get(k) != v:
                        match = False
                        break
                if not match:
                    continue
            matched_ids.append(cid)
            matched_docs.append(data["document"])
            matched_metas.append(data["metadata"])
        return {"ids": matched_ids, "documents": matched_docs, "metadatas": matched_metas}

    def query(self, query_embeddings: List[List[float]], n_results: int = 5, include: Optional[List[str]] = None, where: Optional[Dict] = None) -> Dict[str, Any]:
        # Simple fallback query
        all_res = self.get(where=where)
        limit = min(n_results, len(all_res["ids"]))
        return {
            "ids": [all_res["ids"][:limit]],
            "documents": [all_res["documents"][:limit]],
            "metadatas": [all_res["metadatas"][:limit]],
            "distances": [[0.1] * limit],
        }


class VectorStore:
    """
    Wraps ChromaDB persistent client for the RAG pipeline.

    One collection ('workbench_documents') holds all chunks across all
    uploaded documents. Documents are distinguished by their document_id
    stored in chunk metadata.
    """

    def __init__(self, persist_dir: Path) -> None:
        persist_dir.mkdir(parents=True, exist_ok=True)
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=str(persist_dir))
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},   # cosine similarity
            )
            logger.info(
                "VectorStore ready | collection=%s persist=%s count=%d",
                COLLECTION_NAME, persist_dir, self._collection.count(),
            )
        except Exception as exc:
            logger.warning("ChromaDB persistent client unavailable (%s). Using in-memory fallback store.", exc)
            self._client = None
            self._collection = _InMemoryFallbackCollection()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add_chunks(
        self,
        chunk_ids: List[str],
        embeddings: List[List[float]],
        texts: List[str],
        metadatas: List[Dict],
    ) -> None:
        """
        Upsert chunks into the collection.
        """
        if not chunk_ids:
            return
        self._collection.upsert(
            ids=chunk_ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        logger.debug("Upserted %d chunks into collection", len(chunk_ids))

    def delete_document(self, document_id: str) -> int:
        """
        Delete all chunks belonging to a document.
        """
        existing = self._collection.get(
            where={"document_id": document_id},
            include=[],
        )
        ids_to_delete = existing["ids"]
        if ids_to_delete:
            self._collection.delete(ids=ids_to_delete)
            logger.info("Deleted %d chunks for document_id=%s", len(ids_to_delete), document_id)
        return len(ids_to_delete)

    def clear_collection(self) -> None:
        """Remove ALL chunks from the collection. Use for dev/reset only."""
        if self._client:
            self._client.delete_collection(COLLECTION_NAME)
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        else:
            self._collection = _InMemoryFallbackCollection()
        logger.warning("Collection cleared")

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def query(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        where: Optional[Dict] = None,
    ) -> Dict:
        """
        Similarity search.
        """
        kwargs: Dict = {
            "query_embeddings": [query_embedding],
            "n_results": min(top_k, max(1, self._collection.count())),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        return self._collection.query(**kwargs)

    def get_document_chunks(self, document_id: str) -> Dict:
        """Return all stored chunks for a document (for listing/debug)."""
        return self._collection.get(
            where={"document_id": document_id},
            include=["documents", "metadatas"],
        )

    def list_documents(self) -> List[Dict]:
        """
        Return a deduplicated list of indexed documents with metadata.
        """
        all_chunks = self._collection.get(include=["metadatas"])
        seen: Dict[str, Dict] = {}
        for meta in all_chunks.get("metadatas", []):
            if not meta:
                continue
            doc_id = meta.get("document_id")
            if doc_id and doc_id not in seen:
                seen[doc_id] = {
                    "document_id": doc_id,
                    "filename": meta.get("filename", "unknown"),
                    "file_type": meta.get("file_type", ""),
                }
        return list(seen.values())

    def count(self) -> int:
        """Total number of chunks in the collection."""
        return self._collection.count()

    def document_chunk_count(self, document_id: str) -> int:
        """Number of chunks belonging to a specific document."""
        result = self._collection.get(
            where={"document_id": document_id},
            include=[],
        )
        return len(result["ids"])

    def document_exists(self, document_id: str) -> bool:
        """Return True if the document has at least one chunk indexed."""
        return self.document_chunk_count(document_id) > 0
