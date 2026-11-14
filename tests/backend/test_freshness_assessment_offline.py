"""Stdlib-only tests; temporary SQLite and no production authorization."""
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[2]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

freshness = load('freshness_fixture', 'prototype/freshness_assessment.py')
store_module = load('actual_task_store', 'backend/agent/task_store.py')


class FreshnessTests(unittest.TestCase):
    def test_missing_corrupt_stale_and_matching_never_authorize(self):
        self.assertEqual(freshness.assess(2, None), 'MISSING')
        self.assertEqual(freshness.assess(2, {'generation':2, 'commitment':'broken'}), 'INVALID')
        self.assertEqual(freshness.assess(2, {'generation':1, 'commitment':'a'*64}), 'MISMATCH')
        self.assertEqual(freshness.assess(2, {'generation':2, 'commitment':'a'*64}), 'MATCH_UNVERIFIED')
        self.assertFalse(freshness.authorization_available())

    def test_rollback_of_valid_database_replays_consumed_state(self):
        # Demonstrates an unresolved security limitation; no production state.
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'synthetic.sqlite'
            backup = Path(directory) / 'synthetic-backup.sqlite'
            store = store_module.TaskStore(db)
            store.save_task({'task_id':'t','session_id':'s','user_request':'synthetic','status':'pending'})
            store.save_approval({'approval_id':'a','task_id':'t','step_id':'s','tool_name':'test',
                'arguments_hash':'hash','status':'pending','created_at':datetime.now(timezone.utc).isoformat(),
                'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()})
            self.assertTrue(store.update_approval_status_atomic('a','pending','approved'))
            with closing(sqlite3.connect(db)) as src, closing(sqlite3.connect(backup)) as dst:
                src.backup(dst)
            self.assertTrue(store.consume_approved_approval('a'))
            self.assertFalse(store.consume_approved_approval('a'))
            with closing(sqlite3.connect(backup)) as src, closing(sqlite3.connect(db)) as dst:
                src.backup(dst)
            # The real persistence primitive can consume again after full rollback.
            self.assertTrue(store.consume_approved_approval('a'))
            self.assertFalse(freshness.authorization_available())


if __name__ == '__main__': unittest.main(verbosity=2)
