"""In-memory descriptor and protocol tests; never opens a pipe."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import win32security
from prototype.windows_broker_ipc import dev_pipe, rights_design


class OfflineTests(unittest.TestCase):
    def test_descriptor_uses_specific_candidate_rights(self):
        sid = win32security.ConvertStringSidToSid('S-1-5-21-1-2-3-1001')
        attrs = dev_pipe._security_attributes(sid)
        acl = attrs.SECURITY_DESCRIPTOR.GetSecurityDescriptorDacl()
        self.assertEqual(acl.GetAceCount(), 1)
        mask = acl.GetAce(0)[1]
        self.assertEqual(mask, rights_design.CLIENT_CANDIDATE)
        self.assertEqual(mask & rights_design.FILE_CREATE_PIPE_INSTANCE, 0)
        self.assertFalse(attrs.bInheritHandle)

    def test_responses_are_development_only_and_deny_storage(self):
        for name in ('ping', 'status', 'capabilities'):
            result = json.loads(dev_pipe._response(json.dumps({'version': 1, 'command': name}).encode()))
            self.assertTrue(result['development_only'])
            self.assertFalse(result['trusted_broker'])
        result = json.loads(dev_pipe._response(b'{"version":1,"command":"ingest"}'))
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'UNSUPPORTED_COMMAND')

    def test_public_transport_remains_unavailable(self):
        with self.assertRaises(RuntimeError):
            dev_pipe.serve()
        with self.assertRaises(RuntimeError):
            dev_pipe.request(b'{}')


if __name__ == '__main__':
    unittest.main()
