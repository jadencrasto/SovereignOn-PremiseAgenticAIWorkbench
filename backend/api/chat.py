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
from contextlib import aclosing
import uuid
from typing import AsyncGenerator, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse as _StreamingResponse
from pydantic import BaseModel, Field

from backend.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatMessage,
    ImageAttachment,
    MessageRole,
    StreamChunk,
    _SourceRef,
)
from backend.auth.dependencies import get_current_user, require_permission
from backend.auth.models import Permission, User, resolve_user_clearance
from backend.security.artifact_presentation import public_tool_arguments, public_tool_summary, public_tool_name, public_agent_status

logger = logging.getLogger(__name__)


def _requires_prior_numeric_receipt(message: str) -> bool:
    """Recognize phrases that require a verified prior result.

    Conversation prose and persisted task summaries are not tool receipts.
    This intentionally narrow guard does not resolve general coreference.
    """
    import re
    if not isinstance(message, str):
        return False
    reference = r'(?:that\s+(?:result|number|answer|value)|(?:the\s+)?previous\s+(?:result|answer|number|value)|the\s+result\s+from\s+before|it)'
    prefix = r'^\s*(?:please\s+)?'
    return bool(re.match(prefix + r'(?:multiply|divide|add|subtract|convert|calculate|compute|use)\s+'
                         + reference + r'\b', message, re.I)
                or re.match(prefix + r'(?:add|subtract)\s+\S+\s+(?:to|from)\s+' + reference + r'\b',
                            message, re.I))


class StreamingResponse(_StreamingResponse):
    """Close generators before request authorization context is released."""
    async def stream_response(self, send):
        try:
            await super().stream_response(send)
        finally:
            close = getattr(self.body_iterator, 'aclose', None)
            if close is not None:
                await close()


router = APIRouter(prefix="/api/chat", tags=["chat"])

class ConversationTitle(BaseModel):
    title: str = Field(min_length=1, max_length=160)


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def get_engine(request: Request):
    return request.app.state.engine


def get_router_obj(request: Request):
    return request.app.state.model_router


def _task_belongs_to_user(task, user: User) -> bool:
    """Chat-based task actions must not cross conversation owners."""
    owner = getattr(task, 'user_id', None)
    return bool(owner and owner == getattr(user, 'id', None))


def _prepare_new_chat_identity(engine, session_id: str, current_user: User):
    """Register a new conversation before any inference or tool dispatch."""
    try:
        return engine._memory.prepare_new_chat(
            session_id, current_user.id, getattr(engine, '_system_prompt', None) or None,
        )
    except PermissionError:
        raise HTTPException(status_code=404, detail='Conversation not found') from None
    except Exception:
        raise HTTPException(status_code=503, detail='Conversation unavailable') from None


# ---------------------------------------------------------------------------
# POST /api/chat  (Phase 1–4, UNCHANGED)
# ---------------------------------------------------------------------------

@router.post("", summary="Send a message and receive a response")
async def chat(
    body: ChatRequest,
    request: Request,
    engine=Depends(get_engine),
    model_router=Depends(get_router_obj),
    current_user: User = Depends(get_current_user),
):
    """
    Send a user message.

    - If `stream=true` (default): returns SSE stream with delta, sources, done events.
    - If `stream=false`: returns JSON with message + sources list.
    - If `tools_enabled=true` (default): agent may use local tools.
    """
    session_id = body.session_id or str(uuid.uuid4())
    from backend.auth.request_context import request_tools_enabled
    request_tools_enabled.set(bool(body.tools_enabled))
    try:
        existed = engine._memory.session_exists(session_id)
        if body.session_id is not None and not existed:
            raise HTTPException(status_code=404, detail='Conversation not found')
        initial_identity = engine._memory.stream_identity(session_id) if existed else None
    except PermissionError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    provider_name, model_name = model_router.resolve_model(body.model)
    model_used = f"{provider_name}/{model_name}"

    if getattr(request.app.state, 'enforce_model_readiness', False):
        from backend.health.routes import _report
        if _report(request.app.state, model_used)['selected_chat_model']['state'] != 'available':
            raise HTTPException(status_code=503, detail='Selected model is not ready for chat')

    def guarded_response(content, **options):
        async def guarded():
            async with aclosing(content) as stream:
                if getattr(request.app.state, 'enforce_model_readiness', False):
                    from backend.health.routes import _report
                    if _report(request.app.state, model_used)['selected_chat_model']['state'] != 'available':
                        yield 'data: {"type":"error","content":"Selected model is not ready for chat","error_code":"MODEL_NOT_READY"}\n\n'
                        return
                async for event in stream:
                    yield event
        return StreamingResponse(guarded(), **options)

    logger.info(
        "chat_request | session=%s user=%s role=%s model=%s stream=%s tools=%s len=%d",
        session_id, current_user.username, current_user.role, model_used, body.stream, body.tools_enabled, len(body.message),
    )

    if _requires_prior_numeric_receipt(body.message):
        from backend.agent.numeric_receipts import resolve_numeric_followup
        try:
            receipt = engine._memory.numeric_receipt(session_id) if initial_identity else None
        except PermissionError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None
        if (receipt is not None and body.stream and body.tools_enabled and body.planning_enabled is not False
                and getattr(engine, '_task_manager', None) is not None):
            try:
                resolve_numeric_followup(body.message, receipt)
            except ValueError:
                pass
            else:
                return guarded_response(
                    _stream_sse_with_planning(
                        engine, session_id, body.message, model_used, model_used,
                        user_role=current_user.role, user_id=current_user.id,
                        guard_initial_identity=initial_identity, prior_numeric_receipt=receipt,
                    ),
                    media_type='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
                )
        detail = 'I cannot verify a single previous result for this request. Please include the number and any units explicitly.'
        if not body.stream:
            raise HTTPException(status_code=409, detail=detail)
        async def unresolved_reference():
            import json
            yield f"data: {json.dumps({'type': 'error', 'content': detail, 'error_code': 'RESULT_REFERENCE_UNAVAILABLE'})}\n\n"
        return guarded_response(unresolved_reference(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    if body.stream:
        # Phase A: Check if an approval is currently pending for a task in this session
        has_tm = hasattr(engine, "_task_manager") and engine._task_manager is not None
        has_am = hasattr(engine, "_approval_manager") and engine._approval_manager is not None
        if has_tm and has_am:
            import json
            import re
            from backend.agent.task import TaskStatus
            clean_msg = body.message.strip().lower()
            clean_msg = re.sub(r"^[^\w]+|[^\w]+$", "", clean_msg)
            is_nlp_approval = bool(re.match(r"^(yes(\s+approve)?|approve[d]?|proceed|continue|confirm|go ahead|looks good)\b", clean_msg))
            is_nlp_rejection = bool(re.match(r"^(no|reject(ed)?|deny|denied|cancel(led)?|stop|do not|don't)\b", clean_msg))

            if is_nlp_approval or is_nlp_rejection:
                pending_task = None
                for t in engine._task_manager.list_tasks(status=TaskStatus.AWAITING_APPROVAL):
                    if t.session_id == session_id and _task_belongs_to_user(t, current_user):
                        pending_task = t
                        break
                if pending_task:
                    pending = engine._approval_manager.get_pending_for_task(pending_task.task_id)
                    if pending:
                        if initial_identity is None:
                            from backend.security.task_access import TASK_RESUME_WITHHELD
                            async def _unbound_approval_denied():
                                yield f"data: {json.dumps({'type': 'error', 'content': TASK_RESUME_WITHHELD})}\n\n"
                            return guarded_response(_unbound_approval_denied(), media_type='text/event-stream')
                        from backend.auth.models import Permission, has_permission
                        if is_nlp_approval and not has_permission(current_user.role, Permission.APPROVE_TASKS):
                            logger.warning(
                                "chat_approval_denied | session=%s task=%s user=%s role=%s",
                                session_id, pending_task.task_id, current_user.username, current_user.role,
                            )
                            @_guard_conversation_stream()
                            async def _stream_denied_approval(engine, session_id):
                                yield f"data: {json.dumps({'type': 'error', 'content': f'Permission denied: role \'{current_user.role}\' cannot approve tasks.'})}\n\n"
                                yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"

                            return guarded_response(
                                _stream_denied_approval(engine, session_id, guard_initial_identity=initial_identity),
                                media_type="text/event-stream",
                                headers={
                                    "Cache-Control": "no-cache",
                                    "X-Accel-Buffering": "no",
                                    "Connection": "keep-alive",
                                },
                            )

                        logger.info(
                            "chat_approval_intercept | session=%s task=%s approval=%s action=%s",
                            session_id, pending_task.task_id, pending.approval_id, "approve" if is_nlp_approval else "reject",
                        )
                        approval_uid = getattr(current_user, "id", None) or getattr(current_user, "username", None)

                        @_guard_conversation_stream()
                        async def _stream_intercepted_approval(engine, session_id):
                            async for item in engine.resume_agent_task(
                                task_id=pending_task.task_id,
                                approval_id=pending.approval_id,
                                approved=is_nlp_approval,
                                user_role=current_user.role,
                                user_id=approval_uid,
                            ):
                                if isinstance(item, str):
                                    yield f"data: {json.dumps({'type': 'delta', 'content': item})}\n\n"
                                elif isinstance(item, dict):
                                    from backend.security.artifact_presentation import public_planner_event
                                    from backend.security.task_access import TASK_RESUME_WITHHELD
                                    if item.get('type') == 'error':
                                        text = TASK_RESUME_WITHHELD if item.get('content') == TASK_RESUME_WITHHELD else 'Task unavailable'
                                        public_item = {'type': 'error', 'content': text}
                                    else:
                                        public_item = public_planner_event(item)
                                    yield f"data: {json.dumps(public_item)}\n\n"
                                    if item.get('type') == 'error':
                                        return
                            yield f"data: {json.dumps({'type': 'done', 'task_id': pending_task.task_id})}\n\n"

                        return guarded_response(
                            _stream_intercepted_approval(engine, session_id, guard_initial_identity=initial_identity),
                            media_type="text/event-stream",
                            headers={
                                "Cache-Control": "no-cache",
                                "X-Accel-Buffering": "no",
                                "Connection": "keep-alive",
                            },
                        )

        if initial_identity is None:
            initial_identity = _prepare_new_chat_identity(engine, session_id, current_user)

        # ---- ROUTING GATE (Tests A-D fixes) ----
        # Order:
        #   1. Arithmetic auto-detection → deterministic answer (no LLM)
        #   2. Approval intercept (above, unchanged)
        #   3. Short conversational follow-up → plain stream (preserves history)
        #   4. General knowledge (no doc refs) → plain stream (no RAG, no tools)
        #   5. should_use_planning → Phase 6 planner
        #   6. Default → tools_tracked stream (tool loop: document_search, file_read, etc.)

        from backend.agent.planner import (
            is_general_knowledge_query, should_use_planning,
            is_short_conversational_followup,
        )

        # 1. Arithmetic auto-detection
        expression = _arithmetic_expression(body.message) if body.tools_enabled else None
        if expression is not None and has_tm and body.planning_enabled is not False:
            return guarded_response(
                _stream_sse_with_planning(
                    engine, session_id, body.message, model_used, model_used,
                    user_role=current_user.role, user_id=current_user.id,
                    guard_initial_identity=initial_identity, numeric_expression=expression,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        # 3. Short conversational follow-ups (e.g. "yes plz", "continue", "tell me more")
        #    Route to plain stream to preserve conversation history context
        if is_short_conversational_followup(body.message):
            logger.info("conversational_followup | session=%s msg=%s", session_id, body.message[:50])
            return guarded_response(
                _stream_sse(engine, session_id, body.message, model_used, model_used, resolve_user_clearance(current_user), guard_initial_identity=initial_identity),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        # 4. General knowledge check
        is_general = is_general_knowledge_query(body.message)

        # Choose tool-enabled or plain streaming
        use_tools = body.tools_enabled and hasattr(engine, '_tool_registry') and engine._tool_registry is not None and not is_general
        if use_tools:
            # 5. Phase 6: Deterministic complexity heuristic — decide BEFORE execution
            use_planning = should_use_planning(
                message=body.message,
                planning_enabled=body.planning_enabled if body.planning_enabled is not None else True,
                tools_enabled=True,
            )
            if use_planning and hasattr(engine, '_task_manager') and engine._task_manager is not None:
                user_id = getattr(current_user, "id", None) or getattr(current_user, "username", None)
                return guarded_response(
                    _stream_sse_with_planning(engine, session_id, body.message, model_used, model_used, user_role=current_user.role, user_id=user_id, guard_initial_identity=initial_identity),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    },
                )

            # 6. Default: Phase 6 unified — route through tracked wrapper when TaskManager is wired
            has_task_manager = hasattr(engine, '_task_manager') and engine._task_manager is not None
            if has_task_manager:
                user_id = getattr(current_user, "id", None) or getattr(current_user, "username", None)
                return guarded_response(
                    _stream_sse_with_tools_tracked(engine, session_id, body.message, model_used, model_used, user_role=current_user.role, user_id=user_id, guard_initial_identity=initial_identity),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    },
                )
            user_id = getattr(current_user, "id", None) or getattr(current_user, "username", None)
            return guarded_response(
                _stream_sse_with_tools(engine, session_id, body.message, model_used, model_used, user_role=current_user.role, user_id=user_id, guard_initial_identity=initial_identity),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )
        else:
            return guarded_response(
                _stream_sse(engine, session_id, body.message, model_used, model_used, resolve_user_clearance(current_user), guard_initial_identity=initial_identity),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

    # Non-streaming path
    if initial_identity is None:
        initial_identity = _prepare_new_chat_identity(engine, session_id, current_user)
    try:
        content, sources = await engine.chat(session_id, body.message, model_used, user_clearance=resolve_user_clearance(current_user))
    except RuntimeError as exc:
        logger.error("Chat generation failed")
        safe_errors = {"Selected model is unavailable", "Model request timed out", "Model provider unavailable", "Invalid model response", "Incomplete model response", "Model response interrupted"}
        detail = str(exc) if str(exc) in safe_errors else "Chat generation unavailable"
        raise HTTPException(status_code=503, detail=detail) from None

    source_refs = _sources_to_refs(sources)
    try:
        current_identity = engine._memory.stream_identity(session_id)
        if initial_identity is not None and current_identity != initial_identity:
            raise PermissionError('Conversation unavailable')
    except PermissionError:
        raise HTTPException(status_code=404, detail='Conversation not found') from None
    return ChatResponse(
        session_id=session_id,
        message=ChatMessage(role=MessageRole.assistant, content=content),
        model_used=model_used,
        sources=source_refs if source_refs else None,
    )


# ---------------------------------------------------------------------------
# POST /api/chat/multimodal  (Phase 5 NEW)
# ---------------------------------------------------------------------------

@router.post("/multimodal", summary="Send a multimodal message with image attachment")
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
    current_user: User = Depends(get_current_user),
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
    from backend.multimodal.image_processor import ImageProcessor, ImageValidationError, DEFAULT_MAX_SIZE_BYTES

    resolved_session_id = session_id or str(uuid.uuid4())
    from backend.auth.request_context import request_tools_enabled
    request_tools_enabled.set(bool(tools_enabled))
    try:
        existed = engine._memory.session_exists(resolved_session_id)
        if session_id is not None and not existed:
            raise HTTPException(status_code=404, detail='Conversation not found')
        initial_identity = engine._memory.stream_identity(resolved_session_id) if existed else _prepare_new_chat_identity(engine, resolved_session_id, current_user)
    except PermissionError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    provider_name, model_name = model_router.resolve_model(model)
    model_used = f"{provider_name}/{model_name}"

    logger.info(
        "multimodal_request | session=%s user=%s role=%s model=%s stream=%s tools=%s has_image=%s",
        resolved_session_id, current_user.username, current_user.role, model_used, stream, tools_enabled, image is not None,
    )

    # ---- Process image if provided ----
    image_b64: Optional[str] = None
    attachment_meta: Optional[ImageAttachment] = None

    if image is not None and not image.filename:
        raise HTTPException(status_code=422, detail="Image filename is required.")
    if image is not None and image.filename:
        # Read upload
        try:
            image_data = await image.read(DEFAULT_MAX_SIZE_BYTES + 1)
        except Exception as exc:
            logger.error("Failed to read uploaded image: %s", exc)
            raise HTTPException(status_code=400, detail="Failed to read uploaded image.")

        # Validate and process
        processor = ImageProcessor(upload_dir=request.app.state.upload_dir)
        try:
            processed = processor.process(
                data=image_data,
                filename=image.filename,
                save_to_disk=False,
            )
        except ImageValidationError as exc:
            logger.warning("Image validation failed")
            raise HTTPException(
                status_code=422,
                detail="Image validation failed. Check the image format and size.",
                headers={"X-Error-Code": "IMAGE_INVALID"},
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
    user_id = getattr(current_user, "id", None) or getattr(current_user, "username", None)
    if image_b64 is not None:
        # Multimodal path — use two-step vision + tool loop
        use_tools = tools_enabled and hasattr(engine, '_tool_registry') and engine._tool_registry is not None
        return StreamingResponse(
            _stream_sse_multimodal(
                engine, resolved_session_id, message, image_b64,
                model, model_used, use_tools, attachment_meta,
                user_role=current_user.role,
                user_id=user_id,
                guard_initial_identity=initial_identity,
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
                _stream_sse_with_tools(engine, resolved_session_id, message, model, model_used, user_role=current_user.role, user_id=user_id, guard_initial_identity=initial_identity),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )
        else:
            return StreamingResponse(
                _stream_sse(engine, resolved_session_id, message, model, model_used, resolve_user_clearance(current_user), guard_initial_identity=initial_identity),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )


# ---------------------------------------------------------------------------
# Arithmetic auto-detection (Test A1/A2)
# ---------------------------------------------------------------------------

def _arithmetic_expression(message: str) -> Optional[str]:
    """
    Recognize simple arithmetic expressions for the validated calculator plan.

    Returns the expression, or None if the message
    is not a simple arithmetic query.

    Handles patterns like:
      - "What is 25 + 17?"
      - "calculate 125 * 840 * 1.18"
      - "25 + 17"
      - "What is 3.14 * 2?"

    """
    import re as _re

    if not message or len(message) > 200:
        return None

    q = message.strip()
    q_lower = q.lower()

    # Pattern 1: "What is <expr>?" / "What's <expr>?"
    m = _re.match(
        r"^(?:what(?:'s|\s+is)\s+)([\d\s\+\-\*\/\.\(\)\^%x×]+)\s*\??$",
        q_lower,
    )
    expr = None
    if m:
        expr = m.group(1).strip()

    # Pattern 2: "calculate <expr>" / "compute <expr>"
    if expr is None:
        m = _re.match(
            r"^(?:calculate|compute|eval(?:uate)?)\s+([\d\s\+\-\*\/\.\(\)\^%x×]+)\s*\??$",
            q_lower,
        )
        if m:
            expr = m.group(1).strip()

    # Pattern 3: Pure arithmetic expression (e.g. "25 + 17", "3 * 4.5")
    if expr is None:
        m = _re.match(
            r"^([\d\s\+\-\*\/\.\(\)\^%x×]+)$",
            q.strip(),
        )
        if m:
            expr = m.group(1).strip()

    if expr is None or not expr:
        return None

    # Normalize: replace × with *, x with * (when between digits)
    expr = _re.sub(r"×", "*", expr)
    expr = _re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", expr)
    expr = _re.sub(r"\^", "**", expr)

    # Must contain at least one operator and one digit
    if not _re.search(r"[\+\-\*\/\%]", expr):
        return None
    if not _re.search(r"\d", expr):
        return None

    return expr


def _guard_conversation_stream(*, engine_index: int = 0, session_index: int = 1):
    """Recheck conversation access before releasing each generated SSE event."""
    def decorate(function):
        async def guarded(*args, **kwargs):
            engine = args[engine_index] if len(args) > engine_index else kwargs['engine']
            session_id = args[session_index] if len(args) > session_index else kwargs['session_id']
            expected_identity = kwargs.pop('guard_initial_identity', None)
            stream = function(*args, **kwargs)
            identity = expected_identity
            try:
                try:
                    if engine._memory.session_exists(session_id):
                        current = engine._memory.stream_identity(session_id)
                        if identity is not None and current != identity:
                            raise PermissionError('Conversation unavailable')
                        identity = current
                except PermissionError:
                    yield 'data: {"type":"error","content":"Conversation unavailable","error_code":"CONVERSATION_UNAVAILABLE"}\n\n'
                    return
                while True:
                    try:
                        if identity is not None and engine._memory.stream_identity(session_id) != identity:
                            raise PermissionError('Conversation unavailable')
                        try:
                            event = await stream.__anext__()
                        except StopAsyncIteration:
                            return
                        current = engine._memory.stream_identity(session_id)
                        if identity is not None and current != identity:
                            raise PermissionError('Conversation unavailable')
                        identity = current
                    except PermissionError:
                        yield 'data: {"type":"error","content":"Conversation unavailable","error_code":"CONVERSATION_UNAVAILABLE"}\n\n'
                        return
                    yield event
            finally:
                await stream.aclose()
        return guarded
    return decorate


@_guard_conversation_stream(engine_index=4, session_index=0)
async def _stream_arithmetic_result(
    session_id: str,
    user_message: str,
    result: str,
    model_used: str,
    engine,
) -> AsyncGenerator[str, None]:
    """
    Stream a deterministic arithmetic result as SSE events.
    Also stores the exchange in conversation memory.
    """
    # Store in conversation memory for context continuity
    engine._ensure_session(session_id)
    engine._memory.add_user_message(session_id, user_message)

    answer = f"**{result}**"
    engine._memory.add_assistant_message(session_id, answer, access_provenance={
        'version': 1, 'kind': 'deterministic_calculation', 'sources': [],
    })

    # This is a direct deterministic calculation, not a registry tool call.

    # Emit the answer
    chunk = StreamChunk(type="delta", content=answer)
    yield f"data: {chunk.model_dump_json()}\n\n"

    # Done
    done_chunk = StreamChunk(
        type="done",
        content="",
        session_id=session_id,
        model_used=model_used,
    )
    yield f"data: {done_chunk.model_dump_json()}\n\n"


# ---------------------------------------------------------------------------
# SSE generator — Plain (Phase 3 backward compatible, UNCHANGED)
# ---------------------------------------------------------------------------

@_guard_conversation_stream()
async def _stream_sse(
    engine,
    session_id: str,
    user_message: str,
    model_id,
    model_used: str,
    user_clearance: str = "viewer",
) -> AsyncGenerator[str, None]:
    """
    Wrap the agent engine stream in SSE format.

    The engine yields str deltas followed by a list sentinel (the sources).
    """
    try:
        sources = []
        async with aclosing(engine.chat_stream(session_id, user_message, model_id, user_clearance=user_clearance)) as engine_stream:
            async for item in engine_stream:
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
        safe_provider_errors = {"Selected model is unavailable", "Model request timed out", "Model provider unavailable", "Invalid model response", "Incomplete model response", "Model response interrupted"}
        message = str(exc) if str(exc) in safe_provider_errors else "Operation unavailable"
        error_chunk = StreamChunk(type="error", content=message, error_code="OPERATION_UNAVAILABLE")
        yield f"data: {error_chunk.model_dump_json()}\n\n"

    except Exception as exc:
        logger.exception("unexpected stream error | session=%s", session_id)
        error_chunk = StreamChunk(type="error", content="Internal server error", error_code="INTERNAL_SERVER_ERROR")
        yield f"data: {error_chunk.model_dump_json()}\n\n"


# ---------------------------------------------------------------------------
# SSE generator — Tool-enabled (Phase 4, UNCHANGED)
# ---------------------------------------------------------------------------

@_guard_conversation_stream()
async def _stream_sse_with_tools(
    engine,
    session_id: str,
    user_message: str,
    model_id,
    model_used: str,
    user_role: Optional[str] = None,
    user_id: Optional[str] = None,
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

        async for item in engine.chat_stream_with_tools(session_id, user_message, model_id, user_role=user_role, user_id=user_id):
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
                        tool=public_tool_name(item.get("tool")),
                        tool_args=public_tool_arguments(item.get("tool"), item.get("arguments", {})),
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"

                elif event_type == "tool_result":
                    chunk = StreamChunk(
                        type="tool_result",
                        content="",
                        tool=public_tool_name(item.get("tool")),
                        success=item.get("success", False),
                        summary=public_tool_summary(item.get("tool"), item.get("summary", ""), item.get("success", False)),
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"

                elif event_type == "agent_status":
                    chunk = StreamChunk(
                        type="agent_status",
                        content=public_agent_status(item.get("status")),
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
        error_chunk = StreamChunk(type="error", content="Operation unavailable", error_code="OPERATION_UNAVAILABLE")
        yield f"data: {error_chunk.model_dump_json()}\n\n"

    except Exception as exc:
        logger.exception("unexpected tool stream error | session=%s", session_id)
        error_chunk = StreamChunk(type="error", content="Internal server error", error_code="INTERNAL_SERVER_ERROR")
        yield f"data: {error_chunk.model_dump_json()}\n\n"


# ---------------------------------------------------------------------------
# SSE generator — Tracked tool-enabled (Phase 6 unified)
# ---------------------------------------------------------------------------

@_guard_conversation_stream()
async def _stream_sse_with_tools_tracked(
    engine,
    session_id: str,
    user_message: str,
    model_id,
    model_used: str,
    user_role: Optional[str] = None,
    user_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    SSE generator that wraps engine.chat_stream_with_tools() DIRECTLY
    and creates lightweight persistent task records via TaskManager.

    Task tracking logic lives here (not in the engine) because the SSE
    layer has full visibility of ALL event types, including the sources
    sentinel needed for RAG task tracking.

    Task creation triggers:
      - First tool_start event  → tool-based task (calculator, etc.)
      - Non-empty sources list  → RAG-based task (document retrieval)
      - No tools AND no sources → ordinary conversation, NO task created

    All original SSE event semantics are preserved identically to
    _stream_sse_with_tools — text deltas, tool events, sources, done/error.
    """
    import json as _json  # noqa: F811 — kept for potential debug use

    from backend.agent.planner import AgentPlan, PlanStep, StepStatus

    task_manager = getattr(engine, '_task_manager', None)

    # Task tracking state
    task_id: Optional[str] = None
    task_created = False
    plan_steps: list = []
    step_counter = 0
    current_tool_step_id: Optional[str] = None
    any_tool_failed = False
    final_text_parts: list = []

    def _create_task_if_needed() -> None:
        """Create the task on first execution indicator."""
        nonlocal task_id, task_created
        if task_created or task_manager is None:
            return
        task = task_manager.create_task(
            session_id=session_id,
            user_request=user_message[:500],
            user_id=user_id,
            user_role=user_role,
        )
        task_id = task.task_id
        task_created = True
        task_manager.update_status(task_id, "planning")
        task_manager.update_status(task_id, "executing")

    try:
        sources: list = []

        # Check cancellation before resuming a generator that may dispatch a tool.
        async with aclosing(engine.chat_stream_with_tools(session_id, user_message, model_id, user_role=user_role, user_id=user_id)) as engine_stream:
            while True:
                if task_created and task_id and task_manager.get_task(task_id).status == 'cancelled':
                    yield f"data: {StreamChunk(type='task_cancelled', content='', task_id=task_id).model_dump_json()}\n\n"
                    return
                try:
                    item = await engine_stream.__anext__()
                except StopAsyncIteration:
                    break
                if task_created and task_id and task_manager.get_task(task_id).status == 'cancelled':
                    yield f"data: {StreamChunk(type='task_cancelled', content='', task_id=task_id).model_dump_json()}\n\n"
                    return
                if isinstance(item, str):
                    # Text delta — stream to client and buffer for task result
                    final_text_parts.append(item)
                    chunk = StreamChunk(type="delta", content=item)
                    yield f"data: {chunk.model_dump_json()}\n\n"

                elif isinstance(item, dict):
                    event_type = item.get("type", "agent_status")

                    if event_type == "tool_start":
                        # Create task on first tool execution
                        _create_task_if_needed()
                        if task_created and task_id:
                            # Emit task_started to frontend on first tool
                            if step_counter == 0:
                                task_evt = StreamChunk(type="task_started", content=task_id, task_id=task_id, session_id=session_id)
                                yield f"data: {task_evt.model_dump_json()}\n\n"

                            # Create a plan step for this tool
                            step_counter += 1
                            step_id = f"step_{step_counter}"
                            tool_name = item.get("tool", "unknown")
                            tool_args = item.get("arguments", {})

                            plan_step = PlanStep(
                                id=step_id,
                                description=f"Execute {tool_name}",
                                tool_name=tool_name,
                                arguments=tool_args if isinstance(tool_args, dict) else {},
                                requires_approval=False,
                                status=StepStatus.running.value,
                            )
                            plan_steps.append(plan_step)
                            current_tool_step_id = step_id

                            plan = AgentPlan(
                                task_id=task_id,
                                objective=user_message[:300],
                                steps=list(plan_steps),
                                status="executing",
                            )
                            task_manager.set_plan(task_id, plan)

                        # Forward tool_start to frontend
                        chunk = StreamChunk(
                            type="tool_start",
                            content="",
                            tool=public_tool_name(item.get("tool")),
                            tool_args=public_tool_arguments(item.get("tool"), item.get("arguments", {})),
                        )
                        yield f"data: {chunk.model_dump_json()}\n\n"

                    elif event_type == "tool_result":
                        # Update step status in task
                        if task_created and task_id and current_tool_step_id:
                            success = item.get("success", False)
                            summary = public_tool_summary(item.get("tool"), item.get("summary", ""), success)
                            if success:
                                task_manager.update_step_status(
                                    task_id, current_tool_step_id,
                                    StepStatus.completed.value,
                                    result=summary[:500] if summary else "Completed",
                                )
                            else:
                                any_tool_failed = True
                                task_manager.update_step_status(
                                    task_id, current_tool_step_id,
                                    StepStatus.failed.value,
                                    error=summary[:500] if summary else "Failed",
                                )
                            # Keep the local final-plan snapshot in sync with the persisted step.
                            matching = next((step for step in plan_steps if step.id == current_tool_step_id), None)
                            if matching:
                                matching.status = StepStatus.completed.value if success else StepStatus.failed.value
                                if success:
                                    matching.result = summary[:500] if summary else 'Completed'
                                else:
                                    matching.error = summary[:500] if summary else 'Failed'
                            current_tool_step_id = None

                        # Forward tool_result to frontend
                        chunk = StreamChunk(
                            type="tool_result",
                            content="",
                            tool=public_tool_name(item.get("tool")),
                            success=item.get("success", False),
                            summary=public_tool_summary(item.get("tool"), item.get("summary", ""), item.get("success", False)),
                        )
                        yield f"data: {chunk.model_dump_json()}\n\n"

                    elif event_type == "agent_status":
                        chunk = StreamChunk(
                            type="agent_status",
                            content=public_agent_status(item.get("status")),
                        )
                        yield f"data: {chunk.model_dump_json()}\n\n"

                elif isinstance(item, list):
                    # Sources sentinel
                    sources = item

        if task_created and task_id and current_tool_step_id:
            any_tool_failed = True
            task_manager.update_step_status(task_id, current_tool_step_id, StepStatus.failed.value, error='Tool result unavailable')
            matching = next((step for step in plan_steps if step.id == current_tool_step_id), None)
            if matching:
                matching.status = StepStatus.failed.value
                matching.error = 'Tool result unavailable'

        # ---------------------------------------------------------------
        # Post-stream: RAG task creation (if no tool_start created one)
        # ---------------------------------------------------------------
        if sources and not task_created and task_manager is not None:
            _create_task_if_needed()
            if task_created and task_id:
                # Add a "Document retrieval" step for RAG
                step_counter += 1
                retrieval_step = PlanStep(
                    id=f"step_{step_counter}",
                    description=f"Document retrieval ({len(sources)} sources)",
                    tool_name="document_search",
                    requires_approval=False,
                    status=StepStatus.completed.value,
                    result=f"Retrieved {len(sources)} document chunks",
                )
                plan_steps.append(retrieval_step)

                # Emit task_started for frontend
                task_evt = StreamChunk(type="task_started", content=task_id, task_id=task_id, session_id=session_id)
                yield f"data: {task_evt.model_dump_json()}\n\n"

        # ---------------------------------------------------------------
        # Post-stream: Finalize task
        # ---------------------------------------------------------------
        if task_created and task_id and task_manager is not None:
            # Add a final "Generate response" step
            step_counter += 1
            response_step = PlanStep(
                id=f"step_{step_counter}",
                description="Generate response",
                tool_name=None,
                requires_approval=False,
                status=StepStatus.completed.value,
            )
            plan_steps.append(response_step)

            # Re-fetch task state before finalization to avoid racing cancellation
            from backend.agent.task import TaskStateError
            fresh_task = task_manager.get_task(task_id)
            if fresh_task and (fresh_task.status == "cancelled" or getattr(fresh_task.status, "value", None) == "cancelled"):
                logger.info("tracked_task_cancelled | task=%s was cancelled before finalization", task_id)
            else:
                # Persist the final plan
                final_plan = AgentPlan(
                    task_id=task_id,
                    objective=user_message[:300],
                    steps=list(plan_steps),
                    status="completed" if not any_tool_failed else "failed",
                )
                task_manager.set_plan(task_id, final_plan)

                # Finalize task status
                full_result = "".join(final_text_parts)
                if any_tool_failed:
                    try:
                        task_manager.update_status(
                            task_id, "failed",
                            error="One or more tool executions failed",
                        )
                        task_fail = StreamChunk(type="task_failed", content=task_id, task_id=task_id, session_id=session_id)
                        yield f"data: {task_fail.model_dump_json()}\n\n"
                    except TaskStateError:
                        fresh_after = task_manager.get_task(task_id)
                        if fresh_after and (fresh_after.status == "cancelled" or getattr(fresh_after.status, "value", None) == "cancelled"):
                            logger.info("tracked_task_cancelled_race | task=%s cancelled during finalization", task_id)
                        else:
                            raise
                else:
                    try:
                        task_manager.update_status(
                            task_id, "completed",
                            result=full_result[:1000] if full_result else "Completed",
                        )
                        task_done = StreamChunk(type="task_completed", content=task_id, task_id=task_id, session_id=session_id)
                        yield f"data: {task_done.model_dump_json()}\n\n"
                    except TaskStateError:
                        fresh_after = task_manager.get_task(task_id)
                        if fresh_after and (fresh_after.status == "cancelled" or getattr(fresh_after.status, "value", None) == "cancelled"):
                            logger.info("tracked_task_cancelled_race | task=%s cancelled during finalization", task_id)
                        else:
                            raise

                logger.info(
                    "tracked_task_done | task=%s steps=%d failed=%s",
                    task_id, step_counter, any_tool_failed,
                )

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
        logger.error("tracked tool stream error | session=%s: %s", session_id, exc)
        error_chunk = StreamChunk(type="error", content="Operation unavailable", error_code="OPERATION_UNAVAILABLE")
        yield f"data: {error_chunk.model_dump_json()}\n\n"

    except Exception as exc:
        logger.exception("unexpected tracked tool stream error | session=%s", session_id)
        error_chunk = StreamChunk(type="error", content="Internal server error", error_code="INTERNAL_SERVER_ERROR")
        yield f"data: {error_chunk.model_dump_json()}\n\n"
    finally:
        if task_created and task_id and task_manager is not None:
            try:
                task = task_manager.get_task(task_id)
                if task and task.status in ('pending', 'planning', 'executing'):
                    task_manager.update_status(task_id, 'failed_interrupted', error='Execution interrupted; outcomes may require review')
            except Exception:
                logger.exception('Unable to record interrupted tracked task')



# ---------------------------------------------------------------------------
# SSE generator — Multimodal (Phase 5 NEW)
# ---------------------------------------------------------------------------

@_guard_conversation_stream()
async def _stream_sse_multimodal(
    engine,
    session_id: str,
    user_message: str,
    image_b64: str,
    model_id,
    model_used: str,
    use_tools: bool,
    attachment_meta: Optional[ImageAttachment],
    user_role: Optional[str] = None,
    user_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    Wrap the multimodal engine stream in SSE format.

    Identical event structure to _stream_sse_with_tools() with the addition
    of agent_status: analyzing_image at the start.
    """
    try:
        sources = []
        engine_stream = engine.chat_stream_with_tools_multimodal(
            session_id, user_message, image_b64, model_id, user_role=user_role, user_id=user_id,
            tools_enabled=use_tools,
            attachment_descriptor=attachment_meta.model_dump() if attachment_meta else None,
        )

        async with aclosing(engine_stream):
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
                            tool=public_tool_name(item.get("tool")),
                            tool_args=public_tool_arguments(item.get("tool"), item.get("arguments", {})),
                        )
                        yield f"data: {chunk.model_dump_json()}\n\n"

                    elif event_type == "tool_result":
                        chunk = StreamChunk(
                            type="tool_result",
                            content="",
                            tool=public_tool_name(item.get("tool")),
                            success=item.get("success", False),
                            summary=public_tool_summary(item.get("tool"), item.get("summary", ""), item.get("success", False)),
                        )
                        yield f"data: {chunk.model_dump_json()}\n\n"

                    elif event_type == "agent_status":
                        chunk = StreamChunk(
                            type="agent_status",
                            content=public_agent_status(item.get("status")),
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
        error_chunk = StreamChunk(type="error", content="Operation unavailable", error_code="OPERATION_UNAVAILABLE")
        yield f"data: {error_chunk.model_dump_json()}\n\n"

    except Exception as exc:
        logger.exception("unexpected multimodal stream error | session=%s", session_id)
        error_chunk = StreamChunk(type="error", content="Internal server error", error_code="INTERNAL_SERVER_ERROR")
        yield f"data: {error_chunk.model_dump_json()}\n\n"

# ---------------------------------------------------------------------------
# SSE generator — Planning (Phase 6 NEW)
# ---------------------------------------------------------------------------

@_guard_conversation_stream()
async def _stream_sse_with_planning(
    engine,
    session_id: str,
    user_message: str,
    model_id,
    model_used: str,
    user_role: Optional[str] = None,
    user_id: Optional[str] = None,
    *,
    numeric_expression=None,
    prior_numeric_receipt=None,
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

        numeric_options = {}
        if numeric_expression is not None or prior_numeric_receipt is not None:
            numeric_options = dict(numeric_expression=numeric_expression, prior_numeric_receipt=prior_numeric_receipt)
        async with aclosing(engine.run_agent_task(session_id, user_message, model_id, user_role=user_role, user_id=user_id, **numeric_options)) as engine_stream:
            async for item in engine_stream:
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
                            tool=public_tool_name(item.get("tool")),
                            tool_args=public_tool_arguments(item.get("tool"), item.get("arguments", {})),
                        )
                        yield f"data: {chunk.model_dump_json()}\n\n"

                    elif event_type == "tool_result":
                        chunk = StreamChunk(
                            type="tool_result",
                            content="",
                            tool=public_tool_name(item.get("tool")),
                            success=item.get("success", False),
                            summary=public_tool_summary(item.get("tool"), item.get("summary", ""), item.get("success", False)),
                        )
                        yield f"data: {chunk.model_dump_json()}\n\n"

                    elif event_type == "agent_status":
                        chunk = StreamChunk(
                            type="agent_status",
                            content=public_agent_status(item.get("status")),
                        )
                        yield f"data: {chunk.model_dump_json()}\n\n"

                    # Phase 6 planning & approval events — serialize dict directly
                    elif event_type in (
                        "plan_created", "plan_step", "approval_required",
                        "approval_granted", "approval_rejected", "task_started",
                        "task_completed", "task_failed", "task_cancelled",
                    ):
                        from backend.security.artifact_presentation import public_planner_event
                        yield f"data: {_json.dumps(public_planner_event(item))}\n\n"

                    elif event_type == "error":
                        chunk = StreamChunk(type="error", content="Operation unavailable", error_code="OPERATION_UNAVAILABLE")
                        yield f"data: {chunk.model_dump_json()}\n\n"
                        return

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
        error_chunk = StreamChunk(type="error", content="Operation unavailable", error_code="OPERATION_UNAVAILABLE")
        yield f"data: {error_chunk.model_dump_json()}\n\n"

    except Exception as exc:
        logger.exception("unexpected planning stream error | session=%s", session_id)
        error_chunk = StreamChunk(type="error", content="Internal server error", error_code="INTERNAL_SERVER_ERROR")
        yield f"data: {error_chunk.model_dump_json()}\n\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sources_to_refs(sources: List) -> List[_SourceRef]:
    """Convert RetrievedChunk objects to _SourceRef schema objects with bounded deduplication."""
    refs = []
    seen_chunks = set()
    doc_counts = {}

    for s in sources:
        try:
            cid = getattr(s, "chunk_id", None) or f"{getattr(s, 'filename', 'doc')}_{getattr(s, 'chunk_index', 0)}"
            if cid in seen_chunks:
                continue
            seen_chunks.add(cid)

            doc_key = getattr(s, "document_id", None) or getattr(s, "filename", "unknown")
            if doc_counts.get(doc_key, 0) >= 2:
                continue
            doc_counts[doc_key] = doc_counts.get(doc_key, 0) + 1

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

@router.post("/sessions", status_code=201, summary="Create an owned conversation")
async def create_conversation(engine=Depends(get_engine), current_user: User = Depends(get_current_user)):
    session_id = str(uuid.uuid4())
    _prepare_new_chat_identity(engine, session_id, current_user)
    return {"session_id": session_id}


@router.get("/sessions", summary="List active conversation sessions")
async def list_sessions(
    request: Request,
    engine=Depends(get_engine),
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    sessions = engine._memory.list_sessions()
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/legacy-export-policy", summary="Check local legacy export availability")
async def legacy_export_policy(
    request: Request,
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    # Browser records have no verifiable owner. Only the unauthenticated local
    # principal may offer an explicit export; no account receives an import.
    local_mode = not request.app.state.settings.auth_enabled
    has_credentials = bool(request.headers.get('authorization') or request.cookies.get('session_token'))
    return {"allowed": bool(local_mode and current_user.id == 'user_dev_admin' and not has_credentials)}


@router.get("/sessions/{session_id}", summary="Read an owned conversation")
async def read_session(
    session_id: str,
    engine=Depends(get_engine),
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    try:
        if session_id not in engine._memory.list_sessions():
            raise HTTPException(status_code=404, detail="Conversation not found")
        identity = engine._memory.stream_identity(session_id)
        messages = engine._memory.get_history(session_id)
        title = engine._memory.get_title(session_id)
        created_at, updated_at = engine._memory.get_timestamps(session_id)
    except (PermissionError, KeyError):
        raise HTTPException(status_code=404, detail="Conversation not found")
    from backend.agent.memory import safe_tool_history
    from backend.security.conversation_access import may_read_assistant
    # Only server-owned task records can contribute read-only recovery state.
    # Never return approval arguments, hashes or executable authority here.
    from datetime import datetime, timezone
    tasks = []
    manager = getattr(engine, '_task_manager', None)
    approvals = getattr(engine, '_approval_manager', None)
    if manager and approvals:
        for task in manager.list_tasks(limit=200, user_id=current_user.id):
            if task.session_id != session_id or task.user_id != current_user.id:
                continue
            states = []
            for approval in approvals.get_approvals_for_task(task.task_id):
                state = approval.status
                if state in {'pending', 'approved'}:
                    try:
                        expiry = datetime.fromisoformat(approval.expires_at)
                        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                            state = 'expired'
                    except (ValueError, TypeError):
                        state = 'expired'
                if state in {'pending', 'approved', 'consumed', 'rejected', 'expired'}:
                    states.append(state)
            tasks.append({'task_id': task.task_id, 'status': getattr(task.status, 'value', task.status),
                          'approval_statuses': states[:32]})
    clearance = resolve_user_clearance(current_user)
    doc_service = getattr(engine, '_doc_service', None)
    visible_messages = []
    for message in messages:
        if message.role == 'system':
            continue
        allowed = (message.role != 'assistant' or engine._memory.verify_independent_plain_chat(session_id, message) or may_read_assistant(message, doc_service, clearance))
        visible_messages.append({
            "id": message.id, "role": message.role,
            "content": message.content if allowed else '[Response withheld: source access cannot be verified.]',
            "created_at": message.created_at if allowed else None,
            "tool_events": safe_tool_history(message.extra_fields.get('tool_events_v1'))
            if message.role == 'assistant' and allowed else [],
            "withheld": not allowed,
        })
    try:
        if engine._memory.stream_identity(session_id) != identity:
            raise PermissionError('Conversation unavailable')
    except PermissionError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"session_id": session_id, "title": title, "created_at": created_at, "updated_at": updated_at, "tasks": tasks, "messages": visible_messages}


@router.patch("/sessions/{session_id}/title", summary="Rename an owned conversation")
async def rename_session(session_id: str, body: ConversationTitle, engine=Depends(get_engine),
                         current_user: User = Depends(require_permission(Permission.VIEW_DATA))):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title cannot be blank")
    try:
        engine._memory.set_title(session_id, title)
    except (PermissionError, KeyError):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"session_id": session_id, "title": title}


@router.delete("/sessions/{session_id}", summary="Clear a conversation session")
async def clear_session(
    session_id: str,
    request: Request,
    engine=Depends(get_engine),
    current_user: User = Depends(require_permission(Permission.VIEW_DATA)),
):
    memory = engine._memory
    try:
        deleted = memory.delete_session(session_id)
    except PermissionError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": session_id}
