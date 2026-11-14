import json
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner


def test_create_restart_delete_recreate(tmp_path):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        path = tmp_path / 'memory.json'
        memory = ConversationMemory(path, security_registry=registry)
        memory.create_session(session_id='s')
        first = registry.rows()[0]
        assert first['state'] == 'active' and first['owner_id'] == 'a'
        ConversationMemory(path, security_registry=registry).delete_session('s')
        memory.create_session(session_id='s')
        rows = registry.rows()
        assert [r['state'] for r in rows] == ['revoked', 'active']
        assert rows[0]['generation'] != rows[1]['generation'] and rows[1]['revision'] == 2
        with pytest.raises(ValueError):
            registry.activate('s', first['generation'], 'a')
    finally:
        effective_owner.reset(token)


def test_failed_json_or_activation_never_promotes_pending(tmp_path, monkeypatch):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        path = tmp_path / 'memory.json'
        memory = ConversationMemory(path, security_registry=registry)
        monkeypatch.setattr(memory, '_save', lambda: (_ for _ in ()).throw(OSError('synthetic')))
        with pytest.raises(OSError):
            memory.create_session(session_id='s')
        assert registry.rows()[0]['state'] == 'pending'
        assert ConversationMemory(path, security_registry=registry)._store == {}
        assert registry.rows()[0]['state'] == 'pending'
    finally:
        effective_owner.reset(token)


def test_legacy_not_promoted_and_mismatch_revoked(tmp_path):
    path = tmp_path / 'memory.json'
    path.write_text(json.dumps({'legacy': {'owner_id': 'a', 'messages': [], 'conversation_generation': 'forged'}}))
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    ConversationMemory(path, security_registry=registry)
    assert registry.rows() == []
    generation = registry.begin('missing', 'a')
    registry.activate('missing', generation, 'a')
    ConversationMemory(path, security_registry=registry)
    assert registry.rows()[0]['state'] == 'revoked'


def test_activation_failure_remains_pending_after_restart(tmp_path, monkeypatch):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        path = tmp_path / 'memory.json'
        memory = ConversationMemory(path, security_registry=registry)
        monkeypatch.setattr(registry, 'activate', lambda *args: (_ for _ in ()).throw(OSError('synthetic')))
        with pytest.raises(OSError):
            memory.create_session(session_id='s')
        assert json.loads(path.read_text())['s']['owner_id'] == 'a'
        ConversationMemory(path, security_registry=registry)
        assert registry.rows()[0]['state'] == 'pending'
    finally:
        effective_owner.reset(token)


def test_revocation_failure_preserves_json(tmp_path, monkeypatch):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        path = tmp_path / 'memory.json'
        memory = ConversationMemory(path, security_registry=registry)
        memory.create_session(session_id='s')
        monkeypatch.setattr(registry, 'revoke', lambda *args: (_ for _ in ()).throw(OSError('synthetic')))
        with pytest.raises(OSError):
            memory.delete_session('s')
        assert 's' in json.loads(path.read_text())
    finally:
        effective_owner.reset(token)


def test_concurrent_same_id_create_uses_one_registry_identity(tmp_path):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    path = tmp_path / 'memory.json'
    barrier = threading.Barrier(2)
    def create():
        token = effective_owner.set('a')
        try:
            memory = ConversationMemory(path, security_registry=registry)
            barrier.wait()
            return memory.create_session(session_id='s')
        finally:
            effective_owner.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(create) for _ in range(2)]
        assert [future.result() for future in results] == ['s', 's']
    assert len(registry.rows()) == 1 and registry.rows()[0]['state'] == 'active'


def test_revoked_pending_identity_cannot_activate(tmp_path):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    generation = registry.begin('s', 'a')
    registry.revoke('s')
    with pytest.raises(ValueError):
        registry.activate('s', generation, 'a')
