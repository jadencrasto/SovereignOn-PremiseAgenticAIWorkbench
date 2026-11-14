"""Synthetic transaction tests; these do not test user authorization."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.agent.sqlite_transaction import immediate_transaction
from backend.agent.task_store import TaskStore


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    store.save_task({"task_id": "t", "session_id": "s", "user_request": "synthetic",
                     "status": "awaiting_approval", "user_id": "owner"})
    store.save_approval({"approval_id": "a", "task_id": "t", "step_id": "step",
                         "tool_name": "integer_add", "arguments_hash": "synthetic-hash",
                         "created_at": "2026-01-01", "expires_at": "2099-01-01"})
    return path


def _synthetic_claim_and_complete(path):
    # Deliberately fixture-only: this is NOT the application's authorization
    # or approval implementation and must not be used to dispatch actions.
    with immediate_transaction(path) as conn:
        changed = conn.execute(
            "UPDATE approvals SET status='consumed' WHERE approval_id='a' "
            "AND task_id='t' AND arguments_hash='synthetic-hash' AND status='pending'"
        ).rowcount
        if changed:
            conn.execute("UPDATE tasks SET status='completed', result='3' WHERE task_id='t'")
        return changed


def test_concurrent_transactions_claim_only_once(database):
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        return _synthetic_claim_and_complete(database)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker)
        second = pool.submit(worker)
        assert sorted([first.result(), second.result()]) == [0, 1]
    assert TaskStore(database).get_task('t')['result'] == '3'


def test_failure_rolls_back_both_rows_and_restart_can_retry(database):
    with pytest.raises(RuntimeError):
        with immediate_transaction(database) as conn:
            conn.execute("UPDATE approvals SET status='consumed' WHERE approval_id='a'")
            conn.execute("UPDATE tasks SET status='completed', result='3' WHERE task_id='t'")
            raise RuntimeError("synthetic interruption")
    restarted = TaskStore(database)
    assert restarted.get_approval('a')['status'] == 'pending'
    assert restarted.get_task('t')['result'] is None
    assert _synthetic_claim_and_complete(database) == 1
    assert _synthetic_claim_and_complete(database) == 0


def test_failed_binding_does_not_consume_approval(database):
    with immediate_transaction(database) as conn:
        count = conn.execute(
            "UPDATE approvals SET status='consumed' WHERE approval_id=? "
            "AND task_id=? AND arguments_hash=? AND status='pending'",
            ('a', 't', 'different-hash'),
        ).rowcount
        assert count == 0
    assert TaskStore(database).get_approval('a')['status'] == 'pending'


def test_foreign_keys_enforced_and_rollback(database):
    with pytest.raises(sqlite3.IntegrityError):
        with immediate_transaction(database) as conn:
            conn.execute("DELETE FROM tasks WHERE task_id='t'")
    assert TaskStore(database).get_task('t') is not None


def test_contended_writer_fails_without_changing_data(database):
    blocker = sqlite3.connect(str(database), isolation_level=None)
    try:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            with immediate_transaction(database, timeout_seconds=0.05):
                pytest.fail('transaction unexpectedly acquired')
    finally:
        blocker.execute('ROLLBACK')
        blocker.close()
    assert TaskStore(database).get_approval('a')['status'] == 'pending'


@pytest.mark.parametrize('timeout', [0, -1, 6, float('inf'), float('nan'), True, '1'])
def test_rejects_invalid_or_unbounded_wait(database, timeout):
    with pytest.raises(ValueError):
        with immediate_transaction(database, timeout_seconds=timeout):
            pass
