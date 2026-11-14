"""Tests peer-token control flow with fakes, not Windows authentication."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from prototype.windows_broker_ipc import peer_identity


class PeerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.security = MagicMock(TokenImpersonationLevel=1, TokenUser=2, SecurityIdentification=1)
        self.security.ConvertStringSidToSid.return_value = 'allowed'
        self.token = MagicMock()
        self.security.OpenThreadToken.return_value = self.token
        self.security.GetTokenInformation.side_effect = lambda token, kind: 1 if kind == 1 else ('actual', 0)
        self.security.ConvertSidToStringSid.side_effect = lambda sid: 'S-1-5-21-1'
        self.api = MagicMock()
        self.con = types.SimpleNamespace(TOKEN_QUERY=8)
        self.loader = patch.object(peer_identity, '_windows_api', return_value=(self.security, self.api, self.con))
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def test_matching_sid_reverts_and_closes(self):
        result = peer_identity.inspect_pipe_client(object(), 'S-1-5-21-1')
        self.assertTrue(result.accepted)
        self.token.Close.assert_called_once()
        self.security.RevertToSelf.assert_called_once()

    def test_other_sid_denied(self):
        self.security.ConvertSidToStringSid.side_effect = lambda sid: sid
        self.assertEqual(peer_identity.inspect_pipe_client(object(), 'S-1-5-21-1').code, 'SID_DENIED')
        self.security.RevertToSelf.assert_called_once()

    def test_anonymous_level_denied(self):
        self.security.GetTokenInformation.side_effect = None
        self.security.GetTokenInformation.return_value = 0
        self.assertEqual(peer_identity.inspect_pipe_client(object(), 'S-1-5-21-1').code, 'INSUFFICIENT_TOKEN_LEVEL')
        self.security.RevertToSelf.assert_called_once()

    def test_failed_impersonation_denied(self):
        self.security.ImpersonateNamedPipeClient.side_effect = OSError('secret')
        self.assertEqual(peer_identity.inspect_pipe_client(object(), 'S-1-5-21-1').code, 'IDENTITY_UNAVAILABLE')
        self.security.OpenThreadToken.assert_not_called()

    def test_failed_revert_forces_denial(self):
        self.security.RevertToSelf.side_effect = OSError('secret')
        self.assertEqual(peer_identity.inspect_pipe_client(object(), 'S-1-5-21-1').code, 'CLEANUP_FAILED')

    def test_missing_policy_is_denied_before_api(self):
        self.assertEqual(peer_identity.inspect_pipe_client(object(), '').code, 'INVALID_POLICY')
        self.security.ImpersonateNamedPipeClient.assert_not_called()


if __name__ == '__main__':
    unittest.main()
