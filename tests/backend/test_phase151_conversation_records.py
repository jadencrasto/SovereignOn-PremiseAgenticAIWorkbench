import json
from unittest.mock import patch

import pytest

from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner


def test_legacy_read_is_repeatable_and_preserves_unknown_fields(tmp_path):
    path = tmp_path / 'memory.json'
    original = {'old': {'owner_id': 'a', 'future': {'keep': 1}, 'messages': [
        {'role': 'user', 'content': 'hello', 'future_message_field': 'keep'}]}}
    path.write_text(json.dumps(original), encoding='utf-8')
    token = effective_owner.set('a')
    try:
        first = ConversationMemory(path).get_history('old')[0]
        again = ConversationMemory(path).get_history('old')[0]
        assert first.id == again.id and first.created_at is None
        assert json.loads(path.read_text(encoding='utf-8')) == original
        ConversationMemory(path).add_assistant_message('old', 'reply')
        saved = json.loads(path.read_text(encoding='utf-8'))['old']
        assert saved['future'] == {'keep': 1}
        assert saved['messages'][0]['future_message_field'] == 'keep'
        assert saved['messages'][0]['id'] == first.id
        assert saved['messages'][1]['created_at']
        assert ConversationMemory(path).get_history('old')[1].id == saved['messages'][1]['id']
    finally:
        effective_owner.reset(token)


def test_failed_replace_preserves_original_file(tmp_path):
    path = tmp_path / 'memory.json'
    token = effective_owner.set('a')
    try:
        store = ConversationMemory(path)
        store.add_user_message('session', 'original')
        before = path.read_bytes()
        with patch('backend.agent.memory.os.replace', side_effect=OSError('synthetic failure')):
            with pytest.raises(OSError):
                store.add_assistant_message('session', 'not persisted')
        assert path.read_bytes() == before
        assert [m.content for m in ConversationMemory(path).get_history('session')] == ['original']
    finally:
        effective_owner.reset(token)


def test_owner_isolation_and_new_schema(tmp_path):
    path = tmp_path / 'memory.json'
    token = effective_owner.set('a')
    try:
        store = ConversationMemory(path)
        store.add_user_message('session', 'private')
        saved = json.loads(path.read_text(encoding='utf-8'))['session']
        assert saved['owner_id'] == 'a' and saved['schema_version'] == 2
        assert saved['messages'][0]['id'] and saved['messages'][0]['created_at']
        other = effective_owner.set('b')
        try:
            assert store.list_sessions() == []
            with pytest.raises(PermissionError):
                store.get_history('session')
        finally:
            effective_owner.reset(other)
    finally:
        effective_owner.reset(token)
