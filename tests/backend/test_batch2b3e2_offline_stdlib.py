"""Offline-only synthetic workflow and dormant-validation regression tests."""
import ast
from contextlib import closing, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('audit_test_support', ROOT / 'tests/backend/test_batch2b3e1_offline_stdlib.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


def dormant_manifest_readback_fixture(readback):
    """Run only the actual dormant manifest/write/readback AST with fake stores.

    This is not ingestion and does not replace, mock, or exercise a policy gate.
    """
    tree = ast.parse((ROOT / 'backend/rag/service.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DocumentService')
    method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'ingest_document')
    start = next(i for i, n in enumerate(method.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'manifest' for t in n.targets))
    end = next(i for i, n in enumerate(method.body[start:], start) if isinstance(n, ast.If) and any(isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == 'record_readback' for x in ast.walk(n)))
    subset = ast.Module(body=method.body[start:end + 1], type_ignores=[])
    ast.fix_missing_locations(subset)
    chunk = types.SimpleNamespace(chunk_id='c1', chunk_index=0, text='synthetic')
    registry = MagicMock()
    backend = MagicMock()
    backend._client = None
    backend.backend_identity = None
    backend._maintenance_get_document_chunks.return_value = readback
    service = types.SimpleNamespace(_source_registry=registry, _store=backend,
                                    _settings=types.SimpleNamespace(chunk_size=80, chunk_overlap=10))
    scope = {'hashlib': hashlib, 'json': json, 'self': service, 'chunks': [chunk],
             'index_model': 'test-model', 'embeddings': [[1.0, 0.0]], 'document_id': 'doc',
             'chunk_ids': ['c1'], 'texts': ['synthetic'], 'metadatas': [dict(readback.get('metadatas', [{}])[0]) if readback.get('metadatas') else {}]}
    exec(compile(subset, 'dormant_service_fixture', 'exec'), scope)
    return registry, backend


class WorkflowCompatibilityTests(unittest.TestCase):
    def test_seeder_reports_skipped_and_failure_without_staging(self):
        spec = importlib.util.spec_from_file_location('seed_workflow_fixture', ROOT / 'scripts/seed_demo_data.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = io.StringIO()
        with redirect_stdout(output):
            code = module.seed_data()
        self.assertNotEqual(code, 0)
        self.assertIn('attempted=0 ingested=0 failed=0 skipped=3', output.getvalue())
        self.assertNotIn('successfully', output.getvalue().lower())

    def test_eval_blocked_before_any_network_or_backend(self):
        class Case:
            def __init__(self, **values): self.__dict__.update(values)
        class Suite(Case): pass
        save, check = MagicMock(), MagicMock(side_effect=AssertionError('network probe'))
        env = {'time': time, 'EvalStatus': types.SimpleNamespace(ENVIRONMENT_UNAVAILABLE='ENVIRONMENT_UNAVAILABLE'),
               'TestCaseResult': Case, 'EvaluationSuiteResult': Suite, 'save_evaluation_results': save,
               'check_ollama_available': check}
        blocked = support.extract('eval/rag_eval.py', '_blocked', cls='RAGEvaluator', env=env)
        run = support.extract('eval/rag_eval.py', 'run', cls='RAGEvaluator', env=env)
        obj = types.SimpleNamespace(load_qa_dataset=lambda: [{'id': '1', 'category': 'test'}])
        obj._blocked = lambda *args: blocked(obj, *args)
        result = support.run_immediate(run(obj))
        self.assertEqual(result.environment_unavailable, 1)
        self.assertEqual(result.summary_metrics, {})
        self.assertTrue(result.errors)
        self.assertEqual(result.test_cases[0].status, 'ENVIRONMENT_UNAVAILABLE')
        check.assert_not_called()
        save.assert_called_once()

    def test_eval_requires_all_requested_documents_eligible(self):
        check = support.extract('eval/rag_eval.py', '_index_is_complete')
        eligible = [types.SimpleNamespace(document_id='a')]
        self.assertFalse(check(0, [], 0, eligible))
        self.assertFalse(check(1, ['a'], 1, eligible))
        self.assertFalse(check(1, ['b'], 0, eligible))
        self.assertFalse(check(2, ['a', 'a'], 0, eligible))
        self.assertTrue(check(1, ['a'], 0, eligible))

    def test_eval_records_ingestion_failure_without_exposing_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'synthetic.md').write_text('synthetic', encoding='utf-8')
            logger = MagicMock()
            setup = support.extract('eval/rag_eval.py', 'setup_index', cls='RAGEvaluator',
                                    env={'DEMO_DATA_DIR': Path(temp), 'logger': logger})
            service = types.SimpleNamespace(ingest_document=AsyncMock(side_effect=RuntimeError('PRIVATE PATH')))
            attempted, ingested, failed = support.run_immediate(setup(object(), service))
            self.assertEqual((attempted, ingested, failed), (1, [], 1))
            self.assertNotIn('PRIVATE PATH', str(logger.warning.call_args))

    def test_ingestion_denial_preserves_rows_and_writes_nothing(self):
        ingest = support.extract('backend/rag/service.py', 'ingest_document', cls='DocumentService')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'registry.sqlite'
            registry = support.provenance.QuarantinedSources(path)
            ctx = support.sys.modules['backend.auth.request_context']
            owner, clearance = ctx.effective_owner.set('owner'), ctx.effective_clearance.set('viewer')
            try:
                pending = registry.reserve_document(b'pending')
                revoked = registry.reserve_document(b'revoked')
                self.assertTrue(registry.revoke(revoked))
                with closing(sqlite3.connect(path)) as db:
                    before = db.execute('SELECT * FROM quarantined_source_registry ORDER BY source_id').fetchall()
                backend = MagicMock(backend_status='persistent_durability_unverified')
                embedder = MagicMock()
                upload_dir = Path(temp) / 'uploads'
                obj = types.SimpleNamespace(_source_registry=registry, _store=backend, _embedder=embedder,
                                            _settings=types.SimpleNamespace(upload_dir=upload_dir))
                with self.assertRaises(RuntimeError):
                    support.run_immediate(ingest(obj, 'a.txt', b'synthetic', 'viewer'))
                with closing(sqlite3.connect(path)) as db:
                    after = db.execute('SELECT * FROM quarantined_source_registry ORDER BY source_id').fetchall()
                self.assertEqual(before, after)
                self.assertEqual(len(after), 2)
                self.assertFalse(upload_dir.exists())
                backend.add_chunks.assert_not_called()
                embedder.embed_many.assert_not_called()
                self.assertFalse(registry.retrieval_eligible(pending))
                self.assertFalse(support.provenance.document_content_allowed())
            finally:
                ctx.effective_clearance.reset(clearance)
                ctx.effective_owner.reset(owner)

    def test_dormant_manifest_and_readback_reject_corruption(self):
        meta = {'document_id': 'doc', 'source_revision': 1, 'chunk_index': 0, 'index_model': 'test-model'}
        good = {'ids': ['c1'], 'documents': ['synthetic'], 'metadatas': [meta]}
        registry, backend = dormant_manifest_readback_fixture(good)
        registry.record_manifest.assert_called_once()
        registry.record_readback.assert_called_once()
        backend.add_chunks.assert_called_once()
        for bad in ({'ids': [], 'documents': [], 'metadatas': []},
                    {'ids': ['c1', 'extra'], 'documents': ['synthetic', 'x'], 'metadatas': [meta, meta]},
                    {'ids': ['c1'], 'documents': ['altered'], 'metadatas': [meta]},
                    {'ids': ['c1'], 'documents': ['synthetic'], 'metadatas': [dict(meta, source_revision=2)]}):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                dormant_manifest_readback_fixture(bad)


if __name__ == '__main__':
    unittest.main()
