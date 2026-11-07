"""Deterministic configuration tests; these do not prove OS network isolation."""
import os
from types import ModuleType
from unittest.mock import MagicMock
import pytest


def test_chroma_settings_applied_before_client_import(tmp_path, monkeypatch):
    from backend.rag.store import _create_local_chroma_client

    observed = {}
    real_import = __import__

    class FakeSettings:
        def __init__(self, **kwargs):
            observed['settings'] = kwargs

    fake_chroma = ModuleType('chromadb')
    fake_chroma.__path__ = []
    fake_config = ModuleType('chromadb.config')
    fake_config.Settings = FakeSettings
    fake_chroma.PersistentClient = MagicMock(return_value='client')

    def controlled_import(name, *args, **kwargs):
        if name == 'chromadb':
            observed['before_import'] = (
                os.environ.get('ANONYMIZED_TELEMETRY'),
                os.environ.get('CHROMA_OTEL_GRANULARITY'),
                os.environ.get('CHROMA_OTEL_COLLECTION_ENDPOINT'),
            )
            return fake_chroma
        if name == 'chromadb.config':
            return fake_config
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr('builtins.__import__', controlled_import)
    monkeypatch.setenv('ANONYMIZED_TELEMETRY', 'True')
    monkeypatch.setenv('CHROMA_OTEL_GRANULARITY', 'all')
    monkeypatch.setenv('CHROMA_OTEL_COLLECTION_ENDPOINT', 'https://example.invalid')
    assert _create_local_chroma_client(tmp_path) == 'client'
    assert observed['before_import'] == ('False', 'none', '')
    assert observed['settings'] == {
        'anonymized_telemetry': False,
        'chroma_otel_granularity': 'none',
        'chroma_otel_collection_endpoint': '',
    }
    assert fake_chroma.PersistentClient.call_args.kwargs['path'] == str(tmp_path)


def test_telemetry_configuration_failure_does_not_fall_back(tmp_path, monkeypatch):
    from backend.rag import store

    def fail(_):
        raise store.ChromaTelemetryConfigurationError('unsupported')

    monkeypatch.setattr(store, '_create_local_chroma_client', fail)
    with pytest.raises(store.ChromaTelemetryConfigurationError):
        store.VectorStore(tmp_path / 'index')


def test_collection_uses_only_explicit_embeddings(tmp_path, monkeypatch):
    from backend.rag import store
    client = MagicMock()
    collection = client.get_or_create_collection.return_value
    collection.count.return_value = 0
    collection.get.return_value = {'ids': [], 'metadatas': []}
    monkeypatch.setattr(store, '_create_local_chroma_client', lambda _: client)
    vector_store = store.VectorStore(tmp_path / 'index')
    assert client.get_or_create_collection.call_args.kwargs['embedding_function'] is None
    vector_store.clear_collection()
    assert client.get_or_create_collection.call_args.kwargs['embedding_function'] is None
