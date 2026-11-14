"""Synthetic protocol tests: in-memory only; no production imports or network."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('witness_model', ROOT / 'prototype/witness_protocol.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.w = m.SyntheticWitness(b'synthetic-key-123456')
        self.c = m.SyntheticClient(b'synthetic-key-123456')
        self.i = 0

    def apply(self, operation, approval='a'):
        self.i += 1
        request, mac = self.c.begin('req_' + str(self.i), approval, operation)
        response, proof = self.w.process(request, mac)
        self.assertEqual(self.c.accept(response, proof, (self.w.epoch,self.w.sequence,self.w.head)), 'RECORDED_UNAUTHORIZED')
        self.assertFalse(self.c.authorized())
        return request, mac, response, proof

    def test_issue_consume_and_revoke(self):
        self.apply('issue'); self.apply('consume'); self.apply('revoke')
        self.assertEqual(self.w.states['a'], 'consumed_revoked')
        self.assertEqual(self.c.reconcile(self.w, (self.w.epoch,self.w.sequence,self.w.head)), 'CONSISTENT_UNAUTHORIZED')
        self.assertFalse(self.c.authorized())

    def test_expire_and_inspection(self):
        self.apply('inspect'); self.apply('issue'); self.apply('expire')
        self.assertEqual(self.w.states['a'], 'expired')
        self.assertEqual(self.c.reconcile(self.w, (1,self.w.sequence,self.w.head)), 'CONSISTENT_UNAUTHORIZED')

    def test_duplicate_and_request_authentication(self):
        req, mac, _, _ = self.apply('issue')
        with self.assertRaisesRegex(m.ProtocolError, 'replay'): self.w.process(req, mac)
        bad = dict(req, request_id='new')
        with self.assertRaisesRegex(m.ProtocolError, 'authentication_failed'): self.w.process(bad, mac)
        self.assertEqual(self.w.sequence, 1)

    def test_stale_sequence_unknown_epoch_and_malformed(self):
        req, _, _, _ = self.apply('issue')
        for update in ({'request_id':'new','sequence':0}, {'request_id':'new','epoch':2}, {'request_id':'../bad'}, {'operation':[]}):
            message = dict(req, **update)
            with self.subTest(update=update), self.assertRaises(m.ProtocolError): self.w.process(message, self.w.sign(message))
        self.assertEqual(self.w.sequence, 1)

    def test_local_rollback_and_conflicting_history(self):
        self.apply('issue'); old = copy.deepcopy(self.c); self.apply('consume')
        self.assertEqual(old.reconcile(self.w, (1,self.w.sequence,self.w.head)), 'QUARANTINED')
        fake = copy.deepcopy(self.w); fake.head = 'f'*64
        self.assertEqual(self.c.reconcile(fake, (1,fake.sequence,fake.head)), 'QUARANTINED')

    def test_witness_rollback_against_simulated_anchor(self):
        self.apply('issue'); old = copy.deepcopy(self.w); self.apply('consume')
        anchor = (1,self.w.sequence,self.w.head)
        self.assertEqual(self.c.reconcile(old, anchor), 'QUARANTINED')
        self.assertFalse(self.c.authorized())

    def test_lost_witness_and_interrupted_intent(self):
        self.c.begin('r1','a','issue'); self.c.interrupted()
        self.assertEqual(self.c.reconcile(None, None), 'QUARANTINED')
        with self.assertRaises(m.ProtocolError): self.c.begin('r2','a','issue')

    def test_missing_anchor_and_reused_approval_denied(self):
        self.apply('issue'); self.apply('consume')
        req = {'version':1,'request_id':'r3','approval_id':'a','operation':'issue','sequence':2,'epoch':1}
        with self.assertRaisesRegex(m.ProtocolError, 'invalid_transition'): self.w.process(req,self.w.sign(req))
        self.assertEqual(self.c.reconcile(self.w, None), 'QUARANTINED')

    def test_delayed_ack_after_witness_advanced_is_quarantined_on_reconcile(self):
        req, mac = self.c.begin('r1','a','issue')
        response, proof = self.w.process(req, mac)
        other = {'version':1,'request_id':'r2','approval_id':'a','operation':'revoke','sequence':1,'epoch':1}
        self.w.process(other,self.w.sign(other))
        with self.assertRaises(m.ProtocolError): self.c.accept(response,proof,(1,self.w.sequence,self.w.head))
        self.assertEqual(self.c.reconcile(self.w,(1,self.w.sequence,self.w.head)), 'QUARANTINED')
        self.assertFalse(self.c.authorized())

    def test_corrupt_ack_and_external_effect(self):
        req, mac = self.c.begin('r1','a','issue'); response, proof = self.w.process(req,mac)
        with self.assertRaises(m.ProtocolError): self.c.accept(dict(response,state='consumed'),proof,(1,self.w.sequence,self.w.head))
        self.assertEqual(self.c.phase,'QUARANTINED')
        self.assertEqual(self.c.external_effect(),'EFFECT_UNSUPPORTED')
        self.assertFalse(self.c.authorized())

    def test_acknowledgement_loss_and_partial_commit_remain_quarantined(self):
        req, mac = self.c.begin('r1','a','issue'); self.w.process(req,mac)
        self.c.interrupted()
        self.assertEqual(self.c.reconcile(self.w,(1,self.w.sequence,self.w.head)), 'QUARANTINED')
        self.assertIsNotNone(self.c.pending)

    def test_canonical_encoding_stable(self):
        self.assertEqual(m.encode({'b':1,'a':2}), b'{"a":2,"b":1}')

if __name__ == '__main__': unittest.main(verbosity=2)
