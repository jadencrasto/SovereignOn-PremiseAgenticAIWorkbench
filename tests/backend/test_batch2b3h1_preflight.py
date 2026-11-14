"""No native module imports, OS calls, pipes, subprocesses, or network."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from prototype.windows_broker_ipc import preflight


class PreflightTests(unittest.TestCase):
    def test_missing_binding_does_not_enable_transport(self):
        with patch.object(preflight.importlib.util, 'find_spec', return_value=None), \
             patch.object(preflight.importlib.metadata, 'version', side_effect=preflight.importlib.metadata.PackageNotFoundError):
            result = preflight.inspect_environment()
        self.assertEqual(result.missing_modules, preflight.REQUIRED_MODULES)
        self.assertIsNone(result.binding_version)
        self.assertFalse(result.binding_matches_pin)
        self.assertFalse(result.transport_available)

    def test_even_matching_binding_does_not_enable_transport(self):
        with patch.object(preflight.importlib.util, 'find_spec', return_value=object()), \
             patch.object(preflight.importlib.metadata, 'version', return_value=preflight.EXPECTED_VERSION), \
             patch.object(preflight.sys, 'platform', 'win32'):
            result = preflight.inspect_environment()
        self.assertTrue(result.binding_matches_pin)
        self.assertFalse(result.transport_available)
        self.assertEqual(result.peer_authentication, 'UNSUPPORTED')
        self.assertEqual(result.server_authentication, 'UNSUPPORTED')
        self.assertEqual(result.exclusive_ownership, 'UNSUPPORTED')

    def test_wrong_version_and_non_windows_rejected(self):
        with patch.object(preflight.importlib.util, 'find_spec', return_value=object()), \
             patch.object(preflight.importlib.metadata, 'version', return_value='wrong'):
            self.assertFalse(preflight.inspect_environment().binding_matches_pin)
        with patch.object(preflight.importlib.util, 'find_spec', return_value=object()), \
             patch.object(preflight.importlib.metadata, 'version', return_value=preflight.EXPECTED_VERSION), \
             patch.object(preflight.sys, 'platform', 'linux'):
            self.assertFalse(preflight.inspect_environment().binding_matches_pin)


if __name__ == '__main__':
    unittest.main()
