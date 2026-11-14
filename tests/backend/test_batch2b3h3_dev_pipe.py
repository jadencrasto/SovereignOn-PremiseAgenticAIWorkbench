"""Local functional checks only; no claim of adversarial IPC security."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from prototype.windows_broker_ipc import dev_pipe


class DevelopmentPipeTests(unittest.TestCase):
    def test_unverified_listener_and_client_refuse_to_start(self):
        with self.assertRaisesRegex(RuntimeError, 'disabled'):
            dev_pipe.serve()
        with self.assertRaisesRegex(RuntimeError, 'disabled'):
            dev_pipe.request(b'{"version":1,"command":"ping"}')

    @unittest.skip('Named-pipe access denied in managed environment; unverified draft is disabled')
    def test_separate_process_protocol_and_restart(self):
        executable = sys.executable
        script = str(ROOT / 'prototype/windows_broker_ipc/dev_pipe.py')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        def start():
            return subprocess.Popen([executable, '-I', script, 'serve'], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, creationflags=flags)
        def stop(process):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        def ask(raw):
            deadline = time.monotonic() + 5
            while True:
                try:
                    return json.loads(dev_pipe.request(raw))
                except Exception:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        server = start()
        try:
            for name in ('ping', 'status', 'capabilities'):
                reply = ask(json.dumps({'version': 1, 'command': name}).encode())
                self.assertTrue(reply['ok'])
                self.assertTrue(reply['development_only'])
                self.assertFalse(reply['trusted_broker'])
                self.assertEqual(reply['command'], name)
            self.assertEqual(ask(b'{"version":1,"command":"ingest"}')['error'], 'UNSUPPORTED_COMMAND')
            self.assertEqual(ask(b'{') ['error'], 'INVALID_REQUEST')
            self.assertEqual(ask(b'x' * 1025)['error'], 'REQUEST_TOO_LARGE')
            duplicate = subprocess.run([executable, '-I', script, 'serve'], capture_output=True,
                                       timeout=5, creationflags=flags)
            self.assertNotEqual(duplicate.returncode, 0)
        finally:
            stop(server)
        restarted = start()
        try:
            self.assertTrue(ask(b'{"version":1,"command":"ping"}')['ok'])
        finally:
            stop(restarted)


if __name__ == '__main__':
    unittest.main()
