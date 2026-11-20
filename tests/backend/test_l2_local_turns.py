"""Exercise real local ASGI routes with deterministic, in-process Ollama fixtures."""

import asyncio
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

import anyio
import httpx
import pytest

from backend.auth.models import User
from backend.auth.security import hash_password
from backend.health import routes as health
from backend.main import create_app
from backend.models.base import ChatChunk
from test_a22_async_offline import run_offline
from test_l1_local_dev import local_settings

ORIGIN = "http://127.0.0.1:8000"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}
MODEL = "qwen2.5:7b"


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    async def inventory(target, *, local_only):
        assert target == "http://127.0.0.1:11434/api/tags" and local_only
        return health.ModelObservation("available", "inventory_observed", (MODEL,), ((MODEL, "a" * 64),))

    async def run_sync(function, *args, **kwargs):
        return function(*args)

    monkeypatch.setattr(health, "observe_ollama", inventory)
    monkeypatch.setattr(anyio.to_thread, "run_sync", run_sync)
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<main>Local development</main>", encoding="utf-8")
    return create_app(local_settings(), local_frontend=frontend)


async def login(client, capsys):
    password = re.search(r"this run's password: (\S+)", capsys.readouterr().out)[1]
    response = await client.post("/api/auth/login", json={"username": "local_dev", "password": password})
    assert response.status_code == 200


async def new_turn(client, *, message="Remember the name Cedar."):
    response = await client.post("/api/chat/sessions")
    assert response.status_code == 201
    return {"session_id": response.json()["session_id"], "turn_id": str(uuid4()), "attempt": 1, "message": message}


def api_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers=HEADERS)


def history_path(payload):
    return f"/api/chat/sessions/{payload['session_id']}"


def cancel_path(payload):
    return f"{history_path(payload)}/turns/{payload['turn_id']}/attempts/{payload['attempt']}/cancel"


def parse_events(reply):
    return [json.loads(line[6:]) for line in reply.text.splitlines() if line.startswith("data: ")]


def test_lost_final_event_replays_completion_with_one_write(workbench, capsys, monkeypatch):
    calls = []

    async def provider_stream(request):
        calls.append(request.messages)
        yield ChatChunk("Cedar is the name.", True)

    async def drop_completion(scope, receive, send):
        async def drop(message):
            if b'"type": "done"' in message.get("body", b""):
                message = {**message, "body": b""}
            await send(message)
        await workbench(scope, receive, drop)

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(drop_completion) as browser:
            await login(browser, capsys)
            monkeypatch.setattr(workbench.state.model_router.get_provider("ollama"), "chat_stream", provider_stream)
            turn = await new_turn(browser)
            first = parse_events(await browser.post("/api/chat", json=turn))
            assert [event["type"] for event in first] == ["accepted", "agent_status", "delta"]
            assert first[1]["content"] == "awaiting_model" and first[2]["content"] == "Cedar is the name."
            assert [event["sequence"] for event in first] == [1, 2, 3]
            async with api_client(workbench) as reconnected:
                reconnected.cookies.update(browser.cookies)
                replay = parse_events(await reconnected.post("/api/chat", json=turn))
                assert len(replay) == 1 and replay[0]["type"] == "done"
                assert replay[0]["sequence"] == 1
                final = (await reconnected.get(history_path(turn))).json()["messages"]
                assert [(entry["role"], entry["content"]) for entry in final] == [
                    ("user", turn["message"]), ("assistant", "Cedar is the name."),
                ]
                assert final[0]["id"] == turn["turn_id"]
                assert final[0]["turn"]["assistant_id"] == final[1]["id"]
                assert len(calls) == 1
                assert (await reconnected.post("/api/chat", json={**turn, "message": "Altered"})).status_code == 409
                assert (await reconnected.post("/api/chat", json={**turn, "attempt": 2})).status_code == 409
                assert (await reconnected.post("/api/chat", json={**turn, "turn_id": "invalid"})).status_code == 422
                assert (await reconnected.post(cancel_path(turn))).json()["state"] == "completed"
                assert len(calls) == 1

    run_offline(exercise())


def test_disconnected_observer_cancel_and_concurrent_retry(workbench, capsys, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []
    stopped = []

    async def provider_stream(request):
        calls.append(request.messages)
        try:
            if len(calls) == 1:
                yield ChatChunk("A partial preview that cannot be saved.")
            started.set()
            await release.wait()
            yield ChatChunk("A completed reply.", True)
        finally:
            stopped.append(len(calls))

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as browser:
            await login(browser, capsys)
            monkeypatch.setattr(workbench.state.model_router.get_provider("ollama"), "chat_stream", provider_stream)
            turn = await new_turn(browser)
            disconnected = asyncio.create_task(browser.post("/api/chat", json=turn))
            await asyncio.wait_for(started.wait(), 1)
            disconnected.cancel()
            await asyncio.gather(disconnected, return_exceptions=True)
            pending = (await browser.get(history_path(turn))).json()["messages"]
            assert len(pending) == 1 and pending[0]["turn"]["state"] == "running"
            assert not stopped
            assert (await browser.post("/api/chat", json=turn)).headers["X-Error-Code"] == "TURN_RUNNING"
            another = {**turn, "turn_id": str(uuid4())}
            assert (await browser.post("/api/chat", json=another)).status_code == 409
            cancelled = await browser.post(cancel_path(turn))
            assert cancelled.json()["state"] == "cancelled" and stopped == [1]
            assert (await browser.post(cancel_path(turn))).json()["state"] == "cancelled"
            assert (await browser.get(history_path(turn))).json()["messages"][0]["turn"]["state"] == "cancelled"
            assert parse_events(await browser.post("/api/chat", json=turn))[0]["error_code"] == "TURN_CANCELLED"

            started.clear()
            retry = {**turn, "attempt": 2}
            active = asyncio.create_task(browser.post("/api/chat", json=retry))
            await asyncio.wait_for(started.wait(), 1)
            replies = await asyncio.gather(*(browser.post("/api/chat", json=retry) for _ in range(2)))
            assert [reply.status_code for reply in replies] == [409, 409]
            assert (await browser.post(cancel_path(turn))).status_code == 409
            release.set()
            assert parse_events(await active)[-1]["type"] == "done"
            assert len(calls) == 2 and stopped == [1, 2]
            assert [entry.content for entry in calls[1] if entry.role == "user"] == [turn["message"]]
            final = (await browser.get(history_path(turn))).json()["messages"]
            assert [message["role"] for message in final] == ["user", "assistant"]
            assert final[0]["id"] == turn["turn_id"] and final[0]["turn"]["attempt"] == 2
            assert final[1]["content"] == "A completed reply."

    run_offline(exercise())


@pytest.mark.parametrize(("failure", "error_code"), [
    ("Model request timed out", "MODEL_TIMEOUT"),
    ("Model provider unavailable", "OLLAMA_UNAVAILABLE"),
    ("Incomplete model response", "MODEL_INCOMPLETE"),
])
def test_provider_failure_has_no_phantom_reply(workbench, capsys, monkeypatch, failure, error_code):
    calls = []

    async def provider_stream(request):
        calls.append(request)
        yield ChatChunk("This was only a provisional preview.")
        raise RuntimeError(failure)

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as browser:
            await login(browser, capsys)
            monkeypatch.setattr(workbench.state.model_router.get_provider("ollama"), "chat_stream", provider_stream)
            turn = await new_turn(browser)
            events = parse_events(await browser.post("/api/chat", json=turn))
            assert events[-1]["error_code"] == error_code and not any(event["type"] == "done" for event in events)
            assert parse_events(await browser.post("/api/chat", json=turn))[-1]["error_code"] == error_code
            assert len(calls) == 1
            history = (await browser.get(history_path(turn))).json()["messages"]
            assert len(history) == 1 and history[0]["turn"]["state"] == "failed"
            assert history[0]["turn"]["error_code"] == error_code

    run_offline(exercise())


def test_turn_and_deletion_ownership_and_disposable_restart(workbench, capsys, monkeypatch):
    in_flight = asyncio.Event()
    provider_closed = asyncio.Event()

    async def provider_stream(request):
        if request.messages[-1].content == "Pause until shutdown.":
            try:
                in_flight.set()
                await asyncio.Future()
            finally:
                provider_closed.set()
        yield ChatChunk("An answer for this run only.", True)

    async def exercise():
        async with api_client(workbench) as owner:
            async with workbench.router.lifespan_context(workbench):
                await login(owner, capsys)
                root = workbench.state.settings.tasks_dir
                monkeypatch.setattr(workbench.state.model_router.get_provider("ollama"), "chat_stream", provider_stream)
                keep = await new_turn(owner)
                assert parse_events(await owner.post("/api/chat", json=keep))[-1]["type"] == "done"
                assert len((await owner.get(history_path(keep))).json()["messages"]) == 2
                delete = await new_turn(owner)
                store = workbench.state.auth_store
                store.create_user(User(
                    id="another-local-test-user", username="another", password_hash=hash_password("test-pass"),
                    role="viewer", clearance="L1", created_at=datetime.now(timezone.utc).isoformat(),
                ))
                async with api_client(workbench) as other:
                    assert (await other.post("/api/auth/login", json={
                        "username": "another", "password": "test-pass",
                    })).status_code == 200
                    assert (await other.get(history_path(keep))).status_code == 404
                    assert (await other.post("/api/chat", json=keep)).status_code == 404
                    assert (await other.post(cancel_path(keep))).status_code == 404
                    assert (await other.delete(history_path(delete))).status_code == 404
                response = await owner.delete(history_path(delete))
                assert response.status_code == 200 and response.json()["deleted"] == delete["session_id"]
                assert (await owner.get(history_path(delete))).status_code == 404
                unfinished = await new_turn(owner, message="Pause until shutdown.")
                request = asyncio.create_task(owner.post("/api/chat", json=unfinished))
                await asyncio.wait_for(in_flight.wait(), 1)
            await request
            assert provider_closed.is_set()
            assert not root.exists()
            async with workbench.router.lifespan_context(workbench):
                assert workbench.state.settings.tasks_dir != root
                assert (await owner.get(history_path(keep))).status_code == 401
                await login(owner, capsys)
                assert (await owner.get(history_path(keep))).status_code == 404
                assert (await owner.get("/api/chat/sessions")).json()["count"] == 0

    run_offline(exercise())
