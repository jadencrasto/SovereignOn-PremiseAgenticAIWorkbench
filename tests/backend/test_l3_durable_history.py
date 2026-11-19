"""Offline local history acceptance through the existing FastAPI and memory APIs."""

import asyncio
import sqlite3
from contextlib import closing
from uuid import uuid4

import anyio
import pytest

from backend import local_history
from backend.auth.request_context import effective_owner
from backend.config import Settings
from backend.health import routes as health
from backend.main import create_app
from backend.models.base import ChatChunk
from test_a22_async_offline import run_offline
from test_l1_local_dev import local_settings
from test_l2_local_turns import api_client, history_path, login, new_turn, parse_events

PREFERENCE = "/api/chat/local-retention"
MODEL = "qwen2.5:7b"


@pytest.fixture
def durable_environment(tmp_path, monkeypatch):
    root = tmp_path / "retained"
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<main>Local workbench</main>", encoding="utf-8")

    async def inventory(target, *, local_only):
        assert local_only
        return health.ModelObservation("available", "inventory_observed", (MODEL,), ((MODEL, "a" * 64),))

    async def run_sync(function, *args, **kwargs):
        return function(*args)

    monkeypatch.setattr(local_history, "history_location", lambda: root)
    monkeypatch.setattr(health, "observe_ollama", inventory)
    monkeypatch.setattr(anyio.to_thread, "run_sync", run_sync)

    def app(mode=None, **overrides):
        return create_app(local_settings(SOVEREIGNAI_LOCAL_HISTORY=mode, **overrides), local_frontend=frontend)
    return root, app


def test_opt_in_requires_authenticated_choice_and_switching_uses_separate_stores(durable_environment, capsys):
    root, make_app = durable_environment

    async def exercise():
        app = make_app()
        async with app.router.lifespan_context(app), api_client(app) as client:
            assert not root.exists()
            assert (await client.get("/api/health/capabilities")).json()["history_retention"] == "disposable"
            assert (await client.put(PREFERENCE, json={"mode": "durable"})).status_code == 401
            await login(client, capsys)
            disposable = await new_turn(client)
            result = await client.put(PREFERENCE, json={"mode": "durable"})
            assert result.json() == {"mode": "disposable", "next_mode": "durable", "launch_override": False}
            assert (await client.get(PREFERENCE)).json() == result.json()
            assert not (root / "history.sqlite3").exists()
        (root / "conversations.json").write_text("Legacy source must never be opened.", encoding="utf-8")
        durable = make_app()
        async with durable.router.lifespan_context(durable), api_client(durable) as client:
            await login(client, capsys)
            assert (await client.get("/api/health/capabilities")).json()["history_retention"] == "durable"
            assert (await client.get(history_path(disposable))).status_code == 404
            retained = await new_turn(client)
            result = await client.put(PREFERENCE, json={"mode": "disposable"})
            assert result.json() == {"mode": "durable", "next_mode": "disposable", "launch_override": False}
            for path in ("/api/chat", "/api/chat/multimodal"):
                assert (await client.post(path, files={"file": ("text.pdf", b"%PDF")})).status_code == 415
            assert (await client.get("/api/documents")).status_code == 503
        assert (root / "conversations.json").read_text(encoding="utf-8") == "Legacy source must never be opened."
        database = root / "history.sqlite3"
        before = database.read_bytes()
        disposable_app = make_app()
        async with disposable_app.router.lifespan_context(disposable_app), api_client(disposable_app) as client:
            await login(client, capsys)
            assert (await client.get(PREFERENCE)).json()["mode"] == "disposable"
            assert (await client.get(history_path(retained))).status_code == 404
        assert database.read_bytes() == before
        reopened = make_app("durable")
        async with reopened.router.lifespan_context(reopened), api_client(reopened) as client:
            await login(client, capsys)
            assert (await client.get(history_path(retained))).status_code == 200
            assert (await client.put(PREFERENCE, json={"mode": "disposable"})).status_code == 409
        production = create_app(Settings(_env_file=None, app_env="production", SOVEREIGNAI_RUN_PROFILE=None,
                                         SOVEREIGNAI_LOCAL_HISTORY="durable", auth_enabled=True))
        async with api_client(production) as client:
            assert (await client.get(PREFERENCE)).status_code == 404

    run_offline(exercise())


def test_restart_recovers_principal_turns_and_deletion_without_reexecution(durable_environment, capsys, monkeypatch):
    root, make_app = durable_environment
    requested = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def provider(request):
        requested.append(request.messages)
        if len(requested) == 2:
            raise RuntimeError("Model request timed out")
        if len(requested) == 3:
            entered.set()
            await release.wait()
        yield ChatChunk("I remember Cedar.", True)

    async def exercise():
        app = make_app("durable")
        async with api_client(app) as client:
            async with app.router.lifespan_context(app):
                await login(client, capsys)
                owner = (await client.get("/api/auth/me")).json()["id"]
                old_cookie = client.cookies.get("sovereign_local_dev_session")
                model = app.state.model_router.get_provider("ollama")
                monkeypatch.setattr(model, "chat_stream", provider)
                completed = await new_turn(client)
                assert parse_events(await client.post("/api/chat", json=completed))[-1]["type"] == "done"
                failed = {**completed, "turn_id": str(uuid4()), "message": "Expired question"}
                assert parse_events(await client.post("/api/chat", json=failed))[-1]["error_code"] == "MODEL_TIMEOUT"
                memory = app.state.engine._memory
                token = effective_owner.set(owner)
                try:
                    identity = memory.stream_identity(completed["session_id"])
                    cancelled = {**completed, "turn_id": str(uuid4()), "message": "Never answer this"}
                    interrupted = {**completed, "turn_id": str(uuid4()), "message": "What name did I ask you to remember?"}
                    for pending in (cancelled, interrupted):
                        memory.claim_local_turn(completed["session_id"], identity, pending["turn_id"], 1,
                                                pending["message"], f"ollama/{MODEL}")
                        if pending is cancelled:
                            memory.end_local_turn(completed["session_id"], identity, pending["turn_id"], 1,
                                                  "cancelled", "TURN_CANCELLED")
                finally:
                    effective_owner.reset(token)
                deleted = await new_turn(client)
                assert (await client.delete(history_path(deleted))).json() == {"deleted": deleted["session_id"]}
            assert (root / "history.sqlite3").is_file() and not (root / "conversations.json").exists()
            async with app.router.lifespan_context(app):
                assert (await client.get(history_path(completed))).status_code == 401
                await login(client, capsys)
                assert client.cookies.get("sovereign_local_dev_session") != old_cookie
                assert (await client.get("/api/auth/me")).json()["id"] == owner
                token = effective_owner.set("another-local-principal")
                try:
                    with pytest.raises(PermissionError):
                        app.state.engine._memory.get_history(completed["session_id"])
                finally:
                    effective_owner.reset(token)
                assert (await client.get(history_path(deleted))).status_code == 404
                snapshot = (await client.get(history_path(completed))).json()["messages"]
                assert [m["turn"]["state"] for m in snapshot if m["role"] == "user"] == [
                    "completed", "failed", "cancelled", "interrupted",
                ]
                assert [m["content"] for m in snapshot if m["role"] == "assistant"] == ["I remember Cedar."]
                assert parse_events(await client.post("/api/chat", json=completed))[-1]["type"] == "done"
                assert parse_events(await client.post("/api/chat", json=interrupted))[-1]["error_code"] == "TURN_INTERRUPTED"
                assert len(requested) == 2
                monkeypatch.setattr(app.state.model_router.get_provider("ollama"), "chat_stream", provider)
                retry = {**interrupted, "attempt": 2}
                active = asyncio.create_task(client.post("/api/chat", json=retry))
                await asyncio.wait_for(entered.wait(), 1)
                assert (await client.post("/api/chat", json=retry)).status_code == 409
                assert (await client.patch(history_path(completed) + "/title", json={"title": "Retained Cedar"})).status_code == 200
                release.set()
                assert parse_events(await active)[-1]["type"] == "done"
                assert len(requested) == 3
                assert [(m.role, m.content) for m in requested[-1] if m.role != "system"] == [
                    ("user", completed["message"]), ("assistant", "I remember Cedar."), ("user", retry["message"]),
                ]
                assert (await client.get(history_path(completed))).json()["title"] == "Retained Cedar"
        with closing(sqlite3.connect(root / "history.sqlite3")) as db:
            assert db.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            assert db.execute("PRAGMA user_version").fetchone() == (1,)
            assert db.execute("SELECT COUNT(*) FROM local_history_transcripts WHERE id=?", (deleted["session_id"],)).fetchone() == (0,)

    run_offline(exercise())


def test_durable_delete_rolls_back_together_with_registry(durable_environment, capsys, monkeypatch):
    _, make_app = durable_environment

    async def exercise():
        app = make_app("durable")
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            owned = await new_turn(client)
            memory = app.state.engine._memory
            storage = memory._sqlite_store
            save = storage.save

            def write_and_fail(snapshot):
                save(snapshot)
                raise sqlite3.OperationalError("synthetic write failure")

            token = effective_owner.set(app.state.local_viewer_id)
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(storage, "save", write_and_fail)
                    with pytest.raises(sqlite3.OperationalError, match="synthetic"):
                        memory.delete_session(owned["session_id"])
                assert memory.stream_identity(owned["session_id"])[0] == app.state.local_viewer_id
            finally:
                effective_owner.reset(token)
            assert (await client.get(history_path(owned))).status_code == 200
            assert (await client.delete(history_path(owned))).status_code == 200

    run_offline(exercise())


@pytest.mark.parametrize("damage", ["future_version", "bad_transcript", "foreign_store"])
def test_damaged_or_foreign_database_is_never_reset(durable_environment, capsys, damage):
    root, make_app = durable_environment
    path = root / "history.sqlite3"

    async def exercise():
        app = make_app("durable")
        if damage != "foreign_store":
            async with app.router.lifespan_context(app), api_client(app) as client:
                await login(client, capsys)
                await new_turn(client)
        else:
            root.mkdir(mode=0o700)
            path.touch(mode=0o600)
        with closing(sqlite3.connect(path)) as db, db:
            if damage == "foreign_store":
                db.execute("CREATE TABLE organizational_data (payload TEXT)")
                db.execute("INSERT INTO organizational_data VALUES ('sentinel')")
            elif damage == "future_version":
                db.execute("PRAGMA user_version=999")
            else:
                db.execute("UPDATE local_history_transcripts SET snapshot='[]'")
        before = path.read_bytes()
        with pytest.raises((RuntimeError, ValueError)):
            async with app.router.lifespan_context(app):
                pytest.fail("Damaged local history reached startup")
        assert path.read_bytes() == before
        assert "this run's password" not in capsys.readouterr().out

    run_offline(exercise())


def test_retained_location_must_be_separate_and_single_writer(durable_environment):
    root, make_app = durable_environment

    async def exercise():
        overlapping = make_app("durable", tasks_db_path=root / "production.sqlite3")
        with pytest.raises(ValueError, match="dedicated"):
            async with overlapping.router.lifespan_context(overlapping):
                pytest.fail("Overlapping storage reached startup")
        assert not root.exists()
        first, second = make_app("durable"), make_app("durable")
        async with first.router.lifespan_context(first):
            with pytest.raises(RuntimeError, match="already in use"):
                async with second.router.lifespan_context(second):
                    pytest.fail("Two writers mounted durable history")

    run_offline(exercise())
