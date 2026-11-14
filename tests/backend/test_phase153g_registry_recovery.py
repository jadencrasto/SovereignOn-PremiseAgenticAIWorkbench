import json
import os
import subprocess
import sys
import threading

import pytest

from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner


def _race(*functions):
    barrier = threading.Barrier(len(functions), timeout=5)
    outcomes = []
    def run(fn):
        try:
            barrier.wait()
            outcomes.append(('ok', fn()))
        except Exception as exc:
            outcomes.append(('error', exc))
    threads = [threading.Thread(target=run, args=(fn,), daemon=True) for fn in functions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=8)
    assert not any(thread.is_alive() for thread in threads), 'concurrent operation timed out'
    return outcomes


def test_concurrent_create_delete_and_restart(tmp_path):
    path = tmp_path / 'memory.json'
    db = tmp_path / 'tasks.db'
    registry = ConversationRegistry(db)
    token = effective_owner.set('a')
    try:
        ConversationMemory(path, security_registry=registry).create_session(session_id='s')
    finally:
        effective_owner.reset(token)
    def action(delete):
        token = effective_owner.set('a')
        try:
            memory = ConversationMemory(path, security_registry=registry)
            return memory.delete_session('s') if delete else memory.create_session(session_id='s')
        finally:
            effective_owner.reset(token)
    outcomes = _race(lambda: action(False), lambda: action(True))
    assert all(status == 'ok' for status, _ in outcomes), outcomes
    restarted = ConversationMemory(path, security_registry=registry)
    rows = registry.rows()
    assert len([r for r in rows if r['state'] == 'active']) <= 1
    assert all(r['owner_id'] == 'a' for r in rows)
    if any(r['state'] == 'active' for r in rows):
        assert restarted.is_owned_by('s', 'a')
    for row in rows:
        if row['state'] == 'revoked':
            with pytest.raises(ValueError):
                registry.activate('s', row['generation'], 'a')


def test_activation_racing_revocation_never_resurrects(tmp_path):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    generation = registry.begin('s', 'a')
    outcomes = _race(lambda: registry.activate('s', generation, 'a'), lambda: registry.revoke('s'))
    assert all(status == 'ok' or isinstance(value, ValueError) for status, value in outcomes)
    assert registry.rows()[0]['state'] == 'revoked'
    with pytest.raises(ValueError):
        registry.activate('s', generation, 'a')


def test_registration_failure_does_not_write_json(tmp_path, monkeypatch):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    path = tmp_path / 'memory.json'
    memory = ConversationMemory(path, security_registry=registry)
    monkeypatch.setattr(registry, 'begin', lambda *args: (_ for _ in ()).throw(OSError('synthetic')))
    token = effective_owner.set('a')
    try:
        with pytest.raises(OSError):
            memory.create_session(session_id='s')
    finally:
        effective_owner.reset(token)
    assert not path.exists() and registry.rows() == []


def test_cross_owner_cannot_reuse_or_delete_registered_conversation(tmp_path):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    path = tmp_path / 'memory.json'
    owner = effective_owner.set('a')
    try:
        ConversationMemory(path, security_registry=registry).create_session(session_id='s')
    finally:
        effective_owner.reset(owner)
    other = effective_owner.set('b')
    try:
        memory = ConversationMemory(path, security_registry=registry)
        with pytest.raises(PermissionError):
            memory.create_session(session_id='s')
        with pytest.raises(PermissionError):
            memory.delete_session('s')
    finally:
        effective_owner.reset(other)
    assert registry.rows()[0]['owner_id'] == 'a' and registry.rows()[0]['state'] == 'active'


def test_failed_json_deletion_keeps_revocation_after_restart(tmp_path, monkeypatch):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    path = tmp_path / 'memory.json'
    token = effective_owner.set('a')
    try:
        memory = ConversationMemory(path, security_registry=registry)
        memory.create_session(session_id='s')
        monkeypatch.setattr(memory, '_save', lambda: (_ for _ in ()).throw(OSError('synthetic')))
        with pytest.raises(OSError):
            memory.delete_session('s')
        assert 's' in json.loads(path.read_text())
        assert registry.rows()[0]['state'] == 'revoked'
        restarted = ConversationMemory(path, security_registry=registry)
        assert not restarted.is_owned_by('s', 'a')
        assert registry.rows()[0]['state'] == 'revoked'
    finally:
        effective_owner.reset(token)


def test_reconciliation_failure_does_not_promote_identity(tmp_path, monkeypatch):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    generation = registry.begin('missing', 'a')
    registry.activate('missing', generation, 'a')
    monkeypatch.setattr(registry, 'revoke', lambda *args: (_ for _ in ()).throw(OSError('synthetic')))
    with pytest.raises(OSError):
        ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
    assert registry.rows()[0]['state'] == 'active'


def test_subprocess_exit_after_pending_registration(tmp_path):
    db = tmp_path / 'tasks.db'
    code = ('import os,sys; from pathlib import Path; '
            'from backend.agent.conversation_registry import ConversationRegistry; '
            "ConversationRegistry(Path(sys.argv[1])).begin('s','a'); os._exit(23)")
    process = subprocess.run([sys.executable, '-c', code, str(db)], timeout=15, check=False)
    assert process.returncode == 23
    registry = ConversationRegistry(db)
    ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
    assert registry.rows()[0]['state'] == 'pending'
