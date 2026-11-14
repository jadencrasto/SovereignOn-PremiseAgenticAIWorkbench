import json
import sqlite3
import threading
from contextlib import closing

from backend.agent.conversation_registry import ConversationClassification as C, ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner


def test_new_legacy_and_forged_records_remain_unverified(tmp_path):
    db = tmp_path / 'tasks.db'
    registry = ConversationRegistry(db)
    path = tmp_path / 'memory.json'
    path.write_text(json.dumps({'old': {'owner_id': 'a', 'messages': [], 'conversation_generation': 'forged'}}))
    token = effective_owner.set('a')
    try:
        memory = ConversationMemory(path, security_registry=registry)
        memory.create_session(session_id='new')
        assert registry.classify('new') == C.UNKNOWN
        assert registry.classify('old') == C.UNKNOWN
        assert registry.classify('missing') == C.UNKNOWN
    finally:
        effective_owner.reset(token)


def test_revocation_recreation_and_restart(tmp_path):
    db = tmp_path / 'tasks.db'
    registry = ConversationRegistry(db)
    first = registry.begin('s', 'a')
    registry.activate('s', first, 'a')
    registry.revoke('s')
    assert registry.classify('s') == C.REVOKED
    second = registry.begin('s', 'a')
    assert second != first and registry.classify('s') == C.UNKNOWN
    registry.activate('s', second, 'a')
    assert ConversationRegistry(db).classify('s') == C.UNKNOWN


def test_missing_corrupt_or_replaced_database_is_unknown(tmp_path):
    db = tmp_path / 'tasks.db'
    registry = ConversationRegistry(db)
    generation = registry.begin('s', 'a')
    registry.activate('s', generation, 'a')
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('DELETE FROM conversation_security_registry')
    assert registry.classify('s') == C.UNKNOWN
    db.unlink()
    assert registry.classify('s') == C.UNKNOWN and not db.exists()
    db.write_bytes(b'not a sqlite database')
    assert registry.classify('s') == C.UNKNOWN


def test_restored_older_active_snapshot_never_becomes_verified(tmp_path):
    db = tmp_path / 'tasks.db'
    snapshot = tmp_path / 'snapshot.db'
    registry = ConversationRegistry(db)
    generation = registry.begin('s', 'a')
    registry.activate('s', generation, 'a')
    with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(snapshot)) as destination:
        source.backup(destination)
    registry.revoke('s')
    with closing(sqlite3.connect(snapshot)) as source, closing(sqlite3.connect(db)) as destination:
        source.backup(destination)
    assert registry.classify('s') == C.UNKNOWN


def test_concurrent_transition_never_reports_trusted_state(tmp_path):
    registry = ConversationRegistry(tmp_path / 'tasks.db')
    generation = registry.begin('s', 'a')
    barrier = threading.Barrier(2, timeout=5)
    def transition():
        barrier.wait()
        registry.activate('s', generation, 'a')
        registry.revoke('s')
    worker = threading.Thread(target=transition, daemon=True)
    worker.start()
    barrier.wait()
    states = [registry.classify('s') for _ in range(10)]
    worker.join(8)
    assert not worker.is_alive()
    assert set(states) <= {C.UNKNOWN, C.REVOKED}
    assert registry.classify('s') == C.REVOKED
