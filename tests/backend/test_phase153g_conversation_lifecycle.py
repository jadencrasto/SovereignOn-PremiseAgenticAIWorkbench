import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner, local_legacy_access


def test_legacy_binding_preserves_unknown_fields_and_denies_other_owner(tmp_path):
    path = tmp_path / 'memory.json'
    path.write_text(json.dumps({'old': {'owner_id': None, 'messages': [],
                                      'conversation_generation': {'malformed': True}, 'unknown': 7}}))
    memory = ConversationMemory(path)
    owner_token = effective_owner.set('user_dev_admin')
    local_token = local_legacy_access.set(True)
    try:
        assert memory.get_history('old') == []
    finally:
        local_legacy_access.reset(local_token)
        effective_owner.reset(owner_token)
    saved = json.loads(path.read_text())['old']
    assert saved['owner_id'] == 'user_dev_admin'
    assert saved['unknown'] == 7 and saved['conversation_generation'] == {'malformed': True}
    other = effective_owner.set('other')
    try:
        with pytest.raises(PermissionError):
            memory.delete_session('old')
    finally:
        effective_owner.reset(other)


def test_explicit_and_implicit_creation_and_restart(tmp_path):
    path = tmp_path / 'memory.json'
    owner = effective_owner.set('a')
    try:
        memory = ConversationMemory(path)
        memory.create_session(system_prompt='synthetic', session_id='explicit')
        memory.add_user_message('implicit', 'synthetic')
        restarted = ConversationMemory(path)
        assert [m.role for m in restarted.get_history('explicit')] == ['system']
        assert [m.role for m in restarted.get_history('implicit')] == ['user']
        records = json.loads(path.read_text())
        assert records['explicit']['conversation_generation'] != records['implicit']['conversation_generation']
    finally:
        effective_owner.reset(owner)


def test_concurrent_create_and_delete_preserves_consistent_record(tmp_path):
    path = tmp_path / 'memory.json'
    initial = effective_owner.set('a')
    try:
        ConversationMemory(path).create_session(session_id='s')
    finally:
        effective_owner.reset(initial)
    barrier = threading.Barrier(2)

    def worker(operation):
        token = effective_owner.set('a')
        try:
            instance = ConversationMemory(path)
            barrier.wait()
            return operation(instance)
        finally:
            effective_owner.reset(token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(worker, lambda m: m.create_session(session_id='s'))
        b = pool.submit(worker, lambda m: m.delete_session('s'))
        assert a.result() == 's'
        assert b.result() is True
    data = json.loads(path.read_text())
    assert 's' not in data or (data['s']['owner_id'] == 'a' and data['s']['conversation_generation'])
