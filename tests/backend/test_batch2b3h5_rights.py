"""Static design invariants, not Windows effective-access tests."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from prototype.windows_broker_ipc import rights_design as rights


class CandidateRightsTests(unittest.TestCase):
    def test_client_candidate_excludes_create_instance(self):
        self.assertEqual(rights.CLIENT_CANDIDATE, 0x00100183)
        self.assertEqual(rights.CLIENT_CANDIDATE & rights.FILE_CREATE_PIPE_INSTANCE, 0)

    def test_candidate_is_not_generic_or_standard_write(self):
        self.assertEqual(rights.CLIENT_CANDIDATE & 0xC0000000, 0)
        self.assertEqual(rights.CLIENT_CANDIDATE & 0x00040000, 0)
        self.assertEqual(rights.CLIENT_CANDIDATE & 0x00080000, 0)


if __name__ == '__main__':
    unittest.main()
