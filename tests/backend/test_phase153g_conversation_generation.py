"""Generation persistence only: no verified-task authorization is implied."""

import json
import pytest

from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner


def test_generation_survives_restart_and_changes_after_delete_recreate(tmp_path):
    path = tmp_path / 'memory.json'
    token = effective_owner.set('owner')
    try:
        memory = ConversationMemory(path)
        memory.create_session(session_id='s')
        initial = json.loads(path.read_text())['s']['conversation_generation']
        restarted = ConversationMemory(path)
        restarted.create_session(session_id='s')
        assert json.loads(path.read_text())['s']['conversation_generation'] == initial
        assert restarted.delete_session('s')
        restarted.create_session(session_id='s')
        assert json.loads(path.read_text())['s']['conversation_generation'] != initial
    finally:
        effective_owner.reset(token)


def test_legacy_conversation_is_not_silently_assigned_generation(tmp_path):
    path = tmp_path / 'memory.json'
    path.write_text(json.dumps({'old': {'owner_id': 'owner', 'messages': [], 'unknown': 7}}))
    token = effective_owner.set('owner')
    try:
        memory = ConversationMemory(path)
        memory.add_user_message('old', 'synthetic')
        saved = json.loads(path.read_text())['old']
        assert 'conversation_generation' not in saved
        assert saved['unknown'] == 7
    finally:
        effective_owner.reset(token)


def test_implicit_new_session_gets_generation(tmp_path):
    path = tmp_path / 'memory.json'
    token = effective_owner.set('owner')
    try:
        ConversationMemory(path).add_user_message('new', 'synthetic')
        assert json.loads(path.read_text())['new']['conversation_generation']
    finally:
        effective_owner.reset(token)


def test_legacy_generation_field_is_preserved_without_granting_other_owner_access(tmp_path):
    path = tmp_path / 'memory.json'
    spoofed = '12345678-1234-4234-8234-123456789abc'
    path.write_text(json.dumps({'old': {'owner_id': 'a', 'messages': [],
                                      'conversation_generation': spoofed}}))
    memory = ConversationMemory(path)
    token = effective_owner.set('b')
    try:
        assert not memory.is_owned_by('old', 'b')
        with pytest.raises(PermissionError):
            memory.add_user_message('old', 'synthetic')
    finally:
        effective_owner.reset(token)
    assert json.loads(path.read_text())['old']['conversation_generation'] == spoofed
