"""Offline-only synthetic tests: run directly with Python's unittest runner."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recovery = load('index_reconciliation_test', 'backend/security/index_reconciliation.py')
store = load('index_store_test', 'backend/rag/store.py')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.sid = 'doc_v1_' + 'a' * 32
        self.backend = {'backend': 'chromadb', 'directory': 'C:/synthetic', 'collection': 'workbench_documents'}
        self.config = {'backend_identity': self.backend, 'embedding_model': 'synthetic', 'dimensions': 2,
                       'source_revision': 1, 'model_digest': None}
        manifest = [{'chunk_id': self.sid + '_chunk_0', 'chunk_index': 0, 'sha256': hashlib.sha256(b'chunk').hexdigest()}]
        self.record = {'state': 'pending', 'reconciliation_status': 'restart_unverified', 'source_id': self.sid,
                       'owner_id': 'synthetic-owner', 'classification': 'viewer',
                       'source_revision': 1, 'storage_ref': self.sid + '.bin',
                       'content_sha256': hashlib.sha256(b'source').hexdigest(), 'index_model': 'synthetic',
                       'manifest_json': json.dumps(manifest), 'index_configuration_json': json.dumps(self.config)}
        self.identity = {'backend_identity': self.backend, 'backend_status': 'verified_persistent',
                         'embedding_model': 'synthetic', 'dimensions': 2, 'model_digest': None}
        self.chunks = [{'id': self.sid + '_chunk_0', 'text': 'chunk', 'embedding': [0, 1],
                        'metadata': {'document_id': self.sid, 'source_revision': 1,
                                     'chunk_index': 0, 'index_model': 'synthetic', 'clearance': 'viewer'}}]

    def compare(self, source=b'source'):
        return recovery.compare_snapshot(self.record, source, self.chunks, self.identity)

    def test_match_repeat_is_non_authorizing_and_has_no_side_effects(self):
        before = copy.deepcopy((self.record, self.chunks, self.identity))
        self.assertEqual(self.compare(), 'match_unverified')
        self.assertEqual(self.compare(), 'match_unverified')
        self.assertEqual(before, (self.record, self.chunks, self.identity))

    def test_missing_and_corrupt_source(self):
        self.assertEqual(self.compare(None), 'mismatch')
        self.assertEqual(self.compare(b'corrupt'), 'mismatch')

    def test_missing_extra_modified_chunks(self):
        original = copy.deepcopy(self.chunks)
        for chunks in ([], original + [dict(original[0], id='extra')], [dict(original[0], text='changed')]):
            self.chunks = chunks
            self.assertEqual(self.compare(), 'mismatch')

    def test_wrong_revision_model_dimension_and_backend(self):
        for key, value in [('source_revision', 2), ('index_model', 'other')]:
            old = self.chunks[0]['metadata'][key]
            self.chunks[0]['metadata'][key] = value
            self.assertEqual(self.compare(), 'mismatch')
            self.chunks[0]['metadata'][key] = old
        for key, value in [('dimensions', 3), ('embedding_model', 'other'), ('backend_status', 'in_memory_fallback')]:
            old = self.identity[key]
            self.identity[key] = value
            self.assertEqual(self.compare(), 'mismatch')
            self.identity[key] = old

    def test_legacy_interrupted_and_revoked(self):
        self.record['manifest_json'] = None
        self.assertEqual(self.compare(), 'invalid')
        self.record['state'] = 'revoked'
        self.assertEqual(self.compare(), 'ineligible')
        self.record['state'] = 'pending'
        self.record['reconciliation_status'] = None
        self.assertEqual(self.compare(), 'ineligible')

    def test_fabricated_digest_rejected(self):
        self.identity['model_digest'] = 'sha256:' + 'a' * 64
        self.assertEqual(self.compare(), 'invalid')

    def test_backend_classification(self):
        class Client:
            def get_or_create_collection(self, **kwargs):
                return store._InMemoryFallbackCollection()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(store, '_create_local_chroma_client', return_value=Client()):
                selected = store.VectorStore(Path(directory))
            self.assertEqual(selected.backend_status, 'persistent_durability_unverified')
            self.assertEqual(selected.backend_identity['collection'], store.COLLECTION_NAME)
            with patch.object(store, '_create_local_chroma_client', side_effect=RuntimeError('synthetic')):
                fallback = store.VectorStore(Path(directory))
            self.assertEqual(fallback.backend_status, 'in_memory_fallback')
            self.assertFalse(store.persistent_security_guarantees_available())


if __name__ == '__main__':
    unittest.main()
