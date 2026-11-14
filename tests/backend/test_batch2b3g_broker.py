"""Deterministic offline tests for a simulation, not for an OS broker."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from prototype.storage_broker.contracts import CAPABILITIES, Code, ProtocolError, SourceIdentity, StorageIdentity
from prototype.storage_broker.coordination import SimulatedLocks
from prototype.storage_broker.simulation import InMemoryStorage, InMemoryWitness, SimulatedBroker, SyntheticRecord


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.source = SourceIdentity('src_' + 'a' * 32, 1)
        self.storage = InMemoryStorage(SyntheticRecord(self.source, StorageIdentity('store_' + 'b' * 32)))
        self.witness = InMemoryWitness()
        self.locks = SimulatedLocks()
        self.broker = SimulatedBroker(self.storage, self.witness, self.locks)

    def begin(self, broker=None):
        return (broker or self.broker).handle({'command': 'begin', 'source_id': self.source.source_id, 'expected_generation': 1})

    def finish(self, response, command='finish'):
        return self.broker.handle({'command': command, 'operation_id': response.operation_id, 'generation': response.generation})

    def test_capabilities_are_explicitly_unsupported(self):
        self.assertEqual(CAPABILITIES['simulation'], 'AVAILABLE_IN_MEMORY')
        self.assertTrue(all(v == 'UNSUPPORTED' for k, v in CAPABILITIES.items() if k != 'simulation'))
        self.assertFalse(self.broker.handle({'command': 'status'}).authorized)

    def test_invalid_forged_and_extra_fields(self):
        for req in (None, {}, {'command': []}, {'command': 'status', 'role': 'admin'},
                    {'command': 'begin', 'source_id': '../secret', 'expected_generation': 1},
                    {'command': 'begin', 'source_id': self.source.source_id, 'expected_generation': True},
                    {'command': 'begin', 'source_id': self.source.source_id, 'expected_generation': 1, 'path': 'C:/secret'}):
            self.assertEqual(self.broker.handle(req).code, Code.INVALID_REQUEST)
        self.assertIsNone(self.locks.owner)
        self.assertEqual(self.storage.record.state, 'RESTART_UNVERIFIED')

    def test_unknown_and_denied_commands(self):
        self.assertEqual(self.broker.handle({'command': 'unknown'}).code, Code.UNKNOWN_COMMAND)
        for cmd in ('ingest', 'retrieve', 'mutate_source', 'write_index', 'commit_recovery', 'authorize', 'artifact_read', 'execute_code'):
            self.assertEqual(self.broker.handle({'command': cmd}).code, Code.UNSUPPORTED)
        self.assertFalse(self.broker.handle({'command': 'status'}).authorized)

    def test_forged_and_stale_tokens_never_release_owner(self):
        active = self.begin()
        bad = {'command': 'finish', 'operation_id': 'op_' + 'f' * 32, 'generation': 1}
        self.assertEqual(self.broker.handle(bad).code, Code.STALE)
        self.assertEqual(self.locks.owner, active.operation_id)
        self.assertEqual(self.finish(active).code, Code.OK)
        self.assertEqual(self.finish(active).code, Code.STALE)
        self.assertFalse(self.broker.handle({'command': 'status'}).authorized)

    def test_competing_simulated_brokers(self):
        second = SimulatedBroker(self.storage, self.witness, self.locks)
        active = self.begin()
        self.assertEqual(self.begin(second).code, Code.BUSY)
        self.assertEqual(self.locks.owner, active.operation_id)
        self.assertEqual(self.finish(active).code, Code.OK)

    def test_lock_order_and_canonical_source_order(self):
        with self.assertRaises(ProtocolError) as error:
            self.locks.acquire('a', 1)
        self.assertEqual(error.exception.code, Code.LOCK_ORDER)
        for rank, key in ((0, ''), (1, ''), (2, 'b')):
            self.locks.acquire('a', rank, key)
        with self.assertRaises(ProtocolError):
            self.locks.acquire('a', 2, 'a')
        self.locks.acquire('a', 2, 'c')
        self.locks.acquire('a', 3)
        self.locks.release_all('a')

    def test_revocation_takes_precedence(self):
        active = self.begin()
        self.storage.revoke_for_test()
        self.assertEqual(self.finish(active).code, Code.REVOKED)
        self.assertEqual(self.storage.record.state, 'REVOKED')
        self.assertEqual(self.broker.handle({'command': 'begin', 'source_id': self.source.source_id, 'expected_generation': 2}).code, Code.REVOKED)

    def test_missing_and_rollback_witness(self):
        self.witness.available = False
        self.assertEqual(self.begin().code, Code.WITNESS_UNAVAILABLE)
        self.witness.available = True
        self.witness.epoch = 0
        self.assertEqual(self.begin().code, Code.WITNESS_MISMATCH)
        self.witness.epoch = 1
        active = self.begin()
        self.witness.epoch = 0
        self.assertEqual(self.finish(active).code, Code.WITNESS_MISMATCH)
        self.assertEqual(self.storage.record.state, 'RESTART_UNVERIFIED')

    def test_stale_generation(self):
        active = self.begin()
        self.storage.record.generation += 1
        self.assertEqual(self.finish(active).code, Code.STALE)
        self.assertEqual(self.storage.record.state, 'RESTART_UNVERIFIED')

    def test_interruption_crash_and_repeated_requests(self):
        active = self.begin()
        self.assertEqual(self.finish(active, 'interrupt').state, 'SIMULATED_INTERRUPTED')
        self.assertEqual(self.finish(active).code, Code.STALE)
        active2 = self.begin()
        self.assertNotEqual(active.operation_id, active2.operation_id)
        self.broker.crash_for_test()
        self.assertEqual(self.finish(active2).code, Code.STALE)
        self.assertEqual(self.storage.record.state, 'RESTART_UNVERIFIED')
        self.assertIsNone(self.locks.owner)

    def test_audit_does_not_include_input_or_exception(self):
        self.broker.handle({'command': 'status', 'path': 'PRIVATE_CONTENT'})
        self.assertNotIn('PRIVATE_CONTENT', repr(self.broker.audit))
        self.assertEqual(self.broker.audit[-1].code, Code.INVALID_REQUEST)

    def test_synthetic_components_cannot_be_replaced_by_arbitrary_adapters(self):
        with self.assertRaises(TypeError):
            SimulatedBroker(object(), self.witness, self.locks)
        with self.assertRaises(ValueError):
            SourceIdentity('C:/private', 1)


if __name__ == '__main__':
    unittest.main()
