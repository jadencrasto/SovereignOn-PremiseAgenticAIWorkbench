"""
backend/rag/service.py
----------------------
Document service — coordinates the full ingestion pipeline and
provides a clean interface for the API layer.

Pipeline:
  upload bytes
      ↓
  DocumentParser.parse()   → Document
      ↓
  TextChunker.chunk()      → List[Chunk]
      ↓
  EmbeddingService.embed_many()  → List[List[float]]
      ↓
  VectorStore.add_chunks()
      ↓
  return DocumentUploadResult

This service is the only place that knows all the moving parts.
The API router just calls service methods.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from backend.config import Settings
from backend.rag.embeddings import EmbeddingService
from backend.rag.ingest import Chunk, Document, DocumentParser, TextChunker
from backend.rag.retriever import RetrievedChunk, Retriever
from backend.rag.store import VectorStore

logger = logging.getLogger(__name__)

# Allowed upload extensions — security boundary
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx"}
MAX_FILENAME_LEN = 255


# ---------------------------------------------------------------------------
# Result structures
# ---------------------------------------------------------------------------

@dataclass
class DocumentUploadResult:
    """Returned after a successful document ingestion."""
    document_id: str
    filename: str
    file_type: str
    chunk_count: int
    status: str = "indexed"


@dataclass
class DocumentInfo:
    """Summary of an indexed document."""
    document_id: str
    filename: str
    file_type: str
    chunk_count: int


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DocumentService:
    """
    Coordinates document ingestion, storage, and retrieval.

    One instance is shared across the application lifecycle.
    """

    def __init__(
        self,
        settings: Settings,
        embedding_service: EmbeddingService,
        vector_store: VectorStore,
        retriever: Retriever,
    ) -> None:
        self._settings = settings
        self._embedder = embedding_service
        self._store = vector_store
        self._retriever = retriever
        self._parser = DocumentParser()
        self._chunker = TextChunker(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def ingest_document(
        self,
        filename: str,
        content: bytes,
        clearance: str = "viewer",
    ) -> DocumentUploadResult:
        """
        Full ingestion pipeline for one uploaded file.

        Security checks:
          - Extension whitelist
          - Filename sanitization
          - File size limit
          - No path traversal

        Args:
            filename  : original filename from the upload
            content   : raw file bytes
            clearance : verified server-side clearance ('viewer', 'operator', 'admin')

        Returns:
            DocumentUploadResult with document_id and chunk count.

        Raises:
            ValueError : invalid file type, too large, empty, etc.
        """
        clean_clearance = str(clearance or "viewer").strip().lower()
        if clean_clearance not in ("viewer", "operator", "admin"):
            clean_clearance = "viewer"

        # ------ Security validation ------
        filename = self._sanitize_filename(filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise ValueError(
                f"File type '{suffix}' is not allowed. "
                f"Allowed: {sorted(ALLOWED_EXTENSIONS)}"
            )
        max_bytes = self._settings.max_file_size_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise ValueError(
                f"File is too large ({len(content) // 1024 // 1024} MB). "
                f"Maximum: {self._settings.max_file_size_mb} MB"
            )
        if not content:
            raise ValueError("Uploaded file is empty.")

        logger.info("Ingesting document: %s (%d bytes, clearance=%s)", filename, len(content), clean_clearance)

        # ------ Parse ------
        document = DocumentParser.parse(filename, content)
        document.metadata["clearance"] = clean_clearance
        logger.info(
            "Parsed %s → doc_id=%s text_len=%d",
            filename, document.document_id, len(document.text),
        )

        # ------ Save original file to uploads/ ------
        upload_path = self._settings.upload_dir / f"{document.document_id}_{filename}"
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        upload_path.write_bytes(content)
        logger.debug("Saved original file to %s", upload_path)

        # ------ Chunk ------
        chunks = self._chunker.chunk(document)
        if not chunks:
            raise ValueError(f"No usable text chunks could be extracted from '{filename}'.")
        logger.info("Created %d chunks for %s", len(chunks), filename)

        # ------ Embed ------
        texts = [c.text for c in chunks]
        embeddings = await self._embedder.embed_many(texts)
        logger.info("Generated %d embeddings for %s", len(embeddings), filename)

        # ------ Store ------
        chunk_ids = [c.chunk_id for c in chunks]
        metadatas = [self._chunk_to_metadata(c) for c in chunks]
        for m in metadatas:
            m["clearance"] = clean_clearance
        self._store.add_chunks(
            chunk_ids=chunk_ids,
            embeddings=embeddings,
            texts=texts,
            metadatas=metadatas,
        )
        logger.info("Indexed %d chunks for document_id=%s", len(chunks), document.document_id)

        # ------ Knowledge Graph Ingestion Hook (Non-fatal Isolation) ------
        try:
            if hasattr(self, "_graph_service") and self._graph_service:
                self._graph_service.extract_and_index_document(
                    document=document,
                    chunks=chunks,
                    clearance=clean_clearance,
                )
        except Exception as exc:
            logger.warning(
                "Knowledge graph extraction failed for %s: %s (non-fatal, vector indexing preserved)",
                filename, exc,
            )

        return DocumentUploadResult(
            document_id=document.document_id,
            filename=filename,
            file_type=document.file_type,
            chunk_count=len(chunks),
        )

    def set_graph_service(self, graph_service) -> None:
        """Attach knowledge graph service for cross-modal ingestion & cleanup."""
        self._graph_service = graph_service

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        user_clearance: str = "viewer",
    ) -> List[RetrievedChunk]:
        """Semantic retrieval — delegates to the Retriever with clearance gating."""
        return await self._retriever.retrieve(query, top_k=top_k, user_clearance=user_clearance)

    def has_documents(self) -> bool:
        """True if at least one document chunk has been indexed."""
        return self._store.count() > 0

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def list_documents(self, user_clearance: str = "admin") -> List[DocumentInfo]:
        """
        Return a list of indexed documents visible at user_clearance.
        Restricted documents with 0 accessible chunks are excluded to prevent metadata leakage.
        """
        from backend.auth.models import is_clearance_sufficient, parse_clearance

        raw = self._store.list_documents()
        infos: List[DocumentInfo] = []
        for doc_meta in raw:
            doc_id = doc_meta["document_id"]
            chunks_data = self._store.get_document_chunks(doc_id)
            metas = chunks_data.get("metadatas", [])

            # Count chunks accessible to this user
            permitted_count = 0
            for m in metas:
                raw_clr = m.get("clearance") if isinstance(m, dict) else None
                if not raw_clr or not parse_clearance(raw_clr):
                    req_clr = "admin"
                else:
                    req_clr = str(raw_clr).strip().lower()
                if is_clearance_sufficient(user_clearance, req_clr):
                    permitted_count += 1

            # Do not reveal restricted documents in listing
            if permitted_count == 0:
                continue

            infos.append(DocumentInfo(
                document_id=doc_id,
                filename=doc_meta.get("filename", "unknown"),
                file_type=doc_meta.get("file_type", ""),
                chunk_count=permitted_count,
            ))
        return infos

    def get_document_details(self, document_id: str, user_clearance: str = "admin") -> Optional[Dict]:
        """
        Retrieve full details and vector chunks for a specific indexed document,
        filtering out chunks exceeding user_clearance.

        Returns None if document does not exist or user has zero accessible chunks.
        """
        from backend.auth.models import is_clearance_sufficient, parse_clearance

        if not self._store.document_exists(document_id):
            # Check if document_id was passed as a filename
            all_docs = self._store.list_documents()
            matching = [d for d in all_docs if d.get("document_id") == document_id or d.get("filename") == document_id]
            if matching:
                document_id = matching[0]["document_id"]
            else:
                return None

        chunks_data = self._store.get_document_chunks(document_id)
        ids = chunks_data.get("ids", [])
        docs = chunks_data.get("documents", [])
        metas = chunks_data.get("metadatas", [])

        if not ids:
            return None

        filename = metas[0].get("filename", "unknown") if metas else "unknown"
        file_type = metas[0].get("file_type", "") if metas else ""

        # Construct chunk items and apply clearance filtering
        chunk_items = []
        for i, (cid, text, meta) in enumerate(zip(ids, docs, metas)):
            meta_dict = meta if isinstance(meta, dict) else {}
            raw_clr = meta_dict.get("clearance")
            if not raw_clr or not parse_clearance(raw_clr):
                req_clr = "admin"
            else:
                req_clr = str(raw_clr).strip().lower()

            # Skip chunk if user lacks sufficient clearance
            if not is_clearance_sufficient(user_clearance, req_clr):
                continue

            chunk_items.append({
                "chunk_id": cid,
                "chunk_index": meta_dict.get("chunk_index", i),
                "page": meta_dict.get("page"),
                "text": text,
                "metadata": meta_dict,
            })

        if not chunk_items:
            # Document exists but user has insufficient clearance to view any chunk
            return None

        chunk_items.sort(key=lambda c: c["chunk_index"])

        return {
            "document_id": document_id,
            "filename": filename,
            "file_type": file_type,
            "chunk_count": len(chunk_items),
            "relative_path": filename,
            "chunks": chunk_items,
        }

    def delete_document(self, document_id: str) -> int:
        """
        Delete document from the vector store and remove the uploaded file.

        Returns the number of chunks deleted.
        Raises ValueError if document not found.
        """
        if not self._store.document_exists(document_id):
            raise ValueError(f"Document '{document_id}' not found in the index.")

        # Get filename before deleting (so we can remove the upload)
        chunks_data = self._store.get_document_chunks(document_id)
        filename = None
        if chunks_data.get("metadatas"):
            filename = chunks_data["metadatas"][0].get("filename")

        # Delete from vector store
        deleted = self._store.delete_document(document_id)

        # Remove uploaded file safely
        if filename:
            self._delete_upload_file(document_id, filename)

        # Clean up knowledge graph records safely
        try:
            if hasattr(self, "_graph_service") and self._graph_service:
                self._graph_service.delete_document_entities(document_id)
        except Exception as exc:
            logger.warning("Knowledge graph cleanup failed for doc_id=%s: %s", document_id, exc)

        logger.info("Deleted document %s (%d chunks)", document_id, deleted)
        return deleted

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """
        Sanitize a filename for safe storage.

        - Strip path components (prevent path traversal).
        - Limit length.
        - Reject filenames with null bytes.
        """
        if "\x00" in filename:
            raise ValueError("Filename contains null bytes.")
        # Take only the final component — strip any directory prefix
        name = Path(filename).name
        if not name:
            raise ValueError("Filename is empty after sanitization.")
        if len(name) > MAX_FILENAME_LEN:
            raise ValueError(f"Filename is too long (max {MAX_FILENAME_LEN} chars).")
        # Reject filenames with directory separators still present
        if "/" in name or "\\" in name:
            raise ValueError("Filename contains path separators.")
        return name

    @staticmethod
    def _chunk_to_metadata(chunk: Chunk) -> Dict:
        """Convert a Chunk to ChromaDB-compatible metadata (primitive values only)."""
        meta: Dict = {
            "document_id": chunk.document_id,
            "filename": chunk.filename,
            "file_type": chunk.file_type,
            "chunk_index": chunk.chunk_index,
        }
        # ChromaDB requires metadata values to be str | int | float | bool
        for k, v in chunk.metadata.items():
            if isinstance(v, (str, int, float, bool)):
                meta[k] = v
        return meta

    def _delete_upload_file(self, document_id: str, filename: str) -> None:
        """Safely delete the original uploaded file."""
        upload_path = self._settings.upload_dir / f"{document_id}_{filename}"
        try:
            if upload_path.exists() and upload_path.is_file():
                # Extra safety: ensure path is still inside upload_dir
                resolved = upload_path.resolve()
                upload_dir = self._settings.upload_dir.resolve()
                if str(resolved).startswith(str(upload_dir)):
                    upload_path.unlink()
                    logger.debug("Removed uploaded file: %s", upload_path)
        except Exception as exc:
            logger.warning("Could not delete upload file %s: %s", upload_path, exc)
