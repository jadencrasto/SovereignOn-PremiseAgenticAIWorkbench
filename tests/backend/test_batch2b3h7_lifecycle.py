"""Offline fault-injection tests; not Windows I/O integration."""
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from prototype.windows_broker_ipc.overlapped_lifecycle import Completion, Started, UnsafePendingIO, run_operation


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.backend = MagicMock()
        self.resources = object()

    def test_immediate_completion_count(self):
        self.backend.start.return_value = Started(Completion.COMPLETE, 4)
        self.assertEqual(run_operation(self.backend, self.resources, 10, 10), 4)
        self.backend.cancel.assert_not_called()

    def test_pending_completion(self):
        self.backend.start.return_value = Started(Completion.PENDING)
        self.backend.wait.return_value = True
        self.backend.result.return_value = 8
        self.assertEqual(run_operation(self.backend, self.resources, 10, 10), 8)

    def test_timeout_requests_cancel_then_drains(self):
        self.backend.start.return_value = Started(Completion.PENDING)
        self.backend.wait.side_effect = [False, True]
        with self.assertRaises(TimeoutError):
            run_operation(self.backend, self.resources, 10, 10)
        self.backend.cancel.assert_called_once_with(self.resources)
        self.backend.drain.assert_called_once_with(self.resources)

    def test_interrupt_also_drains(self):
        self.backend.start.return_value = Started(Completion.PENDING)
        self.backend.wait.side_effect = [KeyboardInterrupt(), True]
        with self.assertRaises(KeyboardInterrupt):
            run_operation(self.backend, self.resources, 10, 10)
        self.backend.drain.assert_called_once()

    def test_unconfirmed_drain_is_fatal(self):
        self.backend.start.return_value = Started(Completion.PENDING)
        self.backend.wait.side_effect = [False, False]
        with self.assertRaises(UnsafePendingIO):
            run_operation(self.backend, self.resources, 10, 10)

    def test_invalid_counts_and_deadlines_rejected(self):
        self.backend.start.return_value = Started(Completion.COMPLETE, -1)
        with self.assertRaises(ValueError):
            run_operation(self.backend, self.resources, 10, 10)
        with self.assertRaises(ValueError):
            run_operation(self.backend, self.resources, 0, 10)


if __name__ == '__main__':
    unittest.main()
