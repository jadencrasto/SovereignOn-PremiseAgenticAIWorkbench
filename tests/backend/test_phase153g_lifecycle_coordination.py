import threading

import pytest

from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner


def test_registry_mutation_rejected_outside_lifecycle_lock(tmp_path):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
    with pytest.raises(RuntimeError, match='conversation lock'):
        memory._lifecycle_registry_mutation('begin', 's', 'a')
    assert registry.rows() == []


def test_reconciliation_waits_for_creation_and_keeps_identity_active(tmp_path, monkeypatch):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    path = tmp_path / 'memory.json'
    creator = ConversationMemory(path, security_registry=registry)
    written = threading.Event()
    release = threading.Event()
    failures = []
    original_save = creator._save

    def pause_after_save():
        original_save()
        written.set()
        if not release.wait(5):
            raise TimeoutError('test release timed out')

    monkeypatch.setattr(creator, '_save', pause_after_save)
    def create():
        token = effective_owner.set('a')
        try:
            creator.create_session(session_id='s')
        except Exception as exc:
            failures.append(exc)
        finally:
            effective_owner.reset(token)
    def reconcile():
        try:
            ConversationMemory(path, security_registry=registry)
        except Exception as exc:
            failures.append(exc)

    first = threading.Thread(target=create, daemon=True)
    second = threading.Thread(target=reconcile, daemon=True)
    first.start()
    assert written.wait(5)
    second.start()
    try:
        assert registry.rows()[0]['state'] == 'pending'
    finally:
        release.set()
    first.join(8)
    second.join(8)
    assert not first.is_alive() and not second.is_alive(), 'lifecycle operation timed out'
    assert not failures
    assert registry.rows()[0]['state'] == 'active'


def test_registry_lock_error_propagates_without_json_write(tmp_path, monkeypatch):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    path = tmp_path / 'memory.json'
    memory = ConversationMemory(path, security_registry=registry)
    monkeypatch.setattr(registry, 'begin', lambda *args: (_ for _ in ()).throw(TimeoutError('synthetic lock failure')))
    token = effective_owner.set('a')
    try:
        with pytest.raises(TimeoutError, match='synthetic lock failure'):
            memory.create_session(session_id='s')
    finally:
        effective_owner.reset(token)
    assert not path.exists() and registry.rows() == []
