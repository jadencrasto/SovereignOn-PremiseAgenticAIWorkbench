import json
import threading
import pytest

from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner


def test_revoked_all_normal_operations_denied_and_data_preserved(tmp_path):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        path = tmp_path / 'memory.json'
        memory = ConversationMemory(path, security_registry=registry)
        memory.add_user_message('s', 'synthetic')
        memory.set_title('s', 'private')
        original = json.loads(path.read_text())
        registry.revoke('s')
        actions = [lambda: memory.get_history('s'), lambda: memory.get_title('s'),
                   lambda: memory.session_exists('s'), lambda: memory.add_user_message('s', 'new'),
                   lambda: memory.add_assistant_message('s', 'new'), lambda: memory.add_system_message('s', 'new'),
                   lambda: memory.set_title('s', 'new'), lambda: memory.clear_session('s'),
                   lambda: memory.delete_session('s'), lambda: memory.create_session(session_id='s')]
        for action in actions:
            with pytest.raises(PermissionError):
                action()
        assert not memory.is_owned_by('s', 'a')
        assert 's' not in memory.list_sessions()
        assert json.loads(path.read_text()) == original
        recovered = ConversationMemory(path, security_registry=registry)
        assert not recovered.is_owned_by('s', 'a')
        with pytest.raises(PermissionError):
            recovered.get_title('s')
    finally:
        effective_owner.reset(token)


def test_completed_deletion_requires_explicit_fresh_recreation(tmp_path):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        memory.add_user_message('s', 'synthetic')
        old = registry.rows()[0]['generation']
        assert memory.delete_session('s')
        with pytest.raises(PermissionError):
            memory.add_user_message('s', 'must not recreate')
        memory.create_session(session_id='s')
        rows = registry.rows()
        assert rows[0]['state'] == 'revoked'
        assert rows[1]['state'] == 'active' and rows[1]['generation'] != old
        memory.add_user_message('s', 'fresh')
        assert [m.content for m in memory.get_history('s')] == ['fresh']
    finally:
        effective_owner.reset(token)


def test_registry_read_error_denies_metadata_and_mutation(tmp_path, monkeypatch):
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        memory.add_user_message('s', 'synthetic')
        monkeypatch.setattr(registry, 'observed_revoked', lambda *_: (_ for _ in ()).throw(OSError('sensitive path')))
        for action in [lambda: memory.get_title('s'), lambda: memory.session_exists('s'),
                       lambda: memory.is_owned_by('s', 'a'), lambda: memory.add_user_message('s', 'x'),
                       lambda: memory.delete_session('s'), lambda: memory.create_session(session_id='new')]:
            with pytest.raises(PermissionError, match='access unavailable') as exc:
                action()
            assert 'sensitive path' not in str(exc.value)
    finally:
        effective_owner.reset(token)


def test_memory_revocation_racing_write_cannot_recreate(tmp_path, monkeypatch):
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
    errors, outcome = [], []
    def pause(sid):
        original(sid)
        revoked.set()
        if not release.wait(5):
            raise TimeoutError('test timed out')
    monkeypatch.setattr(registry, 'revoke', pause)
    def delete():
        token = effective_owner.set('a')
        try:
            memory.delete_session('s')
        except Exception as exc:
            errors.append(exc)
        finally:
            effective_owner.reset(token)
    def append():
        token = effective_owner.set('a')
        try:
            other = ConversationMemory(path, security_registry=registry)
            try:
                other.add_user_message('s', 'must be denied')
                outcome.append('allowed')
            except PermissionError:
                outcome.append('denied')
        except Exception as exc:
            errors.append(exc)
        finally:
            effective_owner.reset(token)
    first = threading.Thread(target=delete, daemon=True)
    second = threading.Thread(target=append, daemon=True)
    first.start()
    assert revoked.wait(5)
    second.start()
    release.set()
    first.join(8)
    second.join(8)
    assert not first.is_alive() and not second.is_alive()
    assert not errors and outcome == ['denied']
    assert 's' not in json.loads(path.read_text())
