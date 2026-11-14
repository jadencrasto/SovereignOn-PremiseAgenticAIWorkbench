from unittest.mock import AsyncMock
import pytest

from fastapi.testclient import TestClient

from backend.config import Settings


class EmptyVectorStore:
    def __init__(self, persist_dir):
        pass

    def count(self):
        return 0

    def list_documents(self):
        return []


def test_application_lifespan_uses_one_registry_and_isolated_memory(tmp_path, monkeypatch):
    import backend.main as main
    from backend.auth.request_context import effective_owner
    cfg = Settings(app_env='development', auth_enabled=False,
                   upload_dir=tmp_path / 'uploads', log_dir=tmp_path / 'logs',
                   sandbox_dir=tmp_path / 'sandbox', chroma_persist_dir=tmp_path / 'chroma',
                   tasks_dir=tmp_path / 'tasks', tasks_db_path=tmp_path / 'tasks' / 'tasks.db',
                   conversation_memory_path=tmp_path / 'memory.json')
    monkeypatch.setattr(main, 'VectorStore', EmptyVectorStore)
    monkeypatch.setattr(main.EmbeddingService, 'health_check', AsyncMock(return_value=False))
    monkeypatch.setattr('backend.models.ollama_provider.OllamaProvider.health_check', AsyncMock(return_value=False))
    app = main.create_app(cfg)
    with TestClient(app):
        memory = app.state.engine._memory
        registry = app.state.conversation_registry
        assert memory._security_registry is registry
        token = effective_owner.set('synthetic-user')
        try:
            memory.create_session(session_id='explicit')
            memory.add_user_message('implicit', 'synthetic')
        finally:
            effective_owner.reset(token)
        assert {row['conversation_id'] for row in registry.rows()} == {'explicit', 'implicit'}
        assert all(row['state'] == 'active' for row in registry.rows())
    app2 = main.create_app(cfg)
    with TestClient(app2):
        assert len(app2.state.conversation_registry.rows()) == 2


def test_application_startup_rejects_reconciliation_failure(tmp_path, monkeypatch):
    import backend.main as main
    from backend.agent.conversation_registry import ConversationRegistry
    cfg = Settings(app_env='development', auth_enabled=False,
                   upload_dir=tmp_path / 'uploads', log_dir=tmp_path / 'logs',
                   sandbox_dir=tmp_path / 'sandbox', chroma_persist_dir=tmp_path / 'chroma',
                   tasks_dir=tmp_path / 'tasks', tasks_db_path=tmp_path / 'tasks' / 'tasks.db',
                   conversation_memory_path=tmp_path / 'memory.json')
    monkeypatch.setattr(ConversationRegistry, 'rows', lambda self: (_ for _ in ()).throw(OSError('synthetic failure')))
    with pytest.raises(OSError, match='synthetic failure'):
        with TestClient(main.create_app(cfg)):
            pytest.fail('startup unexpectedly succeeded')
