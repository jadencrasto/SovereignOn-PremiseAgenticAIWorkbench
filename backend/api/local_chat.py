"""Run one owned, text-only local attempt and reconcile it through conversation history."""

import asyncio
import json
import logging
import sqlite3
from contextlib import aclosing
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.agent.engine import LocalToolError
from backend.agent.memory import LocalTurnConflict, public_local_turn
from backend.api.chat import StreamingResponse
from backend.auth.dependencies import get_current_user
from backend.auth.request_context import dispatch_identity_resolver, request_tools_enabled
from backend.health import routes as health
from backend.schemas.chat import ChatMessage, ChatResponse, MessageRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat")
_STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
_ERRORS = {
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


def _event(session_id, turn_id, attempt, sequence, kind, content="", **extra):
    return "data: " + json.dumps({
        "session_id": session_id, "turn_id": turn_id, "attempt": attempt, "sequence": sequence,
        "type": kind, "content": content, **extra,
    }) + "\n\n"


def _terminal(record):
    if record["state"] == "completed":
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

    def __init__(self, app_state, engine, session_id, turn_id, attempt, identity, *, stream, tools_enabled):
        self.app_state = app_state
        self.engine = engine
        self.session_id = session_id
        self.turn_id = turn_id
        self.attempt = attempt
        self.identity = identity
        self.tools_enabled = tools_enabled
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

    async def check_model(self, model):
        await health.refresh_local_inventory(self.app_state)
        self.check_access()
        readiness = health.model_readiness(self.app_state, model)
        if readiness["state"] != "available":
            code = "OLLAMA_UNAVAILABLE" if readiness["reason"] == "ollama_check_failed" else "MODEL_UNAVAILABLE"
            raise LocalToolError(code)

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
            logger.warning("Local text generation failed")
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


async def submit_local_chat(body, request, engine, model_router):
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
    model = "/".join(model_router.resolve_model(body.model))
    try:
        identity = memory.stream_identity(session_id)
        try:
            previous = memory.read_local_turn(session_id, identity, turn_id)
        except KeyError:
            pass
        else:
            runs.reconcile(memory, session_id, identity, turn_id, previous)
        tools_enabled = bool(body.tools_enabled and body.planning_enabled)
        record, admitted = memory.claim_local_turn(
            session_id, identity, turn_id, attempt, body.message, model, tools_enabled=tools_enabled,
        )
    except PermissionError:
        raise HTTPException(404, "Conversation not found") from None
    except LocalTurnConflict as exc:
        raise HTTPException(409, str(exc), headers={"X-Error-Code": "TURN_CONFLICT"}) from None
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(503, _ERRORS["CHAT_UNAVAILABLE"]) from None

    if admitted:
        run = LocalChatRun(app_state, engine, session_id, turn_id, attempt, identity, stream=body.stream,
                           tools_enabled=tools_enabled)
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
    if kind == "error":
        status = {"LOCAL_TOOL_UNAUTHORIZED": 403, "LOCAL_TOOL_APPROVAL_REQUIRED": 403,
                  "LOCAL_TOOL_UNSUPPORTED": 422, "LOCAL_TOOL_INVALID": 422, "LOCAL_TOOL_UNIT": 422,
                  "LOCAL_TOOL_CONTEXT": 422, "LOCAL_TOOL_REFERENCE_UNIT": 422, "LOCAL_TOOL_ZERO_DIVISION": 422,
                  "LOCAL_TOOL_DIMENSION": 422, "LOCAL_TOOL_TEMPERATURE": 422, "LOCAL_TOOL_TARGET_UNIT": 422,
                  "LOCAL_TOOL_ABSOLUTE_ZERO": 422,
                  "LOCAL_TOOL_CONFIRMATION_REQUIRED": 403,
                  "LOCAL_TOOL_CHANGED_PLAN": 409}.get(extra["error_code"], 503)
        if record["state"] == "cancelled":
            status = 409
        raise HTTPException(status, content, headers={"X-Error-Code": extra["error_code"]})
    answer = next((item for item in memory.get_history(session_id) if item.id == record["assistant_id"]), None)
    if answer is None or not memory.verify_local_reply(session_id, answer):
        raise HTTPException(503, _ERRORS["CHAT_UNAVAILABLE"])
    return ChatResponse(session_id=session_id, message=ChatMessage(role=MessageRole.assistant, content=answer.content),
                        model_used=record["model"])


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
            run.stop("cancelled")
            await asyncio.shield(run.task)
            record = memory.read_local_turn(session_id, identity, turn_id)
            if record["attempt"] != attempt:
                raise HTTPException(409, "A newer attempt exists. Refresh conversation history.")
        record = runs.reconcile(memory, session_id, identity, turn_id, record)
        return public_local_turn(record)
    except (PermissionError, KeyError):
        raise HTTPException(404, "Conversation turn not found") from None
