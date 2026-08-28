"""
backend/schemas/document.py
----------------------------
Pydantic schemas for the document API.

Frontend-friendly, no internal implementation details exposed.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Source / evidence (returned with RAG chat responses)
# ---------------------------------------------------------------------------

class SourceReference(BaseModel):
    """A single document chunk used as evidence in a RAG response."""
    document_id: str
    filename: str
    chunk_id: str
    chunk_index: int
    page: Optional[int] = None
    score: float = Field(description="Cosine distance (lower = more relevant)")
    file_type: str = ""


# ---------------------------------------------------------------------------
# Document management
# ---------------------------------------------------------------------------

class DocumentUploadResponse(BaseModel):
    """Response after successfully uploading and indexing a document."""
    document_id: str
    filename: str
    file_type: str
    chunks: int = Field(description="Number of text chunks indexed")
    status: str = "indexed"


class DocumentResponse(BaseModel):
    """Summary of a single indexed document."""
    document_id: str
    filename: str
    file_type: str
    chunk_count: int


class DocumentListResponse(BaseModel):
    """Response for GET /api/documents."""
    documents: List[DocumentResponse]
    total: int


class DocumentDeleteResponse(BaseModel):
    """Response after deleting a document."""
    document_id: str
    chunks_deleted: int
    status: str = "deleted"
