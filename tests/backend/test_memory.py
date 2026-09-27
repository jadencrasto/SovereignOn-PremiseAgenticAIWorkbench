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


def test_persistence_and_reload(tmp_path):
    storage_file = tmp_path / "test_memory.json"
    m1 = ConversationMemory(storage_file=storage_file)
    sid = m1.create_session(session_id="persisted-sess-1", system_prompt="You are an industrial engineer.")
    m1.add_user_message(sid, "Check vibration levels on K-101")
    m1.add_assistant_message(sid, "K-101 vibration was 9.4 mm/s RMS.")

    assert storage_file.exists()

    # Re-initialize second instance from same file (simulating backend restart)
    m2 = ConversationMemory(storage_file=storage_file)
    assert m2.session_exists(sid)
    assert m2.session_count() == 1
    history = m2.get_history(sid)
    assert len(history) == 3
    assert history[0].role == "system"
    assert history[0].content == "You are an industrial engineer."
    assert history[1].role == "user"
    assert history[1].content == "Check vibration levels on K-101"
    assert history[2].role == "assistant"
    assert history[2].content == "K-101 vibration was 9.4 mm/s RMS."


def test_persistence_delete_and_clear(tmp_path):
    storage_file = tmp_path / "test_memory_delete.json"
    m = ConversationMemory(storage_file=storage_file)
    s1 = m.create_session(session_id="s1")
    s2 = m.create_session(session_id="s2")
    m.add_user_message(s1, "Msg 1")
    m.add_user_message(s2, "Msg 2")

    # Clear s1
    m.clear_session(s1)
    m_reloaded = ConversationMemory(storage_file=storage_file)
    assert len(m_reloaded.get_history(s1)) == 0
    assert len(m_reloaded.get_history(s2)) == 1

    # Delete s2
    m.delete_session(s2)
    m_reloaded2 = ConversationMemory(storage_file=storage_file)
    assert not m_reloaded2.session_exists(s2)
    assert m_reloaded2.session_exists(s1)
