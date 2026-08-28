"""
tests/backend/test_api.py
-------------------------
Integration tests for the FastAPI endpoints — Phase 2.

Engine and RAG services are mocked — no live Ollama or ChromaDB required.

Backward-compatible: all Phase 1 tests still pass.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from backend.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_engine():
    """Mock AgentEngine returning (content, sources) tuple for non-streaming."""
    engine = MagicMock()
    engine.chat = AsyncMock(return_value=("Hello from mock assistant!", []))

    async def fake_stream(session_id, message, model_id=None):
        for word in ["Hello ", "from ", "mock!"]:
            yield word
        yield []  # sources sentinel

    engine.chat_stream = fake_stream
    engine._memory = MagicMock()
    engine._memory.list_sessions.return_value = []
    engine._memory.session_count.return_value = 0
    return engine


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.default_model_id = "ollama/qwen2.5:7b"
    router.resolve_model.return_value = ("ollama", "qwen2.5:7b")
    router.list_available_models = AsyncMock(return_value={"ollama": ["qwen2.5:7b"]})
    return router


@pytest.fixture
def mock_doc_service():
    svc = MagicMock()
    svc.list_documents.return_value = []
    svc.ingest_document = AsyncMock(return_value=MagicMock(
        document_id="doc_abc123",
        filename="test.txt",
        file_type="txt",
        chunk_count=3,
        status="indexed",
    ))
    svc.delete_document.return_value = 3
    return svc


@pytest.fixture
def client(mock_engine, mock_router, mock_doc_service):
    with TestClient(app) as c:
        # Override state AFTER lifespan has set up real services
        c.app.state.engine = mock_engine
        c.app.state.model_router = mock_router
        c.app.state.doc_service = mock_doc_service
        c.app.state.ollama_ok = True
        c.app.state.embed_ok = True
        yield c


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_health_body(self, client):
        data = client.get("/api/health").json()
        assert data["status"] == "ok"
        assert data["service"] == "sovereign-workbench"
        assert "version" in data
        assert "model_provider" in data
        assert "default_model" in data


# ---------------------------------------------------------------------------
# Chat — non-streaming
# ---------------------------------------------------------------------------

class TestChatNonStreaming:
    def test_chat_returns_200(self, client):
        resp = client.post("/api/chat", json={"message": "Hello!", "stream": False})
        assert resp.status_code == 200

    def test_chat_response_schema(self, client):
        data = client.post("/api/chat", json={"message": "Hello!", "stream": False}).json()
        assert "session_id" in data
        assert data["message"]["role"] == "assistant"
        assert "model_used" in data
        # sources field present (may be null)
        assert "sources" in data

    def test_chat_empty_message_rejected(self, client):
        resp = client.post("/api/chat", json={"message": "", "stream": False})
        assert resp.status_code == 422

    def test_chat_session_id_reused(self, client):
        r1 = client.post("/api/chat", json={"message": "Hi", "stream": False})
        sid = r1.json()["session_id"]
        r2 = client.post("/api/chat", json={"message": "Follow-up", "session_id": sid, "stream": False})
        assert r2.json()["session_id"] == sid


# ---------------------------------------------------------------------------
# Chat — streaming
# ---------------------------------------------------------------------------

class TestChatStreaming:
    def test_stream_returns_200(self, client):
        with client.stream("POST", "/api/chat", json={"message": "Hello!", "stream": True, "tools_enabled": False}) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]

    def test_stream_emits_sse_events(self, client):
        with client.stream("POST", "/api/chat", json={"message": "Hello!", "stream": True, "tools_enabled": False}) as r:
            events = list(r.iter_lines())
        data_lines = [l for l in events if l.startswith("data: ")]
        assert len(data_lines) > 0

    def test_stream_final_event_is_done(self, client):
        with client.stream("POST", "/api/chat", json={"message": "Hello!", "stream": True, "tools_enabled": False}) as r:
            lines = list(r.iter_lines())
        data_lines = [l[6:] for l in lines if l.startswith("data: ")]
        last = json.loads(data_lines[-1])
        assert last["type"] == "done"
        assert "session_id" in last

    def test_stream_has_delta_events(self, client):
        with client.stream("POST", "/api/chat", json={"message": "Hello!", "stream": True, "tools_enabled": False}) as r:
            lines = list(r.iter_lines())
        data_lines = [json.loads(l[6:]) for l in lines if l.startswith("data: ")]
        delta_events = [e for e in data_lines if e["type"] == "delta"]
        assert len(delta_events) >= 1


# ---------------------------------------------------------------------------
# Models endpoint
# ---------------------------------------------------------------------------

class TestModels:
    def test_list_models_returns_200(self, client):
        assert client.get("/api/models").status_code == 200

    def test_list_models_has_default(self, client):
        data = client.get("/api/models").json()
        assert "default" in data
        assert "providers" in data


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class TestSessions:
    def test_list_sessions(self, client):
        data = client.get("/api/chat/sessions").json()
        assert "sessions" in data
        assert "count" in data


# ---------------------------------------------------------------------------
# Documents endpoint
# ---------------------------------------------------------------------------

class TestDocuments:
    def test_list_documents_empty(self, client):
        resp = client.get("/api/documents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["documents"] == []
        assert data["total"] == 0

    def test_upload_txt_document(self, client):
        content = b"This is a test document about machine learning."
        resp = client.post(
            "/api/documents",
            files={"file": ("test.txt", content, "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["document_id"] == "doc_abc123"
        assert data["filename"] == "test.txt"
        assert data["chunks"] == 3
        assert data["status"] == "indexed"

    def test_upload_invalid_extension(self, client, mock_doc_service):
        from backend.rag.service import DocumentService
        mock_doc_service.ingest_document = AsyncMock(
            side_effect=ValueError("File type '.exe' is not allowed.")
        )
        resp = client.post(
            "/api/documents",
            files={"file": ("malware.exe", b"content", "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"].lower()

    def test_upload_no_filename(self, client):
        # FastAPI returns 422 (Unprocessable Entity) when no file is provided
        # because 'file' is a required field
        resp = client.post(
            "/api/documents",
            files={"file": ("", b"content", "text/plain")},
        )
        # Empty filename → 400 from our validation OR 422 from FastAPI
        # Both are acceptable rejection responses
        assert resp.status_code in (400, 422)

    def test_delete_document(self, client):
        # The mock is already set to return 3 chunks deleted
        client.app.state.doc_service.delete_document.return_value = 3
        client.app.state.doc_service.delete_document.side_effect = None
        resp = client.delete("/api/documents/doc_abc123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["document_id"] == "doc_abc123"
        assert data["chunks_deleted"] == 3

    def test_delete_nonexistent_document(self, client, mock_doc_service):
        mock_doc_service.delete_document.side_effect = ValueError("not found")
        resp = client.delete("/api/documents/nonexistent")
        assert resp.status_code == 404
