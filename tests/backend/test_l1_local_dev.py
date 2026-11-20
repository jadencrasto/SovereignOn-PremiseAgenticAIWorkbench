"""Offline regressions for the explicit local-only application composition."""

import json
import re
import socket
import sys
from unittest.mock import MagicMock
from uuid import uuid4

import anyio
import httpx
import pytest

from backend.config import Settings
from backend.health import routes as health
from backend.local_dev import validate_profile
from backend.main import create_app
from backend.multimodal.document_windows_supervisor import _require_supervised_profile
from test_a22_async_offline import run_offline


def local_settings(**overrides):
    overrides.setdefault("SOVEREIGNAI_LOCAL_HISTORY", "disposable")
    return Settings(_env_file=None, SOVEREIGNAI_RUN_PROFILE="local_dev", **overrides)


@pytest.fixture
def frontend_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>Workbench</title>", encoding="utf-8")
    return dist


def test_local_profile_requires_explicit_opt_in_and_rejects_supervised_selection(monkeypatch):
    monkeypatch.delenv("SOVEREIGNAI_RUN_PROFILE", raising=False)
    assert not Settings(_env_file=None, local_chat_only=True).is_local_dev
    monkeypatch.setenv("SOVEREIGNAI_RUN_PROFILE", "local_dev")
    assert Settings(_env_file=None).is_local_dev
    with pytest.raises(ValueError, match="launch with python -m backend"):
        create_app(Settings(_env_file=None))
    with pytest.raises(RuntimeError, match="Supervised startup requires"):
        _require_supervised_profile(local_settings(auth_enabled=True, local_chat_only=True))


@pytest.mark.parametrize(("operation", "code"), [
    ("create", 10013),
    ("bind", 10048),
    ("listen", 10013),
    ("connect", 10013),
    ("accept", 10060),
    ("set nonblocking mode", 10013),
])
def test_local_launcher_names_failed_operation_before_starting_uvicorn(monkeypatch, capsys, operation, code):
    from backend import __main__ as cli
    from backend import main

    listener = MagicMock()
    probe = MagicMock()
    listener.accept.return_value = (probe, ('127.0.0.1', 0))
    socket_context = MagicMock()
    socket_context.__enter__.return_value = listener
    open_socket = MagicMock(return_value=socket_context)
    connect = MagicMock()
    server = MagicMock()
    monkeypatch.setattr(sys, "argv", ["backend"])
    monkeypatch.setattr(cli, "settings", local_settings(backend_host="127.0.0.1", backend_port=8000))
    monkeypatch.setattr(main, "create_app", MagicMock())
    monkeypatch.setattr(socket, "socket", open_socket)
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(cli.uvicorn, "Server", server)
    failure = {"create": open_socket, "connect": connect, "set nonblocking mode": listener.setblocking}.get(operation)
    if failure is None:
        failure = getattr(listener, operation)
    failure.side_effect = OSError(f"[WinError {code}] synthetic socket failure")

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert f"Local listener {operation} failed (127.0.0.1:8000): [WinError {code}]" in capsys.readouterr().err
    open_socket.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
    if operation == "create":
        socket_context.__exit__.assert_not_called()
    else:
        socket_context.__exit__.assert_called_once()
    server.assert_not_called()
    if operation in {"connect", "accept"}:
        listener.bind.assert_called_once_with(("127.0.0.1", 8000))
        listener.listen.assert_called_once()
        connect.assert_called_once_with(("127.0.0.1", 8000), timeout=2)
    if operation == "set nonblocking mode":
        probe.close.assert_called_once()


@pytest.mark.parametrize(("overrides", "error"), [
    ({"app_env": "production"}, "requires development"),
    ({"environment": "prod"}, "requires development"),
    ({"document_source_restore_pending": True}, "cannot run during restore"),
    ({"backend_host": "0.0.0.0"}, "numeric loopback"),
    ({"backend_host": "localhost"}, "numeric loopback"),
    ({"ollama_base_url": "http://192.0.2.1:11434"}, "unsafe Ollama endpoint"),
    ({"provenance_key_mode": "durable"}, "ephemeral provenance keys"),
])
def test_local_profile_rejects_conflicting_configuration(overrides, error):
    with pytest.raises(ValueError, match=error):
        validate_profile(local_settings(**overrides))


def test_isolated_startup_authentication_and_protected_routes(monkeypatch, tmp_path, frontend_dist, capsys):
    from backend import main

    inventory = health.ModelObservation("unavailable", "ollama_check_failed")

    async def observe_inventory(target, *, local_only):
        assert target == "http://127.0.0.1:11434/api/tags" and local_only
        return inventory

    monkeypatch.setattr(health, "observe_ollama", observe_inventory)
    prod = tmp_path / "production"
    prod.mkdir()
    db = prod / "auth.sqlite3"
    history = prod / "history.json"
    db.write_bytes(b"production-db")
    history.write_bytes(b"production-history")
    modes = []
    actual_keys = main.startup_provenance_keys

    def track_keys(mode):
        modes.append(mode)
        return actual_keys(mode)

    monkeypatch.setattr(main, "startup_provenance_keys", track_keys)
    cfg = local_settings(
        auth_enabled=False, auth_dev_override=True, allow_dev_clearance_simulation=True,
        tasks_db_path=db, conversation_memory_path=history, config_dir=prod, tasks_dir=prod,
        upload_dir=prod, sandbox_dir=prod, log_dir=prod, chroma_persist_dir=prod,
        auth_cookie_domain="example.invalid", cors_origins="*",
    )
    app = create_app(cfg, local_frontend=frontend_dist)

    async def run_sync(fn, *args, **kwargs):
        return fn(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_sync)

    async def exercise():
        nonlocal inventory
        origin = "http://127.0.0.1:8000"
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url=origin) as client:
            assert (await client.get("/")).status_code == 200
            assert "default-src 'self'" in (await client.get("/")).headers["content-security-policy"]
            assert (await client.get("/api/health/ready")).json()["ready"]
            status = (await client.get("/api/health/capabilities")).json()
            assert status["run_profile"] == "local_dev" and status["deployment_verified"] is False
            assert status["selected_chat_model"]["reason"] == "ollama_check_failed"
            root = app.state.settings.tasks_dir
            assert root != prod and app.state.settings.tasks_db_path.is_file()
            assert app.state.settings.conversation_memory_path.parent == root
            assert app.state.settings.config_dir != prod
            assert app.state.settings.auth_enabled and not app.state.settings.allow_dev_clearance_simulation
            assert not hasattr(app.state, "source_recovery_registration")
            assert not hasattr(app.state, "deployment_documents")
            registry = app.state.engine._tool_registry
            assert {tool.name for tool in registry.list_enabled_tools()} == {
                "calculator", "unit_conversion", "local_grounded_draft",
            }
            draft_tool = registry.get("local_grounded_draft")
            assert draft_tool.read_only and draft_tool.risk_level == "low"
            # Draft assembly has a server-issued turn context. Enabling the
            # projection locally does not grant generic registry execution.
            direct = await registry.execute("local_grounded_draft", {}, user_id=app.state.local_viewer_id,
                                            user_role="viewer")
            assert not direct.success
            assert app.state.engine._doc_service is None
            assert not app.state.auth_store.get_user_by_username("admin")
            credentials = {"X-User-Role": "admin", "X-Clearance-Level": "L3", "X-Requested-With": "XMLHttpRequest"}
            client.cookies.set("session_token", "synthetic-production-cookie")
            assert (await client.get("/api/auth/me", headers=credentials)).status_code == 401
            assert (await client.get("/api/health/capabilities", headers={"Host": "other.invalid"})).status_code == 403
            assert (await client.post("/api/auth/login", headers={"Origin": "https://other.invalid"})).status_code == 403
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 12345)), base_url=origin) as other:
                assert (await other.get("/")).status_code == 403
            password = re.search(r"this run's password: (\S+)", capsys.readouterr().out)[1]
            login = await client.post("/api/auth/login", json={"username": "local_dev", "password": password})
            assert login.status_code == 200
            assert "sovereign_local_dev_session=" in login.headers["set-cookie"]
            assert "SameSite=strict" in login.headers["set-cookie"]
            me = (await client.get("/api/auth/me", headers=credentials)).json()
            assert (me["role"], me["clearance"]) == ("viewer", "L1")
            assert (await client.post("/api/chat/sessions")).status_code == 403
            created = await client.post("/api/chat/sessions", headers=credentials)
            assert created.status_code == 201
            session_id = created.json()["session_id"]
            assert (await client.get("/api/auth/users")).status_code >= 400
            for path in ("/api/chat", "/api/chat/multimodal", "/api/chat/text-file"):
                assert (await client.post(path, files={"file": ("source.pdf", b"%PDF")}, headers=credentials)).status_code == 415
            assert (await client.get("/api/documents")).status_code == 503
            assert (await client.get("/api/artifacts")).status_code == 403
            assert (await client.post("/api/tools/execute")).status_code == 403
            assert (await client.post("/api/chat", json={"message": "Hello", "tools_enabled": True}, headers=credentials)).status_code == 403
            response = await client.post("/api/chat", json={
                "message": "Hello", "session_id": session_id, "turn_id": str(uuid4()), "stream": False,
            }, headers=credentials)
            assert response.status_code == 503 and "Start local Ollama" in response.json()["detail"]

            router = app.state.model_router
            model = router.default_model_id.removeprefix("ollama/")
            inventory = health.ModelObservation("available", "inventory_observed", (model,), ((model, "a" * 64),))
            provider = router.get_provider("ollama")
            await provider._client.aclose()
            captured = []

            def synthetic_inference(request):
                assert request.url.path == "/api/chat"
                body = json.loads(request.content)
                assert body["stream"] is True and body["model"] == model
                captured.append(body["messages"])
                return httpx.Response(200, content=json.dumps({
                    "model": model, "done": True, "message": {"role": "assistant", "content": "Synthetic answer."},
                }) + "\n")

            provider._client = httpx.AsyncClient(
                base_url=provider._base_url, transport=httpx.MockTransport(synthetic_inference), trust_env=False,
            )
            for prompt in ("Hello", "What did I just say?"):
                reply = await client.post("/api/chat", json={
                    "message": prompt, "session_id": session_id, "turn_id": str(uuid4()),
                }, headers=credentials)
                assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/event-stream")
                events = [json.loads(line[6:]) for line in reply.text.splitlines() if line.startswith("data: ")]
                assert events[-1]["type"] == "done"
                assert "".join(event["content"] for event in events if event["type"] == "delta") == "Synthetic answer."
            assert [message["content"] for message in captured[1] if message["role"] == "user"] == [
                "Hello", "What did I just say?",
            ]
            assert any(message["role"] == "assistant" for message in captured[1])
            history = (await client.get(f"/api/chat/sessions/{session_id}")).json()
            assert [message["content"] for message in history["messages"] if message["role"] == "assistant"] == [
                "Synthetic answer.", "Synthetic answer.",
            ]
            assert (await client.post("/api/auth/logout", headers=credentials)).status_code == 200
            assert (await client.get("/api/auth/me")).status_code == 401
        assert not root.exists() and app.state.settings is cfg

    run_offline(exercise())
    assert modes == ["ephemeral"]
    assert db.read_bytes() == b"production-db" and history.read_bytes() == b"production-history"
    assert set(prod.iterdir()) == {db, history}


def test_local_inventory_excludes_remote_or_unverifiable_models(monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, stream=httpx.ByteStream(json.dumps({"models": [
            {"name": "local:7b", "size": 1000, "digest": "a" * 64},
            {"name": "remote:7b", "size": 100, "digest": "b" * 64, "remote_model": "remote"},
            {"name": "other:7b-cloud", "size": 100, "digest": "c" * 64},
            {"name": "missing-digest:7b", "size": 100},
        ]}).encode()),
    ))
    client_factory = httpx.AsyncClient
    monkeypatch.setattr(health.httpx, "AsyncClient", lambda **kwargs: client_factory(transport=transport, **kwargs))
    observation = run_offline(health.observe_ollama("http://127.0.0.1:11434/api/tags", local_only=True))
    assert observation.models == ("local:7b",)
    assert observation.digests == (("local:7b", "a" * 64),)
