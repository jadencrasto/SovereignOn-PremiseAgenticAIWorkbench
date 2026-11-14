"""Offline tests of synthetic coordination, not Windows security attestation."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'prototype/cross_store_coordination.py'
spec = importlib.util.spec_from_file_location('cross_store_fixture', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / 'fixture.sqlite'
        conn = sqlite3.connect(self.db)
        try: conn.execute('CREATE TABLE marker(value INTEGER)'); conn.commit()
        finally: conn.close()
        self.file = self.root / 'source.bin'
        self.file.write_bytes(b'synthetic')

    def tearDown(self): self.temp.cleanup()

    def test_observation_and_fail_closed(self):
        self.assertFalse(module.production_coordination_available())
        identity = module.observe_file(self.file)
        coordinator = module.SyntheticCoordinator(self.db)
        with self.assertRaises(RuntimeError): coordinator.observe(self.file, identity)
        with coordinator.exclusive():
            self.assertEqual(coordinator.observe(self.file, identity), 'OBSERVED_UNVERIFIED')
            with self.assertRaises(module.UnsupportedIdentity): coordinator.observe(self.file, None)
            with self.assertRaises(module.UnsupportedIdentity): coordinator.observe(self.file, (identity[0], identity[1] + 1))
            with self.assertRaises(RuntimeError):
                with coordinator.exclusive(): pass

    def test_unavailable_database_and_invalid_timeout(self):
        coordinator = module.SyntheticCoordinator(self.root / 'missing.sqlite')
        with self.assertRaises(module.UnsupportedIdentity):
            with coordinator.exclusive(): pass
        self.assertFalse((self.root / 'missing.sqlite').exists())
        with self.assertRaises(ValueError):
            with coordinator.exclusive(timeout=0): pass

    def test_final_component_symlink_and_reparse_rejected(self):
        real = os.lstat(self.file)
        class SyntheticStat:
            st_mode = real.st_mode
            st_file_attributes = 0x400
        with patch.object(module.os, 'lstat', return_value=SyntheticStat()):
            with self.assertRaises(module.UnsupportedIdentity): module.observe_file(self.file)
        class LinkStat:
            st_mode = 0o120777
            st_file_attributes = 0
        with patch.object(module.os, 'lstat', return_value=LinkStat()):
            with self.assertRaises(module.UnsupportedIdentity): module.observe_file(self.file)

    def test_replaced_file_mismatch(self):
        old = module.observe_file(self.file)
        self.file.rename(self.root / 'old.bin')
        self.file.write_bytes(b'replacement')
        with module.SyntheticCoordinator(self.db).exclusive() as coordinator:
            with self.assertRaises(module.UnsupportedIdentity): coordinator.observe(self.file, old)

    def test_cross_process_timeout_and_crash_release(self):
        script = ("import importlib.util,sys;from pathlib import Path;"
                  "s=importlib.util.spec_from_file_location('fixture',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                  "c=m.SyntheticCoordinator(Path(sys.argv[2]));ctx=c.exclusive();ctx.__enter__();"
                  "print('READY',flush=True);sys.stdin.read()")
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', script, str(SOURCE), str(self.db)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready = []
            thread = threading.Thread(target=lambda: ready.append(child.stdout.readline()), daemon=True)
            thread.start(); thread.join(5)
            self.assertEqual(ready, ['READY\n'])
            with self.assertRaises(sqlite3.OperationalError):
                with module.SyntheticCoordinator(self.db).exclusive(timeout=0.05): pass
            child.kill(); child.wait(timeout=5)
            with module.SyntheticCoordinator(self.db).exclusive(timeout=0.5): pass
        finally:
            if child.poll() is None: child.kill(); child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream and not stream.closed: stream.close()


if __name__ == '__main__': unittest.main(verbosity=2)
