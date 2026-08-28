"""
backend/schemas/chat.py
-----------------------
Pydantic models for the chat API.

These are the data contracts between the frontend and backend.
No internal implementation details leak through these schemas.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, List, Any
from pydantic import BaseModel, Field


# Forward-declared here to avoid circular import;
# full schema is in backend/schemas/document.py
class _SourceRef(BaseModel):
    """Inline source reference (mirrors SourceReference in document.py)."""
    document_id: str
    filename: str
    chunk_id: str
    chunk_index: int
    page: Optional[int] = None
    score: float
    file_type: str = ""


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

class MessageRole(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    """A single message in a conversation."""
    role: MessageRole
    content: str


class ChatRequest(BaseModel):
    """
    POST /api/chat request body.

    - session_id  : client-generated UUID to track conversation state.
                    If omitted, a new session is created each request.
    - message     : the user's current message.
    - model       : optional override of the default model
                    (e.g. "ollama/qwen2.5:7b").
    - stream      : if True, the response is SSE; otherwise JSON.
    """
    session_id: Optional[str] = Field(
        default=None,
        description="Conversation session ID. Generates a new session if absent.",
    )
    message: str = Field(..., min_length=1, max_length=32_000)
    model: Optional[str] = Field(
        default=None,
        description="Override model in 'provider/model' format, e.g. 'ollama/qwen2.5:7b'",
    )
    stream: bool = Field(default=True)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class ChatResponse(BaseModel):
    """Non-streaming chat response."""
    session_id: str
    message: ChatMessage
    model_used: str
    sources: Optional[List[_SourceRef]] = Field(
        default=None,
        description="Document chunks used as evidence (RAG). None when no documents indexed.",
    )


class StreamChunk(BaseModel):
    """
    A single chunk sent over the SSE stream.

    type values:
      'delta'    — partial assistant text
      'done'     — stream finished; content holds final session_id
      'sources'  — sent just before 'done' with retrieved evidence list
      'error'    — something went wrong; content holds the error message
    """
    type: str  # 'delta' | 'done' | 'sources' | 'error'
    content: str
    session_id: Optional[str] = None
    model_used: Optional[str] = None
    sources: Optional[List[_SourceRef]] = None


# ---------------------------------------------------------------------------
# Health response
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """GET /api/health response."""
    status: str
    service: str
    version: str
    environment: str
    model_provider: str
    default_model: str
    ollama_url: str
