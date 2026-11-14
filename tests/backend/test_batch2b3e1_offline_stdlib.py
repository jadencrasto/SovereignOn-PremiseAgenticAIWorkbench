"""Offline checks on production modules and selected extracted function bodies.

Some functions are extracted from the actual source AST with decorators and
defaults omitted. The retriever uses the actual module and exception type.
These are not full framework or process integration tests.
"""
import ast
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def extract(relative, name, *, cls=None, env=None):
    tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
    body = tree.body if cls is None else next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls).body
    node = next(n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    node.args.defaults = [ast.Constant(None) for _ in node.args.defaults]
    node.args.kw_defaults = [ast.Constant(None) if x is not None else None for x in node.args.kw_defaults]
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = dict(env or {})
    exec(compile(module, str(ROOT / relative), 'exec'), scope)
    return scope[name]


def run_immediate(coro):
    """Drive only coroutines that complete without event-loop I/O."""
    try:
        coro.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coro.close()
    raise AssertionError('Coroutine unexpectedly required asynchronous I/O')


from backend.rag import store
from backend.security import provenance_foundation as provenance, task_access as task_policy, execution_policy as execution
from backend.rag.retriever import Retriever, RetrievalBlocked


class HTTPError(Exception):
    def __init__(self, status_code, detail=None, headers=None):
        self.status_code, self.detail, self.headers = status_code, detail, headers


class AuditTests(unittest.TestCase):
    def test_ingestion_fails_before_any_work_for_all_backend_states(self):
        ingest = extract('backend/rag/service.py', 'ingest_document', cls='DocumentService')
        for status in ('persistent_durability_unverified', 'in_memory_fallback', 'initialization_failure', 'verified_persistent', None):
            registry, backend, embedder = MagicMock(), MagicMock(), MagicMock()
            backend.backend_status = status
            service = types.SimpleNamespace(_source_registry=registry, _store=backend, _embedder=embedder)
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, 'Verified persistent index unavailable'):
                run_immediate(ingest(service, 'synthetic.txt', b'data', 'viewer'))
            registry.assert_not_called()
            registry.reserve_document.assert_not_called()
            backend.add_chunks.assert_not_called()
            embedder.embed_many.assert_not_called()

    def test_legacy_sqlite_additive_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'legacy.sqlite'
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE quarantined_source_registry (source_id TEXT PRIMARY KEY, document_reference TEXT NOT NULL, owner_id TEXT NOT NULL, state TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT)")
                db.execute("INSERT INTO quarantined_source_registry VALUES ('legacy','ref','owner','pending',1,'old',NULL)")
                db.commit()
            provenance.QuarantinedSources(path)
            provenance.QuarantinedSources(path)
            with closing(sqlite3.connect(path)) as db:
                row = db.execute('SELECT source_id, state, verification_timestamp, verification_state FROM quarantined_source_registry').fetchone()
            self.assertEqual(row, ('legacy', 'pending', None, None))

    def test_classification_failures_and_path(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(store, '_create_local_chroma_client', side_effect=store.ChromaTelemetryConfigurationError('no')):
                with self.assertRaises(store.ChromaTelemetryConfigurationError):
                    store.VectorStore(root / 'telemetry')
            occupied = root / 'file'
            occupied.write_text('x')
            with self.assertRaises(OSError):
                store.VectorStore(occupied)
            client = MagicMock()
            client.get_or_create_collection.side_effect = RuntimeError('synthetic')
            with patch.object(store, '_create_local_chroma_client', return_value=client):
                instance = store.VectorStore(root / 'folder' / '..' / 'index')
            self.assertEqual(instance.backend_status, 'in_memory_fallback')
            self.assertEqual(instance.backend_identity['directory'], str((root / 'index')).lower())

    def test_document_and_graph_tools_deny_before_access(self):
        backend, embedder = MagicMock(), MagicMock()
        with self.assertRaises(RetrievalBlocked):
            run_immediate(Retriever(embedder, backend).retrieve('synthetic'))
        backend.count.assert_not_called()
        embedder.embed.assert_not_called()
        read = extract('backend/tools/file_read.py', 'create_file_read')(Path('unused'))
        with self.assertRaises(PermissionError):
            run_immediate(read(types.SimpleNamespace()))
        graph = MagicMock()
        query = extract('backend/tools/knowledge_graph.py', 'create_knowledge_graph_query')(graph)
        self.assertFalse(run_immediate(query(query='synthetic'))['found'])
        graph.assert_not_called()

    def test_task_approval_and_artifact_guard(self):
        env = {'HTTPException': HTTPError, '_is_task_accessible': lambda *args: True,
               'task_content_revalidation_available': task_policy.task_content_revalidation_available, '_WITHHELD': 'withheld'}
        approve = extract('backend/api/tasks.py', 'approve_task_step', env=env)
        manager, approvals, engine = MagicMock(), MagicMock(), MagicMock()
        with self.assertRaises(HTTPError) as caught:
            run_immediate(approve('t', object(), object(), manager, approvals, engine, object()))
        self.assertEqual(caught.exception.status_code, 403)
        approvals.get_pending_for_task.assert_not_called()
        engine.resume_agent_task.assert_not_called()
        guard = extract('backend/api/artifacts.py', '_is_artifact_accessible')
        storage = MagicMock()
        self.assertFalse(guard('synthetic', object(), storage))
        storage.assert_not_called()
        request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(task_store=storage)))
        for name in ('preview_artifact', 'download_artifact'):
            handler = extract('backend/api/artifacts.py', name, env={'HTTPException': HTTPError, '_is_artifact_accessible': guard})
            with self.assertRaises(HTTPError) as denied:
                run_immediate(handler('synthetic.txt', request, object()))
            self.assertEqual(denied.exception.status_code, 404)
        resume = extract('backend/agent/engine.py', 'resume_agent_task', cls='AgentEngine')
        agent = types.SimpleNamespace(_task_manager=MagicMock(), _approval_manager=MagicMock())
        events = resume(agent, 't', 'a', True, 'admin', 'user')
        self.assertEqual(run_immediate(events.__anext__())['type'], 'error')
        run_immediate(events.aclose())
        agent._task_manager.assert_not_called()
        agent._approval_manager.assert_not_called()

    def test_code_execution_direct_factory_is_denied(self):
        make = extract('backend/tools/code_execution.py', 'create_code_execution')
        with tempfile.TemporaryDirectory() as temp:
            fn = make(Path(temp), object())
            with self.assertRaises(PermissionError):
                run_immediate(fn(object()))

    def test_upload_route_maps_block_to_503(self):
        ingest = extract('backend/rag/service.py', 'ingest_document', cls='DocumentService')
        service = types.SimpleNamespace(_source_registry=MagicMock(), _store=types.SimpleNamespace(backend_status='in_memory_fallback'),
                                        _settings=types.SimpleNamespace(max_file_size_mb=1))
        service.ingest_document = lambda *args, **kwargs: ingest(service, *args, **kwargs)
        file = types.SimpleNamespace(filename='synthetic.txt', content_type='text/plain',
                                     read=AsyncMock(side_effect=[b'data', b'']), close=AsyncMock())
        env = {'HTTPException': HTTPError, 'logger': MagicMock(), 'resolve_user_clearance': lambda user: 'viewer',
               'public_error': lambda code: code}
        upload = extract('backend/api/documents.py', 'upload_document', env=env)
        with self.assertRaises(HTTPError) as caught:
            run_immediate(upload(file, service, object()))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.headers['X-Error-Code'], 'DOCUMENT_UNAVAILABLE')
        service._source_registry.reserve_document.assert_not_called()


if __name__ == '__main__':
    unittest.main()
