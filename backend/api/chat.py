"""
backend/api/chat.py
-------------------
Chat API route — Phase 4 (with RAG + Tools support).

Endpoints:
  POST /api/chat          — SSE streaming (default) or JSON response
  GET  /api/chat/sessions — List active sessions (debug helper)
  DELETE /api/chat/sessions/{session_id} — Clear a session

SSE stream format:
  data: {"type": "agent_status", "content": "thinking"}
  data: {"type": "tool_start",   "content": "", "tool": "calculator", ...}
  data: {"type": "tool_result",  "content": "", "tool": "calculator", "success": true, ...}
  data: {"type": "delta",        "content": "Hello"}
  data: {"type": "sources",      "content": "", "sources": [...]}   ← RAG evidence
  data: {"type": "done",         "content": "", "session_id": "...", "model_used": "..."}
  data: {"type": "error",        "content": "Error message"}

Backward compatibility:
  - 'sources' event is omitted if no documents are indexed.
  - tools_enabled=false reverts to Phase 3 behavior.
  - All Phase 1/2/3 SSE consumers that only read 'delta' and 'done' still work.
"""

from __future__ import annotations

import logging
import uuid
from typing import AsyncGenerator, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatMessage,
    MessageRole,
    StreamChunk,
    _SourceRef,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def get_engine(request: Request):
    return request.app.state.engine


def get_router_obj(request: Request):
    return request.app.state.model_router


# ---------------------------------------------------------------------------
# POST /api/chat
# ---------------------------------------------------------------------------

@router.post("", summary="Send a message and receive a response")
async def chat(
    body: ChatRequest,
    request: Request,
    engine=Depends(get_engine),
    model_router=Depends(get_router_obj),
):
    """
    Send a user message.

    - If `stream=true` (default): returns SSE stream with delta, sources, done events.
    - If `stream=false`: returns JSON with message + sources list.
    - If `tools_enabled=true` (default): agent may use local tools.
    """
    session_id = body.session_id or str(uuid.uuid4())

    provider_name, model_name = model_router.resolve_model(body.model)
    model_used = f"{provider_name}/{model_name}"

    logger.info(
        "chat_request | session=%s model=%s stream=%s tools=%s len=%d",
        session_id, model_used, body.stream, body.tools_enabled, len(body.message),
    )

    if body.stream:
        # Choose tool-enabled or plain streaming
        use_tools = body.tools_enabled and hasattr(engine, '_tool_registry') and engine._tool_registry is not None
        if use_tools:
            return StreamingResponse(
                _stream_sse_with_tools(engine, session_id, body.message, body.model, model_used),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )
        else:
            return StreamingResponse(
                _stream_sse(engine, session_id, body.message, body.model, model_used),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

    # Non-streaming path
    try:
        content, sources = await engine.chat(session_id, body.message, body.model)
    except RuntimeError as exc:
        logger.error("chat error: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))

    source_refs = _sources_to_refs(sources)
    return ChatResponse(
        session_id=session_id,
        message=ChatMessage(role=MessageRole.assistant, content=content),
        model_used=model_used,
        sources=source_refs if source_refs else None,
    )


# ---------------------------------------------------------------------------
# SSE generator — Plain (Phase 3 backward compatible)
# ---------------------------------------------------------------------------

async def _stream_sse(
    engine,
    session_id: str,
    user_message: str,
    model_id,
    model_used: str,
) -> AsyncGenerator[str, None]:
    """
    Wrap the agent engine stream in SSE format.

    The engine yields str deltas followed by a list sentinel (the sources).
    """
    try:
        sources = []
        async for item in engine.chat_stream(session_id, user_message, model_id):
            if isinstance(item, str):
                # Text delta
                chunk = StreamChunk(type="delta", content=item)
                yield f"data: {chunk.model_dump_json()}\n\n"
            elif isinstance(item, list):
                # Sources sentinel emitted by engine after streaming finishes
                sources = item

        # Emit sources event (if any) before done
        if sources:
            source_refs = _sources_to_refs(sources)
            sources_chunk = StreamChunk(
                type="sources",
                content="",
                sources=source_refs,
            )
            yield f"data: {sources_chunk.model_dump_json()}\n\n"

        # Final done event
        done_chunk = StreamChunk(
            type="done",
            content="",
            session_id=session_id,
            model_used=model_used,
        )
        yield f"data: {done_chunk.model_dump_json()}\n\n"

    except RuntimeError as exc:
        logger.error("stream error | session=%s: %s", session_id, exc)
        error_chunk = StreamChunk(type="error", content=str(exc))
        yield f"data: {error_chunk.model_dump_json()}\n\n"

    except Exception as exc:
        logger.exception("unexpected stream error | session=%s", session_id)
        error_chunk = StreamChunk(type="error", content="Internal server error")
        yield f"data: {error_chunk.model_dump_json()}\n\n"


# ---------------------------------------------------------------------------
# SSE generator — Tool-enabled (Phase 4)
# ---------------------------------------------------------------------------

async def _stream_sse_with_tools(
    engine,
    session_id: str,
    user_message: str,
    model_id,
    model_used: str,
) -> AsyncGenerator[str, None]:
    """
    Wrap the agent engine tool stream in SSE format.

    The engine yields:
        str   — text delta
        dict  — tool/agent event
        list  — sources sentinel
    """
    try:
        sources = []

        async for item in engine.chat_stream_with_tools(session_id, user_message, model_id):
            if isinstance(item, str):
                # Text delta
                chunk = StreamChunk(type="delta", content=item)
                yield f"data: {chunk.model_dump_json()}\n\n"

            elif isinstance(item, dict):
                event_type = item.get("type", "agent_status")

                if event_type == "tool_start":
                    chunk = StreamChunk(
                        type="tool_start",
                        content="",
                        tool=item.get("tool", ""),
                        tool_args=item.get("arguments", {}),
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"

                elif event_type == "tool_result":
                    chunk = StreamChunk(
                        type="tool_result",
                        content="",
                        tool=item.get("tool", ""),
                        success=item.get("success", False),
                        summary=item.get("summary", ""),
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"

                elif event_type == "agent_status":
                    chunk = StreamChunk(
                        type="agent_status",
                        content=item.get("status", "thinking"),
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"

            elif isinstance(item, list):
                # Sources sentinel
                sources = item

        # Emit sources event (if any) before done
        if sources:
            source_refs = _sources_to_refs(sources)
            sources_chunk = StreamChunk(
                type="sources",
                content="",
                sources=source_refs,
            )
            yield f"data: {sources_chunk.model_dump_json()}\n\n"

        # Final done event
        done_chunk = StreamChunk(
            type="done",
            content="",
            session_id=session_id,
            model_used=model_used,
        )
        yield f"data: {done_chunk.model_dump_json()}\n\n"

    except RuntimeError as exc:
        logger.error("tool stream error | session=%s: %s", session_id, exc)
        error_chunk = StreamChunk(type="error", content=str(exc))
        yield f"data: {error_chunk.model_dump_json()}\n\n"

    except Exception as exc:
        logger.exception("unexpected tool stream error | session=%s", session_id)
        error_chunk = StreamChunk(type="error", content="Internal server error")
        yield f"data: {error_chunk.model_dump_json()}\n\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sources_to_refs(sources: List) -> List[_SourceRef]:
    """Convert RetrievedChunk objects to _SourceRef schema objects."""
    refs = []
    for s in sources:
        try:
            refs.append(_SourceRef(
                document_id=s.document_id,
                filename=s.filename,
                chunk_id=s.chunk_id,
                chunk_index=s.chunk_index,
                page=s.page,
                score=s.score,
                file_type=s.file_type,
            ))
        except Exception as exc:
            logger.warning("Could not serialize source ref: %s", exc)
    return refs


# ---------------------------------------------------------------------------
# Session management helpers (debug/dev)
# ---------------------------------------------------------------------------

@router.get("/sessions", summary="List active conversation sessions")
async def list_sessions(request: Request, engine=Depends(get_engine)):
    memory = engine._memory
    return {
        "sessions": memory.list_sessions(),
        "count": memory.session_count(),
    }


@router.delete("/sessions/{session_id}", summary="Clear a conversation session")
async def clear_session(session_id: str, request: Request, engine=Depends(get_engine)):
    memory = engine._memory
    deleted = memory.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return {"deleted": session_id}
