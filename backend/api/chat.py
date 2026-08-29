"""
backend/api/chat.py
-------------------
Chat API route — Phase 5 (with RAG + Tools + Multimodal support).

Endpoints:
  POST /api/chat           — SSE streaming (JSON) or JSON response  [Phase 1-4, UNCHANGED]
  POST /api/chat/multimodal — Multipart SSE with optional image     [Phase 5 NEW]
  GET  /api/chat/sessions  — List active sessions (debug helper)
  DELETE /api/chat/sessions/{session_id} — Clear a session

SSE stream format (identical for both endpoints):
  data: {"type": "agent_status", "content": "analyzing_image"}
  data: {"type": "agent_status", "content": "thinking"}
  data: {"type": "tool_start",   "content": "", "tool": "calculator", ...}
  data: {"type": "tool_result",  "content": "", "tool": "calculator", "success": true, ...}
  data: {"type": "delta",        "content": "Hello"}
  data: {"type": "sources",      "content": "", "sources": [...]}
  data: {"type": "done",         "content": "", "session_id": "...", "model_used": "..."}
  data: {"type": "error",        "content": "Error message"}

Backward compatibility:
  - /api/chat remains 100% unchanged — JSON body, existing SSE events
  - /api/chat/multimodal is a new additive endpoint
  - 'sources' event is omitted if no documents are indexed
  - tools_enabled=false reverts to Phase 3 behavior
"""

from __future__ import annotations

import logging
import uuid
from typing import AsyncGenerator, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse

from backend.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatMessage,
    ImageAttachment,
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
# POST /api/chat  (Phase 1–4, UNCHANGED)
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
            # Phase 6: Deterministic complexity heuristic — decide BEFORE execution
            from backend.agent.planner import should_use_planning
            use_planning = should_use_planning(
                message=body.message,
                planning_enabled=body.planning_enabled if body.planning_enabled is not None else True,
                tools_enabled=True,
            )
            if use_planning and hasattr(engine, '_task_manager') and engine._task_manager is not None:
                return StreamingResponse(
                    _stream_sse_with_planning(engine, session_id, body.message, body.model, model_used),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    },
                )
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
# POST /api/chat/multimodal  (Phase 5 NEW)
# ---------------------------------------------------------------------------

@router.post("/multimodal", summary="Send a message with optional image attachment (Phase 5)")
async def chat_multimodal(
    request: Request,
    engine=Depends(get_engine),
    model_router=Depends(get_router_obj),
    message: str = Form(..., min_length=1, max_length=32_000),
    session_id: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
    stream: bool = Form(default=True),
    tools_enabled: bool = Form(default=True),
    image: Optional[UploadFile] = File(default=None),
):
    """
    Send a multipart message with optional image attachment.

    When image is present:
      - Image is validated (extension, MIME, size, dimensions)
      - Two-step vision: llava:7b analyzes image → qwen2.5:7b reasons + tools
      - SSE emits: agent_status(analyzing_image), then normal tool/delta/done events

    When image is absent:
      - Identical behavior to POST /api/chat (text-only path)

    Returns the same SSE format as /api/chat.
    """
    from backend.multimodal.image_processor import ImageProcessor, ImageValidationError

    resolved_session_id = session_id or str(uuid.uuid4())

    provider_name, model_name = model_router.resolve_model(model)
    model_used = f"{provider_name}/{model_name}"

    logger.info(
        "multimodal_request | session=%s model=%s stream=%s tools=%s has_image=%s",
        resolved_session_id, model_used, stream, tools_enabled, image is not None,
    )

    # ---- Process image if provided ----
    image_b64: Optional[str] = None
    attachment_meta: Optional[ImageAttachment] = None

    if image is not None and image.filename:
        # Read upload
        try:
            image_data = await image.read()
        except Exception as exc:
            logger.error("Failed to read uploaded image: %s", exc)
            raise HTTPException(status_code=400, detail="Failed to read uploaded image.")

        # Validate and process
        processor = ImageProcessor(upload_dir=request.app.state.upload_dir)
        try:
            processed = processor.process(
                data=image_data,
                filename=image.filename,
            )
        except ImageValidationError as exc:
            logger.warning("Image validation failed: %s (code=%s)", exc.message, exc.code)
            raise HTTPException(
                status_code=422,
                detail=f"Image validation failed: {exc.message}",
            )
        except Exception as exc:
            logger.error("Unexpected error processing image: %s", exc)
            raise HTTPException(status_code=500, detail="Internal error processing image.")

        image_b64 = processed.base64_data
        attachment_meta = ImageAttachment(
            attachment_id=processed.attachment_id,
            filename=processed.original_filename,
            mime_type=processed.mime_type,
            size_bytes=processed.size_bytes,
            width=processed.width,
            height=processed.height,
        )

        logger.info(
            "image_accepted | id=%s filename=%s mime=%s size=%d",
            processed.attachment_id, processed.original_filename,
            processed.mime_type, processed.size_bytes,
        )

    # ---- Route to appropriate stream path ----
    if image_b64 is not None:
        # Multimodal path — use two-step vision + tool loop
        use_tools = tools_enabled and hasattr(engine, '_tool_registry') and engine._tool_registry is not None
        return StreamingResponse(
            _stream_sse_multimodal(
                engine, resolved_session_id, message, image_b64,
                model, model_used, use_tools, attachment_meta,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
    else:
        # No image provided — fall back to text-only path (identical to /api/chat)
        use_tools = tools_enabled and hasattr(engine, '_tool_registry') and engine._tool_registry is not None
        if use_tools:
            return StreamingResponse(
                _stream_sse_with_tools(engine, resolved_session_id, message, model, model_used),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )
        else:
            return StreamingResponse(
                _stream_sse(engine, resolved_session_id, message, model, model_used),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )


# ---------------------------------------------------------------------------
# SSE generator — Plain (Phase 3 backward compatible, UNCHANGED)
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
# SSE generator — Tool-enabled (Phase 4, UNCHANGED)
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
# SSE generator — Multimodal (Phase 5 NEW)
# ---------------------------------------------------------------------------

async def _stream_sse_multimodal(
    engine,
    session_id: str,
    user_message: str,
    image_b64: str,
    model_id,
    model_used: str,
    use_tools: bool,
    attachment_meta: Optional[ImageAttachment],
) -> AsyncGenerator[str, None]:
    """
    Wrap the multimodal engine stream in SSE format.

    Identical event structure to _stream_sse_with_tools() with the addition
    of agent_status: analyzing_image at the start.
    """
    try:
        sources = []
        engine_stream = engine.chat_stream_with_tools_multimodal(
            session_id, user_message, image_b64, model_id
        ) if use_tools else engine.chat_stream_with_tools_multimodal(
            session_id, user_message, image_b64, model_id
        )

        async for item in engine_stream:
            if isinstance(item, str):
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
                sources = item

        # Sources
        if sources:
            source_refs = _sources_to_refs(sources)
            sources_chunk = StreamChunk(
                type="sources",
                content="",
                sources=source_refs,
            )
            yield f"data: {sources_chunk.model_dump_json()}\n\n"

        # Done
        done_chunk = StreamChunk(
            type="done",
            content="",
            session_id=session_id,
            model_used=model_used,
            attachment=attachment_meta,
        )
        yield f"data: {done_chunk.model_dump_json()}\n\n"

    except RuntimeError as exc:
        logger.error("multimodal stream error | session=%s: %s", session_id, exc)
        error_chunk = StreamChunk(type="error", content=str(exc))
        yield f"data: {error_chunk.model_dump_json()}\n\n"

    except Exception as exc:
        logger.exception("unexpected multimodal stream error | session=%s", session_id)
        error_chunk = StreamChunk(type="error", content="Internal server error")
        yield f"data: {error_chunk.model_dump_json()}\n\n"

# ---------------------------------------------------------------------------
# SSE generator — Planning (Phase 6 NEW)
# ---------------------------------------------------------------------------

async def _stream_sse_with_planning(
    engine,
    session_id: str,
    user_message: str,
    model_id,
    model_used: str,
) -> AsyncGenerator[str, None]:
    """
    Wrap the agent engine planning stream in SSE format.

    Handles all Phase 6 event types alongside existing tool/delta/sources events.

    Phase 6 events:
        plan_created, plan_step, approval_required, approval_granted,
        approval_rejected, task_started, task_completed, task_failed,
        task_cancelled
    """
    import json as _json

    try:
        sources = []

        async for item in engine.run_agent_task(session_id, user_message, model_id):
            if isinstance(item, str):
                # Text delta
                chunk = StreamChunk(type="delta", content=item)
                yield f"data: {chunk.model_dump_json()}\n\n"

            elif isinstance(item, dict):
                event_type = item.get("type", "agent_status")

                # Phase 4 events — reuse existing format
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

                # Phase 6 events — pass through as raw JSON
                elif event_type in (
                    "plan_created", "plan_step",
                    "approval_required", "approval_granted", "approval_rejected",
                    "task_started", "task_completed", "task_failed", "task_cancelled",
                ):
                    yield f"data: {_json.dumps(item)}\n\n"

                elif event_type == "error":
                    chunk = StreamChunk(type="error", content=item.get("content", "Unknown error"))
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
        logger.error("planning stream error | session=%s: %s", session_id, exc)
        error_chunk = StreamChunk(type="error", content=str(exc))
        yield f"data: {error_chunk.model_dump_json()}\n\n"

    except Exception as exc:
        logger.exception("unexpected planning stream error | session=%s", session_id)
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
