"""Run one owned local attempt and reconcile it through conversation history."""

import asyncio
import base64
import hashlib
import json
import logging
import re
import sqlite3
from contextlib import aclosing
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.requests import ClientDisconnect

from backend.agent.engine import LocalToolError
from backend.agent.memory import LocalTurnConflict, is_local_workflow, public_local_turn
from backend.agent.workflow_intake import (
    MAX_CLARIFICATION_REVISION, ClarificationReply, WorkflowIntake, clarify_intake, make_intake,
)
from backend.api.chat import StreamingResponse
from backend.auth.dependencies import get_current_user
from backend.auth.request_context import dispatch_identity_resolver, request_tools_enabled
from backend.health import routes as health
from backend.multimodal.local_images import MAX_IMAGE_BYTES, LocalImageRef, image_format, prepare_image, refers_to_image
from backend.multimodal.text_file import MAX_TEXT_FILE_BYTES, TextFileError
from backend.rag.local_documents import MAX_DOCUMENT_BYTES, document_format, import_document, validate_document_upload
from backend.rag.local_knowledge import (
    KNOWLEDGE_FIELD, KnowledgeConflict, KnowledgeQuestionError, KnowledgeScope, ScopedSource,
    knowledge_question, question_parts,
)
from backend.schemas.chat import ChatMessage, ChatRequest, ChatResponse, MessageRole
from backend.tools.local_draft_refinement import admit_refinement, follows_draft, parse_refinement
from backend.tools.local_drafts import DraftConflict, DraftScope, _json, artifact_clauses, artifact_intent, evidence_messages, resolve_evidence

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat")
_STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
_ERRORS = {
    "LOCAL_DRAFT_EVIDENCE": "Choose current findings from this conversation before requesting a draft.",
    "LOCAL_DRAFT_FAILED": "The draft could not be assembled within the local limits.",
    "LOCAL_DRAFT_REQUEST": "Enable local tools and choose current sources, an image, or one to five completed evidence turns.",
    "LOCAL_DRAFT_TARGET_INVALID": "This draft is unavailable in the current conversation.",
    "LOCAL_DRAFT_STALE_REVISION": "Review the current draft revision before refining it.",
    "LOCAL_DRAFT_EDIT_INVALID": "That edit could not be applied. Check the section, finding number, or evidence selection in Drafts.",
    "LOCAL_DRAFT_UNSUPPORTED_EVIDENCE": "The selected sources do not support that finding. The draft is unchanged.",
    "LOCAL_WORKFLOW_BUSY": "Two local workflows are running. Wait for one to finish, then retry.",
    "LOCAL_WORKFLOW_RETRY_LIMIT": "This workflow has used its three attempts. Start a new turn to try again.",
    "LOCAL_WORKFLOW_CANCELLED": "Clarification cancelled. Start a new task to continue.",
    "LOCAL_WORKFLOW_PREVIEW_CHANGED": "The sources or task changed. Review the task again before starting.",
    "OLLAMA_UNAVAILABLE": "Start local Ollama and check its model inventory, then retry.",
    "MODEL_UNAVAILABLE": "The selected model is unavailable. Check the installed local model and retry.",
    "MODEL_TIMEOUT": "Ollama timed out. You can retry this message.",
    "MODEL_INCOMPLETE": "Ollama returned an incomplete or invalid response. You can retry this message.",
    "CHAT_UNAVAILABLE": "The response could not be completed. Check conversation history before retrying.",
    "CONVERSATION_UNAVAILABLE": "The conversation or sign-in is no longer available.",
    "TURN_CANCELLED": "Response cancelled.",
    "TURN_INTERRUPTED": "The response was interrupted. You can retry this message.",
    "LOCAL_TOOL_UNAUTHORIZED": "Local chat can run arithmetic and unit conversions only.",
    "LOCAL_TOOL_UNSUPPORTED": "The agent could not plan this request. Try writing an explicit calculation.",
    "LOCAL_TOOL_INVALID": "The numeric request is invalid or outside the supported bounds.",
    "LOCAL_TOOL_UNAVAILABLE": "The requested local tool is unavailable.",
    "LOCAL_TOOL_APPROVAL_REQUIRED": "This operation requires approval and cannot run in local chat.",
    "LOCAL_TOOL_CONFIRMATION_REQUIRED": "This tool requires confirmation and is unavailable in local chat.",
    "LOCAL_TOOL_FAILED": "The tool could not produce a verified result.",
    "LOCAL_TOOL_CHANGED_PLAN": "The proposed plan changed. Send a new message to start it again.",
    "LOCAL_TOOL_CONTEXT": "Specify one verified result, such as the result from the first calculation, or enter the value.",
    "LOCAL_TOOL_REFERENCE_UNIT": "Give both measurements a unit for addition or subtraction, such as 5 m or 5 km.",
    "LOCAL_TOOL_DIMENSION": "Use compatible dimensions for addition and subtraction, or multiply or divide a measurement by a unitless number.",
    "LOCAL_TOOL_TEMPERATURE": "That temperature operation is unsupported. Use Δ°C, Δ°F or ΔK for a difference. To find the difference between two readings, subtract them.",
    "LOCAL_TOOL_ABSOLUTE_ZERO": "An absolute temperature cannot be below absolute zero (0 K). Check the reading or temperature change.",
    "LOCAL_TOOL_TARGET_UNIT": "Which unit should I convert the verified result to?",
    "LOCAL_TOOL_UNIT": "Use supported units of the same dimension. Specify US customary volume units.",
    "LOCAL_TOOL_ZERO_DIVISION": "Division by zero is undefined.",
    "LOCAL_KNOWLEDGE_QUESTION": "Ask a local knowledge question of 1 to 600 characters with at most four parts.",
    "LOCAL_KNOWLEDGE_CHANGED": "Selected local sources changed. Submit a new question with current sources.",
    "LOCAL_IMAGE_UNAVAILABLE": "This image is unavailable in the current conversation. Attach an image to start a new question.",
    "LOCAL_IMAGE_REQUIRED": "Attach or explicitly select an image in this conversation.",
    "LOCAL_IMAGE_AMBIGUOUS": "More than one image is available. Select the image you want to ask about.",
    "LOCAL_IMAGE_REQUEST_INVALID": "Choose one image and ask about it in a separate question from local knowledge sources.",
    "LOCAL_VISION_BUSY": "Another image analysis is running. Wait for it to finish and try again.",
    "LOCAL_VISION_MODEL_UNAVAILABLE": "The selected local vision model is unavailable. Check Ollama and the Models panel.",
    "LOCAL_VISION_FAILED": "The local vision model could not complete this image analysis.",
}


class RetentionChange(BaseModel):
    mode: Literal["disposable", "durable"]


def _owned_retention(request, user):
    if user.id != request.app.state.local_viewer_id:
        raise HTTPException(403, "Only the local viewer can manage history retention.")
    return request.app.state.local_retention


@router.get("/local-retention")
async def read_local_retention(request: Request, user=Depends(get_current_user)):
    retention = _owned_retention(request, user)
    try:
        return retention.status()
    except (OSError, ValueError):
        raise HTTPException(503, "Local history preference is unavailable.") from None


@router.put("/local-retention")
async def change_local_retention(body: RetentionChange, request: Request, user=Depends(get_current_user)):
    retention = _owned_retention(request, user)
    if retention.override is not None:
        raise HTTPException(409, "The launch setting controls history retention.")
    try:
        return retention.set_next_mode(body.mode)
    except (OSError, ValueError):
        raise HTTPException(503, "Local history preference could not be saved.") from None


def _require_local_viewer(request, user):
    state = request.app.state
    if not state.settings.is_local_dev:
        raise HTTPException(404, "Local development is unavailable.")
    resolve = dispatch_identity_resolver.get()
    current = resolve() if resolve else None
    if (current is None or current.id != user.id or current.role != 'viewer'
            or user.id != state.local_viewer_id):
        raise HTTPException(403, "Only the local viewer can manage local sources and images.")
    if not state.startup_complete:
        raise HTTPException(503, "Local development is unavailable.")
    return state


def _owned_knowledge(request, user):
    return _require_local_viewer(request, user).local_knowledge


@router.get("/local-knowledge")
async def read_local_knowledge(request: Request, user=Depends(get_current_user)):
    return _owned_knowledge(request, user).status(user.id)


async def _read_knowledge_upload(request, limit, message):
    payload = bytearray()
    try:
        async for chunk in request.stream():
            if len(payload) + len(chunk) > limit:
                raise HTTPException(413, message)
            payload.extend(chunk)
    except ClientDisconnect:
        raise HTTPException(400, 'The upload connection closed before the file was received.') from None
    return bytes(payload)


async def _read_local_media(request, lock, limit, label, worker_fn, *args):
    if lock.locked():
        raise HTTPException(409, f"A local {label} upload is already in progress.")
    await lock.acquire()
    try:
        raw = await asyncio.wait_for(
            _read_knowledge_upload(request, limit, f"The {label} exceeds {limit // 1024} KiB."), timeout=15,
        )
        worker = asyncio.get_running_loop().run_in_executor(None, worker_fn, *args, raw)
    except TimeoutError:
        lock.release()
        raise HTTPException(408, f"The local {label} upload timed out.") from None
    except BaseException:
        lock.release()
        raise

    def release(future):
        lock.release()
        if not future.cancelled():
            future.exception()

    worker.add_done_callback(release)
    result = await asyncio.shield(worker)
    if await request.is_disconnected():
        raise HTTPException(400, f"The local {label} upload was disconnected.")
    return result


async def _register_knowledge(request, user, filename, source_id=None, revision=None, *,
                              attachment_scope=None, before_save=None):
    _owned_knowledge(request, user)
    media_type = request.headers.get('content-type', '')
    try:
        if kind := document_format(filename):
            validate_document_upload(filename, media_type, kind)
            projection = await _read_local_media(
                request, request.app.state.local_document_intake, MAX_DOCUMENT_BYTES, 'document', import_document, kind,
            )
            if before_save:
                before_save()
            return _owned_knowledge(request, user).register_document(
                user.id, filename, projection, source_id=source_id, revision=revision, attachment_scope=attachment_scope,
            )
        payload = await asyncio.wait_for(
            _read_knowledge_upload(request, MAX_TEXT_FILE_BYTES, "The text file exceeds 2 KiB."), timeout=15,
        )
        if before_save:
            if await request.is_disconnected():
                raise HTTPException(400, 'The local document upload was disconnected.')
            before_save()
        return _owned_knowledge(request, user).register(
            user.id, filename, payload, media_type, source_id=source_id, revision=revision, attachment_scope=attachment_scope,
        )
    except TextFileError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except KeyError:
        raise HTTPException(404, "Local knowledge source not found.") from None
    except KnowledgeConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except TimeoutError:
        raise HTTPException(408, 'The local document upload timed out.') from None
    except (OSError, ValueError):
        raise HTTPException(503, "Local knowledge could not be saved.") from None


@router.post("/local-knowledge/sources", status_code=201)
async def add_knowledge(request: Request, filename: str = Query(min_length=1, max_length=128),
                        user=Depends(get_current_user)):
    return await _register_knowledge(request, user, filename)


@router.put("/local-knowledge/sources/{source_id}")
async def replace_knowledge(source_id: str, request: Request, filename: str = Query(min_length=1, max_length=128),
                            revision: int = Query(ge=1, le=2**31 - 1), user=Depends(get_current_user)):
    return await _register_knowledge(request, user, filename, source_id, revision)


@router.delete("/local-knowledge/sources/{source_id}")
async def remove_knowledge(source_id: str, request: Request, revision: int = Query(ge=1, le=2**31 - 1),
                           user=Depends(get_current_user)):
    try:
        _owned_knowledge(request, user).remove(user.id, source_id, revision)
    except KeyError:
        raise HTTPException(404, "Local knowledge source not found.") from None
    except KnowledgeConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except (OSError, ValueError):
        raise HTTPException(503, "Local knowledge could not be saved.") from None
    return {"removed": True}


def _owned_images(request, user, session_id):
    state = _require_local_viewer(request, user)
    try:
        identity = state.engine._memory.stream_identity(session_id)
    except PermissionError:
        raise HTTPException(404, "Conversation not found.") from None
    return state.local_images, identity


@router.get("/sessions/{session_id}/images")
async def list_local_images(session_id: str, request: Request, user=Depends(get_current_user)):
    images, identity = _owned_images(request, user, session_id)
    return JSONResponse({"images": [ref.attachment(True) for ref in images.list(session_id, identity)]},
                        headers={"Cache-Control": "no-store"})


@router.post("/sessions/{session_id}/images", status_code=201)
async def upload_local_image(session_id: str, request: Request, filename: str = Query(min_length=1, max_length=128),
                             user=Depends(get_current_user)):
    ref = await _register_image(session_id, request, user, filename)
    return JSONResponse(ref.attachment(True), status_code=201, headers={"Cache-Control": "no-store"})


async def _register_image(session_id, request, user, filename, *, turn_id=None, before_save=None):
    images, identity = _owned_images(request, user, session_id)
    try:
        kind = image_format(filename)
        mime = request.headers.get('content-type', '').lower().strip()
        if mime not in ('application/octet-stream', f'image/{kind}'):
            raise HTTPException(415, "The image media type does not match the selected format.")
        ref, encoded = await _read_local_media(request, request.app.state.local_image_intake, MAX_IMAGE_BYTES,
                                                'image', prepare_image, filename, kind)
        current_store, current_identity = _owned_images(request, user, session_id)
        if current_store is not images or current_identity != identity:
            raise HTTPException(404, "Conversation not found.")
        if before_save:
            before_save()
        return images.register(session_id, identity, ref, encoded, turn_id=turn_id)
    except TextFileError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except OSError:
        raise HTTPException(503, "The local image could not be saved.") from None


@router.get("/sessions/{session_id}/images/{image_id}")
async def read_local_image(session_id: str, image_id: str, request: Request, user=Depends(get_current_user)):
    images, identity = _owned_images(request, user, session_id)
    try:
        _, encoded = images.get(session_id, identity, image_id)
    except KeyError:
        raise HTTPException(404, _ERRORS["LOCAL_IMAGE_UNAVAILABLE"]) from None
    return Response(base64.b64decode(encoded), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/sessions/{session_id}/images/{image_id}")
async def delete_local_image(session_id: str, image_id: str, request: Request, user=Depends(get_current_user)):
    images, identity = _owned_images(request, user, session_id)
    try:
        images.remove(session_id, identity, image_id)
    except KeyError:
        raise HTTPException(404, _ERRORS["LOCAL_IMAGE_UNAVAILABLE"]) from None
    except OSError:
        raise HTTPException(503, "The local image could not be removed.") from None
    return {"removed": True}


def _event(session_id, turn_id, attempt, sequence, kind, content="", **extra):
    return "data: " + json.dumps({
        "session_id": session_id, "turn_id": turn_id, "attempt": attempt, "sequence": sequence,
        "type": kind, "content": content, **extra,
    }) + "\n\n"


def _terminal(record):
    if record["state"] in ("completed", "waiting_clarification"):
        return "done", "", {"model_used": record["model"]}
    code = record.get("error_code", "CHAT_UNAVAILABLE")
    return "error", _ERRORS.get(code, _ERRORS["CHAT_UNAVAILABLE"]), {"error_code": code}


def _valid_turn_id(value):
    try:
        parsed = UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _provider_error(exc):
    return {
        "Model provider unavailable": "OLLAMA_UNAVAILABLE",
        "Selected model is unavailable": "MODEL_UNAVAILABLE",
        "Model request timed out": "MODEL_TIMEOUT",
        "Model response interrupted": "MODEL_INCOMPLETE",
        "Incomplete model response": "MODEL_INCOMPLETE",
        "Invalid model response": "MODEL_INCOMPLETE",
        "Planning response invalid": "LOCAL_TOOL_INVALID",
    }.get(str(exc), "CHAT_UNAVAILABLE")


class LocalChatRun:
    """A single attempt's task and bounded, disposable outgoing SSE queue."""

    def __init__(self, app_state, engine, session_id, turn_id, attempt, identity, *, stream, tools_enabled,
                 knowledge_scope=None, image_ref=None, draft_scope=None, workflow_prompt=None, answer_only=False):
        self.app_state = app_state
        self.engine = engine
        self.session_id = session_id
        self.turn_id = turn_id
        self.attempt = attempt
        self.identity = identity
        self.tools_enabled = tools_enabled
        self.knowledge_scope = knowledge_scope
        self.image_ref = image_ref
        self.draft_scope = draft_scope
        self.workflow_prompt = workflow_prompt
        self.answer_only = answer_only
        self.resolve_user = dispatch_identity_resolver.get()
        self.queue = asyncio.Queue(maxsize=64) if stream else None
        self.sequence = 0
        self.started = False
        self.stop_state = None
        self.task = None

    @property
    def key(self):
        return self.session_id, self.turn_id, self.attempt

    def check_access(self):
        if not self.app_state.startup_complete:
            raise PermissionError("Conversation unavailable")
        user = self.resolve_user() if self.resolve_user else None
        if (user is None or user.id != self.identity[0] or
                ((self.image_ref is not None or self.draft_scope is not None or self.knowledge_scope is not None)
                 and (user.role != 'viewer' or user.id != self.app_state.local_viewer_id)) or
                self.engine._memory.stream_identity(self.session_id) != self.identity):
            raise PermissionError("Conversation unavailable")
        return user

    def emit(self, kind, content="", **extra):
        if self.queue is None:
            return
        if self.queue.full():
            self.detach()
            return
        self.sequence += 1
        self.queue.put_nowait(_event(*self.key, self.sequence, kind, content, **extra))

    def check_workflow_active(self):
        if self.stop_state:
            raise asyncio.CancelledError
        return self.check_access()

    def detach(self):
        queue, self.queue = self.queue, None
        if queue is not None:
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)

    async def events(self):
        queue = self.queue
        if queue is None:
            return
        try:
            while (event := await queue.get()) is not None:
                self.check_access()
                yield event
        except PermissionError:
            return
        finally:
            self.detach()

    def stop(self, reason):
        if self.stop_state is None and not self.task.done():
            self.stop_state = reason
            if self.started:
                self.task.cancel()

    async def check_model(self, model, capability='chat'):
        await health.refresh_local_inventory(self.app_state)
        self.check_access()
        readiness = health.model_readiness(self.app_state, model, capability)
        if readiness["state"] != "available":
            code = "OLLAMA_UNAVAILABLE" if readiness["reason"] == "ollama_check_failed" else "MODEL_UNAVAILABLE"
            if capability == 'vision':
                code = "LOCAL_VISION_MODEL_UNAVAILABLE"
            raise LocalToolError(code)

    def image_payload(self):
        self.check_access()
        try:
            ref, payload = self.app_state.local_images.get(self.session_id, self.identity, self.image_ref.image_id,
                                                           turn_id=self.turn_id)
        except KeyError:
            raise LocalToolError('LOCAL_IMAGE_UNAVAILABLE') from None
        if ref != self.image_ref:
            raise LocalToolError('LOCAL_IMAGE_UNAVAILABLE')
        return payload

    async def generate(self, message, model):
        self.started = True
        memory = self.engine._memory
        failure = None
        try:
            if self.stop_state:
                raise asyncio.CancelledError
            self.check_access()
            async with aclosing(self.engine.local_chat_stream(self, message, model)) as stream:
                async for delta in stream:
                    self.check_access()
                    if isinstance(delta, str):
                        self.emit("delta", delta)
                        if self.queue is not None and self.queue.qsize() >= 32:
                            # Give an attached reader a chance to drain a burst of buffered tokens.
                            await asyncio.sleep(0)
        except asyncio.CancelledError:
            state = self.stop_state or "interrupted"
            failure = (state, "TURN_CANCELLED" if state == "cancelled" else "TURN_INTERRUPTED")
        except PermissionError:
            failure = ("interrupted", "CONVERSATION_UNAVAILABLE")
        except LocalToolError as exc:
            failure = ("failed", str(exc))
        except RuntimeError as exc:
            failure = ("failed", _provider_error(exc))
        except Exception:
            logger.warning("Local generation failed")
            failure = ("failed", "CHAT_UNAVAILABLE")

        try:
            if failure:
                memory.end_local_turn(self.session_id, self.identity, self.turn_id, self.attempt, *failure)
            record = memory.read_local_turn(self.session_id, self.identity, self.turn_id)
            kind, content, extra = _terminal(record)
        except (PermissionError, KeyError):
            kind, content, extra = _terminal({"state": "interrupted", "error_code": "CONVERSATION_UNAVAILABLE"})
        except Exception:
            kind, content, extra = _terminal({"state": "failed"})
        self.emit(kind, content, **extra)
        if self.queue is not None:
            if self.queue.full():
                self.detach()
            else:
                self.queue.put_nowait(None)


class LocalChatRuns:
    """Task ownership for the disposable, single-process local lifespan."""

    def __init__(self):
        self.active = {}

    def start(self, run, message, model):
        run.emit("accepted")
        run.task = asyncio.create_task(run.generate(message, model))
        self.active[run.key] = run
        run.task.add_done_callback(lambda _: self.active.pop(run.key, None))

    def stop_session(self, session_id):
        for run in tuple(self.active.values()):
            if run.session_id == session_id:
                run.stop("interrupted")

    async def close(self):
        runs = tuple(self.active.values())
        for run in runs:
            run.detach()
            run.stop("interrupted")
        await asyncio.gather(*(run.task for run in runs), return_exceptions=True)

    def reconcile(self, memory, session_id, identity, turn_id, record):
        if record["state"] == "running" and (session_id, turn_id, record["attempt"]) not in self.active:
            memory.end_local_turn(session_id, identity, turn_id, record["attempt"], "interrupted", "TURN_INTERRUPTED")
            return memory.read_local_turn(session_id, identity, turn_id)
        return record


def _workflow_preview(state, body, identity, model, scope, image, draft, intake):
    effective_scope = intake.scope if intake else scope
    refs = list(effective_scope.sources) if effective_scope else []
    sources = [{'label': ref.label, 'revision': ref.revision} for ref in refs]
    inherited = []
    images = []
    if draft and not draft.workflow:
        evidence = resolve_evidence(state, body.session_id, identity, draft.evidence)
        if len(evidence) != len(draft.evidence):
            raise LocalTurnConflict(_ERRORS['LOCAL_DRAFT_EVIDENCE'])
        known_sources = {}
        known_images = {}
        for ref, findings in evidence.values():
            inherited.append(ref.model_dump())
            for finding in findings:
                if finding.source:
                    source = finding.source
                    known_sources[source['source_id']] = {'label': source['label'], 'revision': source['revision']}
                if finding.image:
                    known_images[finding.image['sha256']] = finding.image['filename']
        sources = list(known_sources.values())
        images = list(known_images.values())
    if intake and intake.image:
        image = intake.image
    if image:
        images.append(image.filename)
    clarification = intake.clarification() if intake else None
    steps = []
    if effective_scope or inherited:
        steps.append('Review source evidence' if effective_scope else 'Reuse verified conversation evidence')
    if re.search(r'\b(?:compar\w*|contrast|differences?)\b', body.message, re.I):
        steps.append('Compare supported findings')
    if image or intake and intake.needs_image:
        steps.append('Analyze the conversation image')
    if draft or intake and not intake.answer_only:
        steps.append('Prepare a draft for review')
    if not steps:
        steps.append('Answer in chat')
    format_name = draft.format if draft else intake.format if intake else None
    output = format_name.upper() + ' draft' if format_name else (
        'Choose a draft format' if intake and not intake.answer_only else 'Chat answer')
    token = hashlib.sha256(_json([
        list(identity), body.session_id, body.turn_id, model, body.message, steps,
        body.tools_enabled, body.planning_enabled,
        effective_scope.model_dump() if effective_scope else None,
        image.model_dump() if image else None, draft.binding() if draft else None,
        intake.binding() if intake else None, inherited,
    ])).hexdigest()
    return {'objective': body.message, 'sources': sources, 'images': images, 'steps': steps, 'output': output,
            'sources_to_choose': bool(intake and intake.choose_sources), 'token': token,
            'question': clarification['question'] if clarification else None}


async def submit_local_chat(body, request, engine, model_router, *, clarification=None, preview=False):
    if ((body.tools_enabled and "tools_enabled" in body.model_fields_set)
            or (body.planning_enabled and "planning_enabled" in body.model_fields_set)):
        raise HTTPException(403, "Local chat manages tool selection automatically.")
    if not body.session_id or not _valid_turn_id(body.turn_id) or not body.message.strip():
        raise HTTPException(422, "Local chat requires an owned conversation, a UUIDv4 turn ID, and a message.")
    request_tools_enabled.set(False)
    session_id, turn_id, attempt = body.session_id, body.turn_id, body.attempt or 1
    memory = engine._memory
    app_state = request.app.state
    runs = app_state.local_chat_runs
    selected = body.model
    if body.image_id is not None and selected is None:
        selected = model_router.configured_model_id('vision')
    model = "/".join(model_router.resolve_model(selected))
    selection = body.knowledge_source_ids
    if selection is not None and len(selection) != len(set(selection)):
        raise HTTPException(422, "Choose each local source only once.")
    output_format = artifact_intent(body.message)
    question = knowledge_question(body.message)
    if selection is not None and question is None:
        question = body.message.strip()
    scope = None
    image_ref = None
    draft_scope = None
    intake = None
    refinement = False
    try:
        identity = memory.stream_identity(session_id)
        try:
            previous = memory.read_local_turn(session_id, identity, turn_id)
        except KeyError:
            previous = None
        else:
            if preview:
                raise LocalTurnConflict('Preview requires a new turn.')
            previous = runs.reconcile(memory, session_id, identity, turn_id, previous)
        images = app_state.local_images
        corpus = app_state.local_knowledge
        saved_draft = (DraftScope.model_validate(previous['draft_scope']['scope'])
                       if previous and 'draft_scope' in previous else None)
        try:
            operation = parse_refinement(body.message) if previous is None else None
        except ValueError as exc:
            raise DraftConflict('Use an existing finding number and bounded wording for the draft edit.') from exc
        if operation and not body.draft_target and not follows_draft(memory, session_id, body.message):
            operation = None
        if (saved_draft and saved_draft.refinement) or operation or body.draft_target:
            resolver = dispatch_identity_resolver.get()
            viewer = resolver() if resolver else None
            if viewer is None or viewer.id != app_state.local_viewer_id or viewer.role != 'viewer':
                raise PermissionError('Draft access denied')
            if body.image_id or preview or not body.tools_enabled or not body.planning_enabled:
                raise DraftConflict('Refine the draft in chat using completed evidence turns for image observations.')
            if saved_draft and (plan := saved_draft.refinement):
                if ((body.draft_target and body.draft_target != plan.target)
                        or (selection is not None and (not plan.sources or not plan.sources.matches_selection(selection)))
                        or (body.draft_turn_ids is not None and body.draft_turn_ids != saved_draft.selection)):
                    raise LocalTurnConflict('This turn is already bound to a different draft refinement.')
                draft_scope = saved_draft
            elif previous is not None:
                raise LocalTurnConflict('This turn is already bound to a different task.')
            elif operation is None:
                raise DraftConflict('Try an edit such as Shorten the summary or Change the format to XLSX.')
            else:
                draft_scope = admit_refinement(app_state, session_id, identity, operation, body.draft_target,
                                               selection, body.draft_turn_ids)
            if attempt > 3:
                raise LocalTurnConflict(_ERRORS['LOCAL_WORKFLOW_RETRY_LIMIT'])
            refinement = True
        elif previous and 'workflow_intake' in previous:
            intake = WorkflowIntake.model_validate(previous['workflow_intake']['intake'])
            model = previous['model']
            if clarification is not None:
                if previous['state'] != 'waiting_clarification' or attempt != previous['attempt']:
                    raise LocalTurnConflict('This workflow is no longer waiting for clarification.')
                try:
                    intake = clarify_intake(intake, clarification)
                except ValueError as exc:
                    raise HTTPException(409, str(exc), headers={"X-Error-Code": "TURN_CONFLICT"}) from None
                if not intake.choose_sources:
                    corpus.check_scope(identity[0], intake.scope)
                if (intake.image is not None and
                        images.get(session_id, identity, intake.image.image_id, turn_id=turn_id)[0] != intake.image):
                    raise KeyError(intake.image.image_id)
            elif (selection is not None and not intake.scope.matches_selection(selection)
                  or body.image_id is not None and (intake.image is None or body.image_id != intake.image.image_id)):
                raise LocalTurnConflict('Turn ID is already bound to a different workflow selection.')
        elif clarification is not None:
            raise LocalTurnConflict('This workflow has no pending clarification.')
        elif (previous is None and body.draft_turn_ids is None and body.tools_enabled and body.planning_enabled):
            prior_evidence = False
            if output_format and selection is None and body.image_id is None:
                prior = [turn for turn, _, _, visual in evidence_messages(memory, session_id) if not visual]
                prior_evidence = bool(prior and re.search(r'\b(?:compar\w*|contrast|differences?)\b', prior[-1].content, re.I))
            try:
                chosen_image = (images.get(session_id, identity, body.image_id, turn_id=turn_id)[0]
                                if _valid_turn_id(body.image_id) else None)
                intake = make_intake(app_state, session_id, identity, body.message, selection, chosen_image,
                                     output_format, prior_evidence=bool(prior_evidence)) if not body.image_id or chosen_image else None
            except KeyError as exc:
                if body.image_id and exc.args == (body.image_id,):
                    raise
                raise HTTPException(404, "One or more selected local sources are unavailable.",
                                    headers={"X-Error-Code": "LOCAL_KNOWLEDGE_SOURCE_UNAVAILABLE"}) from None
            if intake and intake.needs_image and not body.image_id:
                model = "/".join(model_router.resolve_model(model_router.configured_model_id('vision')))
        if intake is not None:
            user = dispatch_identity_resolver.get()
            if user is None or (current := user()) is None or current.role != 'viewer' or current.id != app_state.local_viewer_id:
                raise PermissionError('Local workflow unavailable')
            if attempt > 3:
                raise LocalTurnConflict(_ERRORS["LOCAL_WORKFLOW_RETRY_LIMIT"])
            if not intake.scope.sources and not intake.choose_sources:
                raise HTTPException(422, "Import an authorized local document before comparing sources.",
                                    headers={"X-Error-Code": "LOCAL_KNOWLEDGE_SOURCE_UNAVAILABLE"})
            if not intake.reason:
                scope, image_ref = intake.scope, intake.image
                if not intake.answer_only:
                    draft_scope = DraftScope(format=intake.format, evidence=[], workflow=True)
                if previous is None or attempt > previous['attempt'] or clarification is not None:
                    corpus.check_scope(identity[0], scope)
                    if image_ref and images.get(session_id, identity, image_ref.image_id, turn_id=turn_id)[0] != image_ref:
                        raise KeyError(image_ref.image_id)
                    if image_ref and any(run.image_ref is not None and not run.task.done() for run in runs.active.values()):
                        raise HTTPException(409, _ERRORS["LOCAL_VISION_BUSY"], headers={"X-Error-Code": "LOCAL_VISION_BUSY"})
        elif previous and 'image' in previous:
            image_ref = LocalImageRef.model_validate(previous['image']['ref'])
        if not refinement and intake is None and previous and body.image_id != (image_ref.image_id if image_ref else None):
            raise LocalTurnConflict('Turn ID is already bound to a different image.')
        new_attempt = previous is None or attempt > previous['attempt']
        if not refinement and intake is None and (output_format or body.draft_turn_ids is not None):
            from backend.agent.local_workflow import workflow_parts, workflows_with_sources

            workflow = output_format is not None and body.draft_turn_ids is None and (
                len(artifact_clauses(body.message)) > 1 or selection is not None or body.image_id is not None
                or workflows_with_sources(body.message)
            )
            if (not output_format or (not workflow and (body.image_id is not None or selection is not None))
                    or not body.tools_enabled or not body.planning_enabled
                    or (body.draft_turn_ids is not None
                        and (not body.draft_turn_ids or len(set(body.draft_turn_ids)) != len(body.draft_turn_ids)))):
                code = 'LOCAL_DRAFT_REQUEST'
                raise HTTPException(422, _ERRORS[code], headers={"X-Error-Code": code})
            if workflow:
                if attempt > 3:
                    code = 'LOCAL_WORKFLOW_RETRY_LIMIT'
                    raise HTTPException(409, _ERRORS[code], headers={"X-Error-Code": code})
                if body.image_id is None and refers_to_image(body.message):
                    code = 'LOCAL_IMAGE_REQUIRED'
                    raise HTTPException(422, _ERRORS[code], headers={"X-Error-Code": code})
                parts = workflow_parts(body.message, has_sources=selection is not None, has_image=body.image_id is not None)
                question = body.message if parts else None
            else:
                question = None
            if previous:
                if 'draft_scope' not in previous:
                    raise LocalTurnConflict('Turn ID is already bound to a different submission.')
                draft_scope = DraftScope.model_validate(previous['draft_scope']['scope'])
                if (draft_scope.selection != body.draft_turn_ids or draft_scope.format != output_format
                        or draft_scope.workflow != workflow):
                    raise LocalTurnConflict('Turn ID is already bound to a different draft selection.')
            elif workflow:
                draft_scope = DraftScope(format=output_format, evidence=[], workflow=True)
            else:
                eligible = [turn.id for turn, _, _, visual in evidence_messages(memory, session_id)
                            if not visual]
                ids = body.draft_turn_ids if body.draft_turn_ids is not None else eligible[-1:]
                if not ids or len(resolve_evidence(app_state, session_id, identity, ids)) != len(ids):
                    code = 'LOCAL_DRAFT_EVIDENCE'
                    raise HTTPException(409, _ERRORS[code], headers={"X-Error-Code": code})
                draft_scope = DraftScope(format=output_format, selection=body.draft_turn_ids, evidence=ids)
        if not refinement and intake is None and body.image_id is not None:
            if not _valid_turn_id(body.image_id) or (
                    (selection is not None or question is not None) and not (draft_scope and draft_scope.workflow)):
                code = 'LOCAL_IMAGE_REQUEST_INVALID'
                raise HTTPException(422, _ERRORS[code], headers={"X-Error-Code": code})
            if new_attempt:
                stored, _ = images.get(session_id, identity, body.image_id, turn_id=turn_id)
                if image_ref is not None and image_ref != stored:
                    raise KeyError(body.image_id)
                image_ref = stored
                if any(run.image_ref is not None and not run.task.done() for run in runs.active.values()):
                    raise HTTPException(409, _ERRORS["LOCAL_VISION_BUSY"], headers={"X-Error-Code": "LOCAL_VISION_BUSY"})
        elif intake is None and previous is None and draft_scope is None and question is None and refers_to_image(body.message):
            code = 'LOCAL_IMAGE_AMBIGUOUS' if len(images.list(session_id, identity)) > 1 else 'LOCAL_IMAGE_REQUIRED'
            raise HTTPException(422, _ERRORS[code], headers={"X-Error-Code": code})
        if not refinement and intake is None and previous is None and question is not None:
            if not (draft_scope and draft_scope.workflow):
                question_parts(question)
            try:
                scope = corpus.bind_scope(identity[0], selection)
            except KeyError:
                raise HTTPException(404, "One or more selected local sources are unavailable.",
                                    headers={"X-Error-Code": "LOCAL_KNOWLEDGE_SOURCE_UNAVAILABLE"}) from None
        elif not refinement and intake is None and previous is not None:
            if 'knowledge_scope' in previous:
                scope = KnowledgeScope.model_validate(previous['knowledge_scope']['scope'])
                if not scope.matches_selection(selection):
                    raise LocalTurnConflict('Turn ID is already bound to a different local source selection.')
                if attempt > previous['attempt'] and previous['state'] in ('failed', 'cancelled', 'interrupted'):
                    corpus.check_scope(identity[0], scope)
            elif selection is not None:
                raise LocalTurnConflict('Turn ID is already bound to a different submission.')
            elif question is not None and attempt > previous['attempt']:
                raise KnowledgeConflict('The original question has no saved source scope. Submit a new question.')
        workflow = bool(draft_scope and draft_scope.workflow or intake and intake.answer_only and not intake.reason)
        if workflow and (new_attempt or clarification is not None) and sum(
            bool(run.answer_only or run.draft_scope and run.draft_scope.workflow) and not run.task.done()
            for run in runs.active.values()
        ) >= 2:
            code = 'LOCAL_WORKFLOW_BUSY'
            raise HTTPException(409, _ERRORS[code], headers={"X-Error-Code": code})
        tools_enabled = (image_ref is None or bool(draft_scope and draft_scope.workflow)) and bool(
            body.tools_enabled and body.planning_enabled
        )
        if (new_attempt and previous and is_local_workflow(previous)
                and previous.get('error_code') in ('LOCAL_TOOL_UNAUTHORIZED', 'CONVERSATION_UNAVAILABLE')):
            raise HTTPException(403, 'This failure cannot be retried. Start a new task if access has changed.')
        if preview or body.workflow_preview and previous is None:
            interpretation = _workflow_preview(app_state, body, identity, model, scope, image_ref, draft_scope, intake)
            if preview:
                return JSONResponse(interpretation, headers={'Cache-Control': 'no-store'})
            if body.workflow_preview != interpretation['token']:
                raise HTTPException(409, _ERRORS['LOCAL_WORKFLOW_PREVIEW_CHANGED'],
                                    headers={'X-Error-Code': 'LOCAL_WORKFLOW_PREVIEW_CHANGED'})
        record, admitted = memory.claim_local_turn(
            session_id, identity, turn_id, attempt, body.message, model, tools_enabled=tools_enabled,
            knowledge_scope=scope, image_ref=image_ref, draft_scope=draft_scope,
            intake=intake, clarification=clarification,
        )
    except KeyError:
        raise HTTPException(404, _ERRORS["LOCAL_IMAGE_UNAVAILABLE"],
                            headers={"X-Error-Code": "LOCAL_IMAGE_UNAVAILABLE"}) from None
    except PermissionError:
        raise HTTPException(404, "Conversation not found") from None
    except LocalTurnConflict as exc:
        raise HTTPException(409, str(exc), headers={"X-Error-Code": "TURN_CONFLICT"}) from None
    except DraftConflict as exc:
        raise HTTPException(409, str(exc), headers={"X-Error-Code": "LOCAL_DRAFT_TARGET_REQUIRED"}) from None
    except KnowledgeQuestionError:
        raise HTTPException(422, _ERRORS["LOCAL_KNOWLEDGE_QUESTION"],
                            headers={"X-Error-Code": "LOCAL_KNOWLEDGE_QUESTION"}) from None
    except KnowledgeConflict as exc:
        raise HTTPException(409, str(exc), headers={"X-Error-Code": "LOCAL_KNOWLEDGE_CHANGED"}) from None
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(503, _ERRORS["CHAT_UNAVAILABLE"]) from None

    if admitted:
        run = LocalChatRun(app_state, engine, session_id, turn_id, attempt, identity, stream=body.stream,
                           tools_enabled=tools_enabled, knowledge_scope=scope, image_ref=image_ref, draft_scope=draft_scope,
                           workflow_prompt=intake.execution_prompt(body.message) if intake else None,
                           answer_only=bool(intake and intake.answer_only))
        runs.start(run, body.message, model)
        if body.stream:
            return StreamingResponse(run.events(), media_type="text/event-stream", headers=_STREAM_HEADERS)
        await asyncio.shield(run.task)
        try:
            run.check_access()
            record = memory.read_local_turn(session_id, identity, turn_id)
        except (PermissionError, KeyError):
            raise HTTPException(404, "Conversation not found") from None
    elif record["state"] == "running":
        raise HTTPException(409, "This turn is already running. Refresh conversation history.",
                            headers={"X-Error-Code": "TURN_RUNNING"})
    if record["attempt"] != attempt:
        raise HTTPException(409, "A newer attempt exists. Refresh conversation history.")
    kind, content, extra = _terminal(record)
    if body.stream:
        async def replay():
            yield _event(session_id, turn_id, attempt, 1, kind, content, **extra)
        return StreamingResponse(replay(), media_type="text/event-stream", headers=_STREAM_HEADERS)
    if record['state'] == 'waiting_clarification':
        return JSONResponse(public_local_turn(record), status_code=202, headers={'Cache-Control': 'no-store'})
    if kind == "error":
        status = {"LOCAL_TOOL_UNAUTHORIZED": 403, "LOCAL_TOOL_APPROVAL_REQUIRED": 403,
                  "LOCAL_TOOL_UNSUPPORTED": 422, "LOCAL_TOOL_INVALID": 422, "LOCAL_TOOL_UNIT": 422,
                  "LOCAL_TOOL_CONTEXT": 422, "LOCAL_TOOL_REFERENCE_UNIT": 422, "LOCAL_TOOL_ZERO_DIVISION": 422,
                  "LOCAL_TOOL_DIMENSION": 422, "LOCAL_TOOL_TEMPERATURE": 422, "LOCAL_TOOL_TARGET_UNIT": 422,
                  "LOCAL_TOOL_ABSOLUTE_ZERO": 422,
                  "LOCAL_TOOL_CONFIRMATION_REQUIRED": 403,
                  "LOCAL_KNOWLEDGE_CHANGED": 409, "LOCAL_KNOWLEDGE_QUESTION": 422,
                  "LOCAL_IMAGE_UNAVAILABLE": 409, "LOCAL_DRAFT_TARGET_INVALID": 404,
                  "LOCAL_DRAFT_STALE_REVISION": 409, "LOCAL_DRAFT_EDIT_INVALID": 422,
                  "LOCAL_DRAFT_UNSUPPORTED_EVIDENCE": 422,
                  "LOCAL_TOOL_CHANGED_PLAN": 409}.get(extra["error_code"], 503)
        if record["state"] == "cancelled":
            status = 409
        raise HTTPException(status, content, headers={"X-Error-Code": extra["error_code"]})
    answer = next((item for item in memory.get_history(session_id) if item.id == record["assistant_id"]), None)
    if answer is None or not memory.verify_local_reply(session_id, answer):
        raise HTTPException(503, _ERRORS["CHAT_UNAVAILABLE"])
    if 'local_workflow' in answer.extra_fields and 'image' in record:
        from backend.agent.local_workflow import workflow_image_current

        if not workflow_image_current(app_state, session_id, identity, turn_id):
            raise HTTPException(409, _ERRORS["LOCAL_IMAGE_UNAVAILABLE"])
    if KNOWLEDGE_FIELD in answer.extra_fields:
        knowledge = app_state.local_knowledge.public_receipt(identity[0], answer.extra_fields[KNOWLEDGE_FIELD])
        if knowledge is None:
            raise HTTPException(409, _ERRORS["LOCAL_KNOWLEDGE_CHANGED"])
        return JSONResponse({"session_id": session_id, "message": {"role": "assistant", "content": answer.content},
                             "model_used": record["model"], "local_knowledge": knowledge})
    return ChatResponse(session_id=session_id, message=ChatMessage(role=MessageRole.assistant, content=answer.content),
                        model_used=record["model"])


@router.post("/workflow-preview")
async def preview_local_workflow(body: ChatRequest, request: Request, user=Depends(get_current_user)):
    state = _require_local_viewer(request, user)
    return await submit_local_chat(body, request, state.engine, state.model_router, preview=True)


@router.post("/sessions/{session_id}/turns/{turn_id}/clarify")
async def clarify_local_workflow(session_id: str, turn_id: str, reply: ClarificationReply, request: Request,
                                 user=Depends(get_current_user)):
    state = _require_local_viewer(request, user)
    memory = state.engine._memory
    try:
        identity = memory.stream_identity(session_id)
        record = memory.read_local_turn(session_id, identity, turn_id)
        original = next(item.content for item in memory.get_history(session_id) if item.id == turn_id)
    except (KeyError, PermissionError, StopIteration):
        raise HTTPException(404, 'Conversation turn not found.') from None
    except (ValueError, OSError, sqlite3.Error):
        raise HTTPException(503, _ERRORS['CHAT_UNAVAILABLE']) from None
    body = ChatRequest(session_id=session_id, turn_id=turn_id, attempt=record['attempt'], message=original,
                       model=record['model'])
    return await submit_local_chat(body, request, state.engine, state.model_router, clarification=reply)


@router.post('/sessions/{session_id}/turns/{turn_id}/clarify/attachment', status_code=201)
async def upload_clarification_attachment(
    session_id: str, turn_id: str, request: Request, kind: Literal['document', 'image'],
    revision: int = Query(ge=1, le=MAX_CLARIFICATION_REVISION),
    filename: str = Query(min_length=1, max_length=128), user=Depends(get_current_user),
):
    state = _require_local_viewer(request, user)
    memory = state.engine._memory
    try:
        identity = memory.stream_identity(session_id)

        def check_pause():
            memory.pending_attachment(session_id, identity, turn_id, revision, kind)

        check_pause()
        if kind == 'document':
            if not filename.lower().endswith(('.txt', '.md')) and not document_format(filename):
                raise HTTPException(415, 'Choose a TXT, Markdown, PDF, DOCX or XLSX document.')
            source = await _register_knowledge(request, user, filename,
                                               attachment_scope=(session_id, identity[1], turn_id), before_save=check_pause)
            attachment = ScopedSource.model_validate({key: source[key] for key in ('source_id', 'revision', 'digest', 'label')})
            details = source
        else:
            attachment = await _register_image(session_id, request, user, filename, turn_id=turn_id, before_save=check_pause)
            details = attachment.attachment(True)
        turn = memory.record_clarification_attachment(session_id, identity, turn_id, revision, kind, attachment)
    except (KeyError, PermissionError):
        raise HTTPException(404, 'Conversation turn not found.') from None
    except LocalTurnConflict as exc:
        raise HTTPException(409, str(exc), headers={'X-Error-Code': 'TURN_CONFLICT'}) from None
    except (OSError, ValueError, sqlite3.Error):
        raise HTTPException(503, 'The attachment could not be saved to this clarification.') from None
    return JSONResponse({'turn': turn, 'attachment': details}, status_code=201, headers={'Cache-Control': 'no-store'})


@router.post("/sessions/{session_id}/turns/{turn_id}/attempts/{attempt}/cancel")
async def cancel_local_turn(session_id: str, turn_id: str, attempt: int, request: Request,
                            _user=Depends(get_current_user)):
    app_state = request.app.state
    memory = app_state.engine._memory
    runs = app_state.local_chat_runs
    try:
        identity = memory.stream_identity(session_id)
        record = memory.read_local_turn(session_id, identity, turn_id)
        if record["attempt"] != attempt:
            raise HTTPException(409, "A newer attempt exists. Refresh conversation history.")
        run = runs.active.get((session_id, turn_id, attempt))
        if run and record["state"] == "running":
            memory.end_local_turn(session_id, identity, turn_id, attempt, "cancelled", "TURN_CANCELLED")
            run.stop("cancelled")
            await asyncio.shield(run.task)
            record = memory.read_local_turn(session_id, identity, turn_id)
            if record["attempt"] != attempt:
                raise HTTPException(409, "A newer attempt exists. Refresh conversation history.")
        if record["state"] == "waiting_clarification":
            memory.end_local_turn(session_id, identity, turn_id, attempt, "cancelled", "LOCAL_WORKFLOW_CANCELLED")
            record = memory.read_local_turn(session_id, identity, turn_id)
        record = runs.reconcile(memory, session_id, identity, turn_id, record)
        return public_local_turn(record)
    except (PermissionError, KeyError):
        raise HTTPException(404, "Conversation turn not found") from None
