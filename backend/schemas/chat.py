"""
backend/schemas/chat.py
-----------------------
Pydantic models for the chat API.

These are the data contracts between the frontend and backend.
No internal implementation details leak through these schemas.

Phase 5 additions (fully backward-compatible):
  - ImageAttachment — metadata for an uploaded image (no base64 returned to client)
  - Multimodal chat request fields for /api/chat/multimodal
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
# Phase 5: Image attachment metadata
# ---------------------------------------------------------------------------

class ImageAttachment(BaseModel):
    """
    Metadata for an image attached to a multimodal chat request.

    IMPORTANT: Base64 image data is NEVER returned here.
    Only minimal metadata is exposed to the client.
    """
    attachment_id: str = Field(description="Unique ID for this image attachment")
    filename: str = Field(description="Sanitized filename")
    mime_type: str = Field(description="Detected MIME type, e.g. 'image/png'")
    size_bytes: int = Field(description="File size in bytes")
    width: Optional[int] = Field(default=None, description="Image width in pixels (if detected)")
    height: Optional[int] = Field(default=None, description="Image height in pixels (if detected)")


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
    - tools_enabled : if True (default), the agent may use tools.
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
    tools_enabled: Optional[bool] = Field(
        default=True,
        description="Enable agentic tool execution. Set to false for plain chat.",
    )
    planning_enabled: Optional[bool] = Field(
        default=True,
        description="Phase 6: Enable agent planning for complex multi-step requests.",
    )


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
      'delta'        — partial assistant text
      'done'         — stream finished; content holds final session_id
      'sources'      — sent just before 'done' with retrieved evidence list
      'error'        — something went wrong; content holds the error message
      'tool_start'   — agent is executing a tool
      'tool_result'  — tool execution completed
      'agent_status' — agent state update (e.g. "thinking", "analyzing_image")
    """
    type: str  # 'delta' | 'done' | 'sources' | 'error' | 'tool_start' | 'tool_result' | 'agent_status' | 'plan_created' | 'plan_step' | 'approval_required' | 'approval_granted' | 'approval_rejected' | 'task_started' | 'task_completed' | 'task_failed' | 'task_cancelled'
    content: str
    session_id: Optional[str] = None
    model_used: Optional[str] = None
    sources: Optional[List[_SourceRef]] = None
    # Tool event fields
    tool: Optional[str] = None
    tool_args: Optional[dict] = None
    success: Optional[bool] = None
    summary: Optional[str] = None
    # Phase 5: image attachment metadata (multimodal responses)
    attachment: Optional[ImageAttachment] = None


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
