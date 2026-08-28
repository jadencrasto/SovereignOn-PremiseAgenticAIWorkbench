"""
tests/backend/test_memory.py
----------------------------
Tests for ConversationMemory session management and history.
"""

from backend.agent.memory import ConversationMemory


def test_create_session():
    m = ConversationMemory()
    sid = m.create_session()
    assert sid is not None
    assert m.session_exists(sid)
    assert m.session_count() == 1


def test_create_session_with_id():
    m = ConversationMemory()
    sid = m.create_session(session_id="test-session-1")
    assert sid == "test-session-1"


def test_create_session_idempotent():
    m = ConversationMemory()
    sid1 = m.create_session(session_id="abc")
    sid2 = m.create_session(session_id="abc")
    assert sid1 == sid2
    assert m.session_count() == 1


def test_system_prompt_in_history():
    m = ConversationMemory()
    sid = m.create_session(system_prompt="You are helpful.")
    history = m.get_history(sid)
    assert len(history) == 1
    assert history[0].role == "system"
    assert history[0].content == "You are helpful."


def test_add_messages():
    m = ConversationMemory()
    sid = m.create_session()
    m.add_user_message(sid, "Hello!")
    m.add_assistant_message(sid, "Hi there!")
    history = m.get_history(sid)
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[1].role == "assistant"
    assert history[1].content == "Hi there!"


def test_multi_turn():
    m = ConversationMemory()
    sid = m.create_session(system_prompt="Be concise.")
    m.add_user_message(sid, "Turn 1")
    m.add_assistant_message(sid, "Reply 1")
    m.add_user_message(sid, "Turn 2")
    m.add_assistant_message(sid, "Reply 2")
    history = m.get_history(sid)
    assert len(history) == 5
    assert history[0].role == "system"


def test_clear_session():
    m = ConversationMemory()
    sid = m.create_session()
    m.add_user_message(sid, "Test")
    m.clear_session(sid)
    assert m.session_exists(sid)
    assert len(m.get_history(sid)) == 0


def test_delete_session():
    m = ConversationMemory()
    sid = m.create_session()
    deleted = m.delete_session(sid)
    assert deleted is True
    assert not m.session_exists(sid)


def test_delete_nonexistent_session():
    m = ConversationMemory()
    deleted = m.delete_session("nonexistent")
    assert deleted is False


def test_history_for_missing_session():
    m = ConversationMemory()
    history = m.get_history("does-not-exist")
    assert history == []
