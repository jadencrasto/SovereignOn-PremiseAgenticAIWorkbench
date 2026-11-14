"""Deterministic conceptual model; no production imports, OS locks or I/O.

Tokens and generations are idealized assumptions, not proof of Windows safety.
"""
from dataclasses import dataclass
import unittest


@dataclass(frozen=True)
class Snapshot:
    token: int
    generation: int
    database_epoch: int
    witness_epoch: int
    source: str
    index: str


class SyntheticProtocol:
    def __init__(self):
        self.state = 'RESTART_UNVERIFIED'
        self.generation = 1
        self.database_epoch = 1
        self.witness_epoch = 1
        self.source = 'source-1'
        self.index = 'index-1'
        self.lock_owner = None
        self.next_token = 1
        self.held_token = None

    def begin(self, owner):
        if self.lock_owner is not None or self.state not in ('RESTART_UNVERIFIED', 'RECOVERY_FAILED'):
            return None
        if self.database_epoch != self.witness_epoch:
            self.state = 'RECOVERY_FAILED'
            return None
        self.lock_owner = owner
        self.held_token = self.next_token
        self.next_token += 1
        self.state = 'RECONCILING'
        return Snapshot(self.held_token, self.generation, self.database_epoch,
                        self.witness_epoch, self.source, self.index)

    def revoke(self):
        # Models an independently committed revocation, even against a lock
        # protocol violation. Real enforcement must make this serialization safe.
        self.state = 'REVOKED'
        self.generation += 1

    def commit(self, owner, snapshot, manifest_matches=True):
        if self.lock_owner != owner or self.held_token != snapshot.token:
            return False
        ok = (self.state == 'RECONCILING' and manifest_matches
              and self.generation == snapshot.generation
              and self.database_epoch == snapshot.database_epoch == self.witness_epoch == snapshot.witness_epoch
              and self.source == snapshot.source and self.index == snapshot.index)
        if self.state != 'REVOKED':
            self.state = 'VERIFIED_UNAUTHORIZED' if ok else 'RECOVERY_FAILED'
        self.lock_owner = self.held_token = None
        return ok

    def abandon(self, owner):
        if self.lock_owner == owner:
            self.lock_owner = self.held_token = None
            if self.state == 'RECONCILING':
                self.state = 'RECOVERY_FAILED'

    def restart(self):
        self.lock_owner = self.held_token = None
        if self.state in ('RECONCILING', 'VERIFIED_UNAUTHORIZED'):
            self.state = 'RESTART_UNVERIFIED'

    def authorized(self):
        return False


class ProtocolTests(unittest.TestCase):
    def test_competing_writers(self):
        p = SyntheticProtocol()
        a = p.begin('a')
        self.assertIsNone(p.begin('b'))
        self.assertTrue(p.commit('a', a))
        self.assertFalse(p.authorized())

    def test_revocation_between_snapshot_and_commit(self):
        p = SyntheticProtocol()
        snap = p.begin('a')
        p.revoke()
        self.assertFalse(p.commit('a', snap))
        self.assertEqual(p.state, 'REVOKED')
        self.assertIsNone(p.begin('b'))

    def test_source_and_index_replacement(self):
        for field in ('source', 'index'):
            with self.subTest(field=field):
                p = SyntheticProtocol()
                snap = p.begin('a')
                setattr(p, field, 'replacement')
                self.assertFalse(p.commit('a', snap))
                self.assertEqual(p.state, 'RECOVERY_FAILED')

    def test_database_rollback(self):
        p = SyntheticProtocol()
        snap = p.begin('a')
        p.database_epoch = 0
        self.assertFalse(p.commit('a', snap))
        self.assertIsNone(p.begin('b'))

    def test_restart_interruption_and_stale_token(self):
        p = SyntheticProtocol()
        old = p.begin('a')
        p.restart()
        new = p.begin('b')
        self.assertFalse(p.commit('a', old))
        self.assertTrue(p.commit('b', new))
        self.assertFalse(p.authorized())

    def test_abandoned_lock_requires_fresh_attempt(self):
        p = SyntheticProtocol()
        old = p.begin('a')
        p.abandon('a')
        self.assertEqual(p.state, 'RECOVERY_FAILED')
        new = p.begin('b')
        self.assertFalse(p.commit('a', old))
        self.assertTrue(p.commit('b', new))

    def test_stale_generation_and_manifest_mismatch(self):
        p = SyntheticProtocol()
        old = p.begin('a')
        p.generation += 1
        self.assertFalse(p.commit('a', old))
        new = p.begin('b')
        self.assertFalse(p.commit('b', new, manifest_matches=False))

    def test_repeated_reconciliation_requires_restart_and_never_authorizes(self):
        p = SyntheticProtocol()
        self.assertTrue(p.commit('a', p.begin('a')))
        self.assertIsNone(p.begin('b'))
        p.restart()
        self.assertTrue(p.commit('b', p.begin('b')))
        self.assertEqual(p.state, 'VERIFIED_UNAUTHORIZED')
        self.assertFalse(p.authorized())


if __name__ == '__main__':
    unittest.main()
