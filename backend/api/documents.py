"""
backend/api/documents.py
------------------------
Document management API.

Endpoints:
  POST   /api/documents                     — Upload & index a document
  GET    /api/documents                     — List indexed documents
  DELETE /api/documents/{document_id}       — Delete a document

Security:
  - File type validated against whitelist
  - Filename sanitized (path traversal prevented in service layer)
  - File size validated (MAX_FILE_SIZE_MB from config)
  - Files saved only to data/uploads/
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from backend.schemas.document import (
    DocumentChunkItem,
    DocumentDeleteResponse,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from backend.auth.dependencies import get_current_user, require_permission
from backend.auth.models import Permission, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------

def get_doc_service(request: Request):
    return request.app.state.doc_service


# ---------------------------------------------------------------------------
# POST /api/documents — upload & ingest
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=DocumentUploadResponse,
    summary="Upload and index a document",
)
async def upload_document(
    file: UploadFile = File(..., description="PDF, TXT, MD, or DOCX file"),
    doc_service=Depends(get_doc_service),
    current_user: User = Depends(require_permission(Permission.EXECUTE_WRITE_TOOLS)),
):
    """
    Upload a document for RAG indexing.

    The document is:
      1. Validated (file type + size).
      2. Parsed (text extracted).
      3. Chunked (split into overlapping segments).
      4. Embedded (local Ollama nomic-embed-text).
      5. Stored (local ChromaDB).

    Returns document_id and chunk count.  The document_id can be used
    to delete the document later.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    logger.info("Upload request: filename=%s content_type=%s", file.filename, file.content_type)

    # Read file content
    try:
        content = await file.read()
    except Exception as exc:
        logger.error("Failed to read uploaded file: %s", exc)
        raise HTTPException(status_code=400, detail="Failed to read uploaded file.")
    finally:
        await file.close()

    # Delegate to service (handles all validation + pipeline)
    try:
        result = await doc_service.ingest_document(file.filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        logger.error("Ingestion failed: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))

    return DocumentUploadResponse(
        document_id=result.document_id,
        filename=result.filename,
        file_type=result.file_type,
        chunks=result.chunk_count,
        status=result.status,
    )


# ---------------------------------------------------------------------------
# GET /api/documents — list all indexed documents
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List all indexed documents",
)
async def list_documents(
    doc_service=Depends(get_doc_service),
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    """Return all documents currently indexed in the vector store."""
    docs = doc_service.list_documents()
    return DocumentListResponse(
        documents=[
            DocumentResponse(
                document_id=d.document_id,
                filename=d.filename,
                file_type=d.file_type,
                chunk_count=d.chunk_count,
            )
            for d in docs
        ],
        total=len(docs),
    )


# ---------------------------------------------------------------------------
# GET /api/documents/{document_id} — get document details and chunks
# ---------------------------------------------------------------------------

@router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
    summary="Get details and vector chunks for an indexed document",
)
async def get_document(
    document_id: str,
    doc_service=Depends(get_doc_service),
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    """
    Retrieve metadata and all stored vector chunks for an indexed document.
    Read-only inspection endpoint.
    """
    if not document_id or len(document_id) > 100:
        raise HTTPException(status_code=400, detail="Invalid document_id.")

    details = doc_service.get_document_details(document_id)
    if details is None:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found.")

    return DocumentDetailResponse(**details)


# ---------------------------------------------------------------------------
# DELETE /api/documents/{document_id}
# ---------------------------------------------------------------------------

@router.delete(
    "/{document_id}",
    response_model=DocumentDeleteResponse,
    summary="Delete an indexed document",
)
async def delete_document(
    document_id: str,
    doc_service=Depends(get_doc_service),
    current_user: User = Depends(require_permission(Permission.EXECUTE_WRITE_TOOLS)),
):
    """
    Remove a document from the vector store and delete the uploaded file.

    This operation is permanent.
    """
    # Basic input validation
    if not document_id or len(document_id) > 100:
        raise HTTPException(status_code=400, detail="Invalid document_id.")

    try:
        deleted_chunks = doc_service.delete_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Delete failed for %s: %s", document_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete document.")

    return DocumentDeleteResponse(
        document_id=document_id,
        chunks_deleted=deleted_chunks,
    )
