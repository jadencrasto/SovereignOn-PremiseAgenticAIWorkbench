"""Unresolved registry-based conversation authorization guard."""
import pytest
import json
import threading

from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner


def test_revoked_conversation_is_not_readable_or_listed(tmp_path):
    token = effective_owner.set('synthetic-owner')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        memory.add_user_message('s', 'synthetic secret')
        registry.revoke('s')
        assert 's' not in memory.list_sessions()
    finally:
        effective_owner.reset(token)


def test_revoked_conversation_history_is_denied(tmp_path):
    token = effective_owner.set('synthetic-owner')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        memory.add_user_message('s', 'synthetic secret')
        registry.revoke('s')
        with pytest.raises(PermissionError):
            memory.get_history('s')
    finally:
        effective_owner.reset(token)


def test_registry_failure_denies_listing_and_history(tmp_path, monkeypatch):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        memory.add_user_message('s', 'synthetic')
        monkeypatch.setattr(registry, 'observed_revoked', lambda *_: (_ for _ in ()).throw(OSError('private path')))
        with pytest.raises(PermissionError, match='access unavailable') as err:
            memory.get_history('s')
        assert 'private path' not in str(err.value)
        with pytest.raises(PermissionError):
            memory.list_sessions()
    finally:
        effective_owner.reset(token)


def test_active_legacy_unknown_and_cross_owner_behavior(tmp_path):
    path = tmp_path / 'memory.json'
    path.write_text(json.dumps({'legacy': {'owner_id': 'a', 'messages': [{'role': 'user', 'content': 'legacy'}]}}))
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(path, security_registry=registry)
        memory.add_user_message('active', 'synthetic')
        # An owner label without an active generation binding is not sufficient.
        assert set(memory.list_sessions()) == {'active'}
        with pytest.raises(PermissionError):
            memory.get_history('legacy')
        assert memory.get_history('active')[0].content == 'synthetic'
    finally:
        effective_owner.reset(token)
    token = effective_owner.set('b')
    try:
        assert memory.list_sessions() == []
        with pytest.raises(PermissionError):
            memory.get_history('active')
    finally:
        effective_owner.reset(token)


def test_failed_deletion_revocation_hides_history_after_restart(tmp_path, monkeypatch):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        path = tmp_path / 'memory.json'
        memory = ConversationMemory(path, security_registry=registry)
        memory.add_user_message('s', 'synthetic')
        monkeypatch.setattr(memory, '_save', lambda: (_ for _ in ()).throw(OSError('synthetic')))
        with pytest.raises(OSError):
            memory.delete_session('s')
        assert 's' in json.loads(path.read_text())
        recovered = ConversationMemory(path, security_registry=registry)
        assert 's' not in recovered.list_sessions()
        with pytest.raises(PermissionError):
            recovered.get_history('s')
    finally:
        effective_owner.reset(token)


def test_memory_revocation_serializes_history_and_listing(tmp_path, monkeypatch):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    path = tmp_path / 'memory.json'
    token = effective_owner.set('a')
    try:
        memory = ConversationMemory(path, security_registry=registry)
        memory.add_user_message('s', 'synthetic')
    finally:
        effective_owner.reset(token)
    revoked, release = threading.Event(), threading.Event()
    original = registry.revoke
    def pause(sid):
        original(sid)
        revoked.set()
        if not release.wait(5):
            raise TimeoutError('test timed out')
    monkeypatch.setattr(registry, 'revoke', pause)
    results = []
    def delete():
        token = effective_owner.set('a')
        try:
            memory.delete_session('s')
        finally:
            effective_owner.reset(token)
    def read():
        token = effective_owner.set('a')
        try:
            other = ConversationMemory(path, security_registry=registry)
            sessions = other.list_sessions()
            try:
                other.get_history('s')
                results.append((sessions, 'allowed'))
            except PermissionError:
                results.append((sessions, 'denied'))
        finally:
            effective_owner.reset(token)
    first = threading.Thread(target=delete, daemon=True)
    second = threading.Thread(target=read, daemon=True)
    first.start()
    assert revoked.wait(5)
    second.start()
    release.set()
    first.join(8)
    second.join(8)
    assert not first.is_alive() and not second.is_alive()
    assert results == [([], 'denied')]
