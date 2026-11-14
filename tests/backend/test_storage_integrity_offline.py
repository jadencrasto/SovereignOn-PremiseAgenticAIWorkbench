"""Synthetic stdlib-only SQLite regression checks; no app initialization."""
import ast
from contextlib import closing
import importlib.util
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('synthetic_task_store', ROOT / 'backend/agent/task_store.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
spec2 = importlib.util.spec_from_file_location('synthetic_transaction', ROOT / 'backend/agent/sqlite_transaction.py')
tx = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(tx)


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'synthetic.sqlite'
        self.store = module.TaskStore(self.db)
        self.store.save_task({'task_id': 'task', 'session_id': 'session', 'user_request': 'synthetic', 'status': 'pending'})
        self.approval = {'approval_id': 'a', 'task_id': 'task', 'step_id': 's', 'tool_name': 'synthetic',
                         'arguments_hash': 'hash', 'status': 'pending', 'created_at': datetime.now(timezone.utc).isoformat(),
                         'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
        self.store.save_approval(self.approval)

    def tearDown(self): self.temp.cleanup()

    def approve_consume(self):
        self.assertTrue(self.store.update_approval_status_atomic('a', 'pending', 'approved'))
        self.assertTrue(self.store.consume_approved_approval('a'))

    def test_replay_cannot_replace_consumed_record(self):
        self.approve_consume()
        with self.assertRaises(sqlite3.IntegrityError): self.store.save_approval(dict(self.approval, status='approved'))
        self.assertFalse(self.store.consume_approved_approval('a'))
        self.assertEqual(self.store.get_approval('a')['status'], 'consumed')

    def test_terminal_states_are_not_resurrected(self):
        self.approve_consume()
        self.store.update_approval_status('a', 'expired')
        self.assertEqual(self.store.get_approval('a')['status'], 'consumed')
        with self.assertRaises(ValueError): self.store.update_approval_status('a', 'approved')
        with self.assertRaises(ValueError): self.store.update_approval_status_atomic('a', 'consumed', 'approved')

    def test_valid_transitions_and_invalidation(self):
        self.assertTrue(self.store.update_approval_status_atomic('a', 'pending', 'approved'))
        self.store.update_approval_status('a', 'rejected')
        self.assertFalse(self.store.consume_approved_approval('a'))
        self.assertEqual(module.TaskStore(self.db).get_approval('a')['status'], 'rejected')

    def test_protected_task_owner_cannot_be_reassigned(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO task_security_provenance (task_id,schema_version,owner_id,conversation_id,created_at,verification_state,dependency_state) VALUES ('task',1,NULL,'session','synthetic','unverified','unknown')")
        with self.assertRaises(ValueError):
            self.store.save_task({'task_id':'task','session_id':'other','user_request':'synthetic','status':'pending','user_id':'other'})
        self.assertEqual(self.store.get_task('task')['session_id'], 'session')

    def test_invalid_expiry_fails_closed(self):
        tree = ast.parse((ROOT / 'backend/agent/approval.py').read_text(encoding='utf-8'))
        cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == 'ApprovalManager')
        fn = next(x for x in cls.body if isinstance(x, ast.FunctionDef) and x.name == '_is_expired')
        fn.decorator_list = []
        code = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), fn], type_ignores=[])
        ast.fix_missing_locations(code)
        env = {'datetime': datetime, 'timezone': timezone}
        exec(compile(code, 'approval_expiry_fixture', 'exec'), env)
        for invalid in (None, '', 'bad', '2025-01-01T00:00:00'):
            self.assertTrue(env['_is_expired'](None, SimpleNamespace(expires_at=invalid)))

    def test_uncommitted_write_crash_rolls_back(self):
        program = "import os,sqlite3,sys;c=sqlite3.connect(sys.argv[1]);c.execute('BEGIN IMMEDIATE');c.execute(\"UPDATE approvals SET status='approved' WHERE approval_id='a'\");os._exit(0)"
        child = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(self.db)], capture_output=True, timeout=8)
        self.assertEqual(child.returncode, 0)
        self.assertEqual(module.TaskStore(self.db).get_approval('a')['status'], 'pending')

    def test_two_processes_cannot_consume_same_approval(self):
        self.assertTrue(self.store.update_approval_status_atomic('a', 'pending', 'approved'))
        program = ("import importlib.util,sys;from pathlib import Path;"
                   "s=importlib.util.spec_from_file_location('store',sys.argv[1]);m=importlib.util.module_from_spec(s);"
                   "s.loader.exec_module(m);print(int(m.TaskStore(Path(sys.argv[2])).consume_approved_approval('a')))" )
        args = [sys.executable, '-I', '-B', '-c', program, str(ROOT / 'backend/agent/task_store.py'), str(self.db)]
        children = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            results = [child.communicate(timeout=8) for child in children]
            self.assertTrue(all(child.returncode == 0 for child in children))
            self.assertEqual(sorted(out.strip() for out, err in results), ['0', '1'])
            self.assertEqual(module.TaskStore(self.db).get_approval('a')['status'], 'consumed')
        finally:
            for child in children:
                if child.poll() is None: child.kill(); child.wait(timeout=5)

    def test_cross_process_writer_contention_is_bounded(self):
        program = "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);c.execute('BEGIN IMMEDIATE');print('READY',flush=True);sys.stdin.read();c.rollback();c.close()"
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', program, str(self.db)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            # communicate has a hard deadline; child exits only after supplied input.
            import threading
            ready = []
            reader = threading.Thread(target=lambda: ready.append(child.stdout.readline()), daemon=True)
            reader.start(); reader.join(5)
            self.assertEqual(ready, ['READY\n'])
            with self.assertRaises(sqlite3.OperationalError):
                with tx.immediate_transaction(self.db, timeout_seconds=0.05): pass
            child.stdin.close(); child.wait(timeout=5)
            self.assertEqual(child.returncode, 0)
            with tx.immediate_transaction(self.db, timeout_seconds=0.2) as conn:
                self.assertEqual(conn.execute('SELECT status FROM approvals WHERE approval_id=?', ('a',)).fetchone()[0], 'pending')
        finally:
            if child.poll() is None: child.kill(); child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream and not stream.closed: stream.close()

    def test_production_consume_busy_has_no_fallback(self):
        self.assertTrue(self.store.update_approval_status_atomic('a', 'pending', 'approved'))
        conn = sqlite3.connect(self.db, timeout=1, isolation_level=None)
        try:
            conn.execute('BEGIN IMMEDIATE')
            with self.assertRaises(sqlite3.OperationalError): self.store.consume_approved_approval('a')
            self.assertEqual(conn.execute("SELECT status FROM approvals WHERE approval_id='a'").fetchone()[0], 'approved')
        finally:
            conn.rollback(); conn.close()
        self.assertTrue(self.store.consume_approved_approval('a'))

    def test_missing_database_is_not_recreated(self):
        moved = self.db.with_name('moved.sqlite')
        self.db.rename(moved)
        try:
            with self.assertRaises(sqlite3.OperationalError): self.store.consume_approved_approval('a')
            self.assertFalse(self.db.exists())
        finally: moved.rename(self.db)

    def test_invalid_and_expired_database_expiry_denied(self):
        self.assertTrue(self.store.update_approval_status_atomic('a', 'pending', 'approved'))
        for expiry in ('invalid', '2099-01-01T00:00:00', '2000-01-01T00:00:00+00:00'):
            with closing(sqlite3.connect(self.db)) as conn, conn:
                conn.execute('UPDATE approvals SET expires_at=? WHERE approval_id=?', (expiry, 'a'))
            self.assertFalse(self.store.consume_approved_approval('a'))
            self.assertEqual(self.store.get_approval('a')['status'], 'approved')

    def test_interrupted_production_consume_rolls_back(self):
        self.assertTrue(self.store.update_approval_status_atomic('a', 'pending', 'approved'))
        real_connect = sqlite3.connect
        class InterruptedConnection:
            def __init__(self, connection): self.connection = connection
            def execute(self, sql, *args):
                result = self.connection.execute(sql, *args)
                if sql.startswith("UPDATE approvals SET status = 'consumed'"):
                    raise RuntimeError('synthetic interruption')
                return result
            def __getattr__(self, name): return getattr(self.connection, name)
        with patch.object(module.sqlite3, 'connect', side_effect=lambda *a, **k: InterruptedConnection(real_connect(*a, **k))):
            with self.assertRaisesRegex(RuntimeError, 'synthetic interruption'):
                self.store.consume_approved_approval('a')
        self.assertEqual(module.TaskStore(self.db).get_approval('a')['status'], 'approved')

    def test_expiry_clock_checked_after_writer_lock(self):
        self.assertTrue(self.store.update_approval_status_atomic('a', 'pending', 'approved'))
        conn = sqlite3.connect(self.db, timeout=1, isolation_level=None)
        conn.execute('BEGIN IMMEDIATE')
        waiting = threading.Event(); released = threading.Event(); results = []
        real_connect = sqlite3.connect
        class ConnectionProxy:
            def __init__(self, connection): self.connection = connection
            def execute(self, sql, *args):
                if sql == 'BEGIN IMMEDIATE': waiting.set()
                return self.connection.execute(sql, *args)
            def __getattr__(self, name): return getattr(self.connection, name)
        class CheckedDatetime:
            @staticmethod
            def fromisoformat(value): return datetime.fromisoformat(value)
            @staticmethod
            def now(zone):
                if not released.is_set(): raise AssertionError('clock read before writer lock')
                return datetime(2100, 1, 1, tzinfo=timezone.utc)
        def consume():
            try: results.append(self.store.consume_approved_approval('a'))
            except BaseException as exc: results.append(exc)
        try:
            with patch.object(module, 'datetime', CheckedDatetime), patch.object(module.sqlite3, 'connect', side_effect=lambda *a, **k: ConnectionProxy(real_connect(*a, **k))):
                worker = threading.Thread(target=consume)
                worker.start(); self.assertTrue(waiting.wait(2))
                released.set(); conn.rollback(); worker.join(3)
                self.assertFalse(worker.is_alive())
            self.assertEqual(results, [False])
            self.assertEqual(self.store.get_approval('a')['status'], 'approved')
        finally:
            if conn.in_transaction: conn.rollback()
            conn.close()


if __name__ == '__main__': unittest.main(verbosity=2)
