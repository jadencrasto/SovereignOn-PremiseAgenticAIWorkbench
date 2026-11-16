"""In-process tests for request-scoped UTF-8 preview and understanding."""
import asyncio
import json
from contextlib import aclosing
from types import SimpleNamespace

import httpx
import pytest

from backend.models.base import ChatChunk
from backend.multimodal.text_file import MAX_TEXT_FORM_BYTES
from backend.security.request_limits import MultipartBodyLimitMiddleware
from test_a22_async_offline import run_offline
from test_a61_multimodal_offline import image_chat_app


BASE_URL = "http://synthetic"
PREVIEW = "/api/chat/text-preview"
ASK = "/api/chat/text-file"
AUTH = {"Authorization": "Bearer a"}
SOURCE = "Maintenance note: amber switch. नमस्ते.\n"
ANSWER = "The switch is amber."


class Provider:
    provider_name = "ollama"
    _base_url = "http://127.0.0.1:11434"

    def __init__(self):
        self.requests = []

    async def chat_stream(self, request):
        self.requests.append(request)
        yield ChatChunk(ANSWER if request.max_tokens == 384 else "Hello.", True)


def app_with_text_profile(tmp_path, provider=None):
    app = image_chat_app(tmp_path, provider or Provider())
    app.state.settings.local_chat_only = True
    return app


def text_upload(content=SOURCE.encode("utf-8"), name="note.txt", mime="text/plain"):
    return {"file": (name, content, mime)}


def ask_form(**fields):
    return {"session_id": "session-a", "model": "ollama/text", "message": "Which switch?", **fields}


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_preview_is_utf8_bounded_owner_scoped_and_independent_of_inference(tmp_path):
    provider = Provider()
    app = app_with_text_profile(tmp_path, provider)
    app.state.inference_observation = None
    data = ("<script>inert</script>\nनमस्ते café\n" + "a" * 900).encode("utf-8-sig")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client:
            assert (await client.post(PREVIEW, files=text_upload(data))).status_code == 401
            assert (await client.post(PREVIEW, files=text_upload(data),
                                      headers={"Authorization": "Bearer b"},
                                      data={"session_id": "session-a"})).status_code == 404
            preview = await client.post(PREVIEW, files=text_upload(data), headers=AUTH)
            assert preview.status_code == 200
            assert preview.headers["Cache-Control"] == "no-store"
            body = preview.json()
            assert body["filename"] == "note.txt"
            assert body["size_bytes"] == len(data)
            assert body["truncated"] is True
            assert len(body["text"]) == 800 and body["text"].startswith("<script>inert</script>\nनमस्ते café")
            assert (await client.post(PREVIEW, headers=AUTH, files=text_upload(mime="application/octet-stream"),
                                      data={"session_id": "session-a"})).json()["text"] == SOURCE
            assert (await client.post(PREVIEW, headers=AUTH,
                                      files=text_upload(name="review__notes.txt", mime="text/plain; charset=UTF-8"))).status_code == 200
        assert provider.requests == []
        assert SOURCE not in (tmp_path / "memory.json").read_text(encoding="utf-8")

    run_offline(exercise())


@pytest.mark.parametrize("content,name,mime,status", [
    (b"", "note.txt", "text/plain", 422),
    (b" \t\n", "note.txt", "text/plain", 422),
    (b"x" * 2049, "note.txt", "text/plain", 413),
    (b"x" * 1201, "note.txt", "text/plain", 413),
    (b"\xff", "note.txt", "text/plain", 422),
    (b"a\x00b", "note.txt", "text/plain", 422),
    (b"\x1b[2J", "note.txt", "text/plain", 422),
    ("%PDF-1.4".encode(), "note.txt", "text/plain", 422),
    ("text\u202e".encode(), "note.txt", "text/plain", 422),
    (b"text", "../note.txt", "text/plain", 422),
    (b"text", "note.md", "text/plain", 415),
    (b"text", "note.txt", "application/pdf", 415),
    (b"text", "note.txt", "text/plain; charset=UTF-16", 415),
])
def test_preview_rejects_invalid_bytes_and_declared_types(tmp_path, content, name, mime, status):
    app = app_with_text_profile(tmp_path)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, headers=AUTH) as client:
            response = await client.post(PREVIEW, files=text_upload(content, name, mime))
            assert response.status_code == status
            assert response.headers["X-Error-Code"].startswith("TEXT_FILE_")
            assert "text" not in response.json()

    run_offline(exercise())


def test_malformed_duplicate_and_oversized_multipart(tmp_path):
    app = app_with_text_profile(tmp_path)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, headers=AUTH) as client:
            for kwargs in (
                {"content": b"malformed", "headers": {"Content-Type": "multipart/form-data; boundary=x"}},
                {"content": b"malformed", "headers": {"Content-Type": "multipart/form-data"}},
                {"files": [("file", ("one.txt", b"one")), ("file", ("two.txt", b"two"))]},
                {"files": text_upload(), "data": {"unexpected": "field"}},
                {"files": [("file", ("note.txt", b"Line one\nLine two", "text/plain")),
                           ("line_start", (None, "1")), ("line_start", (None, "2")), ("line_end", (None, "2"))]},
                {"content": b'--x\r\nContent-Disposition: form-data; name="file"; filename="note.txt"\r\n'
                            b'Content-Type: text/plain\r\n\r\nIncomplete\r\n--x\r\n',
                 "headers": {"Content-Type": "multipart/form-data; boundary=x"}},
            ):
                response = await client.post(PREVIEW, **kwargs)
                assert response.status_code == 400 and response.headers["X-Error-Code"] == "TEXT_FILE_FORM"
            assert (await client.post(PREVIEW, content=b"x" * (MAX_TEXT_FORM_BYTES + 1))).status_code == 413

    run_offline(exercise())


def test_preview_and_inference_select_the_same_lines_beyond_the_initial_preview(tmp_path):
    provider = Provider()
    app = app_with_text_profile(tmp_path, provider)
    app.state.engine._retrieve_context = lambda *args, **kwargs: pytest.fail("Unexpected retrieval")
    app.state.engine._dispatch_tool = lambda *args, **kwargs: pytest.fail("Unexpected tool dispatch")
    excerpt = "Amber switch\r\n\r"
    source = "x" * 810 + "\r\n" + excerpt + "Outside the range.\n"
    upload = text_upload(source.encode("utf-8-sig"))
    bounds = {"line_start": "2", "line_end": "3"}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, headers=AUTH) as client:
            whole = (await client.post(PREVIEW, files=upload)).json()
            assert whole["line_count"] == 4
            assert whole["truncated"] is True and "Amber switch" not in whole["text"]
            scoped = await client.post(PREVIEW, files=upload, data={"session_id": "session-a", **bounds})
            assert scoped.status_code == 200 and scoped.headers["Cache-Control"] == "no-store"
            assert scoped.json()["text"] == excerpt
            assert scoped.json()["truncated"] is False
            long_line = (await client.post(PREVIEW, files=upload, data={"line_start": "1", "line_end": "1"})).json()
            assert long_line["truncated"] is True and long_line["text"] == "x" * 800
            for url, form in ((PREVIEW, {"session_id": "session-a", **bounds}), (ASK, ask_form(**bounds))):
                denied = await client.post(url, files=upload, data=form, headers={"Authorization": "Bearer b"})
                assert denied.status_code == 404
            assert provider.requests == []
            answer = await client.post(ASK, files=upload, data=ask_form(**bounds))
            assert [event["type"] for event in events(answer)] == ["agent_status", "delta", "done"]
            prompt = provider.requests[0].messages[1].content
            assert json.loads(prompt) == {"question": "Which switch?", "untrusted_file_text": excerpt}
            history = (await client.get("/api/chat/sessions/session-a")).json()["messages"]
            assert set(history[0]["attachments"][0]) == {"attachment_id", "filename", "mime_type", "size_bytes"}
            assert "Amber switch" not in json.dumps(history)
            assert (await client.get("/api/chat/sessions/s")).json()["messages"] == []

    run_offline(exercise())
    assert "Amber switch" not in (tmp_path / "memory.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("bounds", [
    {"line_start": "1"}, {"line_end": "2"}, {"line_start": "", "line_end": ""},
    {"line_start": "0", "line_end": "1"}, {"line_start": "1.0", "line_end": "2"},
    {"line_start": "3", "line_end": "1"}, {"line_start": "1", "line_end": "4"},
    {"line_start": "2", "line_end": "2"},
])
def test_invalid_line_ranges_fail_before_preview_or_inference(tmp_path, bounds):
    provider = Provider()
    app = app_with_text_profile(tmp_path, provider)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, headers=AUTH) as client:
            for url, form in ((PREVIEW, bounds), (ASK, ask_form(**bounds))):
                error = await client.post(url, files=text_upload(b"First\r\n\r\nLast\n"), data=form)
                assert error.status_code == 422
                assert error.headers["X-Error-Code"] == "TEXT_FILE_RANGE_INVALID"
                assert error.headers["Cache-Control"] == "no-store"
            assert (await client.get("/api/chat/sessions/session-a")).json()["messages"] == []
        assert provider.requests == []

    run_offline(exercise())


def test_text_file_answers_never_enter_history_or_protected_services(tmp_path):
    provider = Provider()
    app = app_with_text_profile(tmp_path, provider)
    app.state.engine._retrieve_context = lambda *args, **kwargs: pytest.fail("Unexpected retrieval")
    app.state.engine._dispatch_tool = lambda *args, **kwargs: pytest.fail("Unexpected tool dispatch")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, headers=AUTH) as client:
            cross_owner = await client.post(ASK, data=ask_form(), files=text_upload(),
                                            headers={"Authorization": "Bearer b"})
            assert cross_owner.status_code == 404
            response = await client.post(ASK, data=ask_form(), files=text_upload())
            assert [event["type"] for event in events(response)] == ["agent_status", "delta", "done"]
            sent = provider.requests[0]
            assert sent.model == "text" and sent.max_tokens == 384
            assert [message.role for message in sent.messages] == ["system", "user"]
            assert SOURCE not in sent.messages[0].content
            assert json.loads(sent.messages[1].content) == {"question": "Which switch?", "untrusted_file_text": SOURCE}
            for prompt in ("Summarize the earlier file.", "What does it say?"):
                earlier = await client.post("/api/chat", json={"session_id": "session-a", "message": prompt})
                assert [event["error_code"] for event in events(earlier)] == ["TEXT_FILE_REATTACH_REQUIRED"]
            comparison = await client.post(ASK, data=ask_form(message="Compare with the earlier file."), files=text_upload())
            assert comparison.status_code == 409
            assert len(provider.requests) == 1
            response = await client.get("/api/chat/sessions/session-a")
            messages = response.json()["messages"]
            assert len(messages) == 1 and messages[0]["role"] == "user"
            assert messages[0]["attachments"][0]["filename"] == "note.txt"
            assert SOURCE.strip() not in response.text and ANSWER not in response.text
            assert (await client.get("/api/chat/sessions/s")).json()["messages"] == []
            # The ordinary text pipeline can continue, but neither history marker nor transient text is model context.
            async def empty_context(*args, **kwargs):
                return []

            app.state.engine._retrieve_context = empty_context
            for session_id in ("session-a", "s"):
                ordinary = await client.post("/api/chat", json={"session_id": session_id, "message": "Hello",
                                                                 "tools_enabled": False})
                assert events(ordinary)[-1]["type"] == "done"
                sent_messages = " ".join(message.content for message in provider.requests[-1].messages)
                assert "Maintenance note" not in sent_messages and "attached text file" not in sent_messages
            assert (await client.get("/api/chat/sessions/session-a",
                                     headers={"Authorization": "Bearer b"})).status_code == 404
            assert (await client.delete("/api/chat/sessions/session-a",
                                        headers={"Authorization": "Bearer b"})).status_code == 404
            assert (await client.delete("/api/chat/sessions/session-a")).status_code == 200
            assert (await client.get("/api/chat/sessions/session-a")).status_code == 404
    run_offline(exercise())
    stored = (tmp_path / "memory.json").read_text(encoding="utf-8")
    assert "Maintenance note" not in stored and ANSWER not in stored
    assert {path.name for path in tmp_path.iterdir()} <= {"memory.json", "memory.json.lock", "registry.db",
                                                          "registry.db-wal", "registry.db-shm"}


def test_text_file_understanding_rejects_other_profiles_unready_models_and_wrong_adapter(tmp_path):
    provider = Provider()
    app = app_with_text_profile(tmp_path, provider)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, headers=AUTH) as client:
            app.state.settings.local_chat_only = False
            assert (await client.post(ASK, data=ask_form(), files=text_upload())).status_code == 403
            app.state.settings.local_chat_only = True
            assert (await client.post(ASK, data=ask_form(model="ollama/unknown"), files=text_upload())).status_code == 503
            record = app.state.inference_observation
            app.state.inference_observation = None
            assert (await client.post(ASK, data=ask_form(), files=text_upload())).status_code == 503
            app.state.inference_observation = record
            provider._base_url = "http://127.0.0.1:65535"
            denied = await client.post(ASK, data=ask_form(), files=text_upload())
            assert events(denied)[-1]["error_code"] == "MODEL_NOT_READY"
        assert not provider.requests

    run_offline(exercise())


@pytest.mark.parametrize("result", ["failure", "incomplete", "tool", "oversized"])
def test_failed_model_answers_have_no_delta_done_or_history(tmp_path, result):
    class FailedProvider(Provider):
        async def chat_stream(self, request):
            if result == "failure":
                raise RuntimeError(SOURCE)
            if result == "incomplete":
                yield ChatChunk("Partial answer", False)
            elif result == "tool":
                yield ChatChunk('<tool_call>{"name":"file_write"}</tool_call>', True)
            else:
                yield ChatChunk("a" * 4097, True)

    app = app_with_text_profile(tmp_path, FailedProvider())

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, headers=AUTH) as client:
            response = await client.post(ASK, data=ask_form(), files=text_upload())
            assert [event["type"] for event in events(response)] == ["agent_status", "error"]
            assert "Maintenance note" not in response.text
            assert (await client.get("/api/chat/sessions/session-a")).json()["messages"] == []

    run_offline(exercise())


def test_cancelled_text_inference_closes_provider_and_leaves_no_history(tmp_path):
    from backend.api.chat import _stream_text_file
    from backend.auth.request_context import dispatch_identity_resolver, effective_owner

    async def exercise():
        started, closed = asyncio.Event(), asyncio.Event()

        class PendingProvider(Provider):
            async def chat_stream(self, request):
                try:
                    started.set()
                    await asyncio.Event().wait()
                    yield ChatChunk(ANSWER, True)
                finally:
                    closed.set()

        app = app_with_text_profile(tmp_path, PendingProvider())
        engine = app.state.engine
        owner_token = effective_owner.set("a")
        resolver_token = dispatch_identity_resolver.set(lambda: app.state.auth_store.get_user_by_id("a"))
        try:
            identity = engine._memory.stream_identity("session-a")
            async with aclosing(_stream_text_file(engine, "session-a", SimpleNamespace(app=app), "a", "ollama/text",
                                                  "Which switch?", SOURCE, {},
                                                  guard_initial_identity=identity)) as stream:
                assert "agent_status" in await anext(stream)
                pending = asyncio.create_task(anext(stream))
                await started.wait()
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            assert closed.is_set()
            assert all(message.role == "system" for message in engine._memory.get_history("session-a"))
        finally:
            dispatch_identity_resolver.reset(resolver_token)
            effective_owner.reset(owner_token)

    run_offline(exercise())


def test_revoked_session_discards_completed_text_file_answer(tmp_path):
    class RevokingProvider(Provider):
        async def chat_stream(self, request):
            app.state.session_manager.validate_session.side_effect = lambda token: None
            yield ChatChunk(ANSWER, True)

    app = app_with_text_profile(tmp_path, RevokingProvider())
    validate_session = app.state.session_manager.validate_session.side_effect

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, headers=AUTH) as client:
            response = await client.post(ASK, data=ask_form(), files=text_upload())
            assert [event["type"] for event in events(response)] == ["agent_status", "error"]
            assert events(response)[-1]["error_code"] == "TEXT_FILE_AUTH_CHANGED"
            assert ANSWER not in response.text
            app.state.session_manager.validate_session.side_effect = validate_session
            assert (await client.get("/api/chat/sessions/session-a")).json()["messages"] == []

    run_offline(exercise())


def test_upload_disconnect_stops_before_text_form_parser():
    incoming = iter([{"type": "http.request", "body": b"partial", "more_body": True},
                     {"type": "http.disconnect"}])

    async def receive():
        return next(incoming)

    async def unreachable(*args):
        pytest.fail("Disconnected upload must not reach the form parser or publish a response")

    app = MultipartBodyLimitMiddleware(unreachable, max_file_bytes=1024 * 1024)
    run_offline(app({"type": "http", "path": PREVIEW, "method": "POST", "headers": []}, receive, unreachable))
