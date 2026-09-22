"""
tests/backend/test_security_step11_hardening.py
------------------------------------------------
Regression tests for:
1. Custom settings propagation inside lifespan and tool execution factories.
2. Authentication-disabled production startup fail-closed semantics vs dev override.
3. Clearance escalation prevention via query parameters.
4. Knowledge Graph failure behavior in production (audited 503, no silent fallback).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.config import Settings
from backend.auth.models import AuthStore, User, UserRole
from backend.auth.security import hash_password
from backend.audit.logger import AuditLogger
from backend.utils.config_validation import ConfigValidator, ConfigValidationError


@pytest.fixture
def test_dirs(tmp_path: Path):
    d = {
        "sandbox": tmp_path / "custom_sandbox",
        "uploads": tmp_path / "custom_uploads",
        "tasks": tmp_path / "custom_tasks",
        "chromadb": tmp_path / "custom_chromadb",
        "db": tmp_path / "custom_tasks" / "tasks.db",
    }
    for p in d.values():
        if p.suffix == "":
            p.mkdir(parents=True, exist_ok=True)
    return d


def create_user_with_token(db_path: Path, username: str, role: str) -> str:
    from backend.auth.security import SessionManager
    store = AuthStore(db_path=db_path)
    user = User(
        id=f"user_{username}",
        username=username,
        password_hash=hash_password("ValidPassword123!"),
        role=role,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    store.create_user(user)
    session_mgr = SessionManager(store=store, idle_timeout_seconds=3600, absolute_timeout_seconds=7200)
    raw_token, session = session_mgr.create_session(user.id)
    return raw_token


# ---------------------------------------------------------------------------
# 1. Custom Settings Propagation
# ---------------------------------------------------------------------------

class TestCustomSettingsPropagation:
    def test_custom_settings_propagate_to_lifespan_components(self, test_dirs):
        custom_cfg = Settings(
            app_env="development",
            auth_enabled=False,
            sandbox_dir=test_dirs["sandbox"],
            upload_dir=test_dirs["uploads"],
            tasks_dir=test_dirs["tasks"],
            tasks_db_path=test_dirs["db"],
            chroma_persist_dir=test_dirs["chromadb"],
            max_plan_steps=42,
            approval_timeout_seconds=777,
            ollama_base_url="http://127.0.0.1:21434",
            embedding_model="custom-embed-model",
        )

        app = create_app(custom_cfg)
        with TestClient(app) as client:
            # 1. DocumentService uses custom settings
            assert app.state.doc_service._settings.sandbox_dir == test_dirs["sandbox"]
            assert app.state.doc_service._settings.upload_dir == test_dirs["uploads"]
            assert app.state.upload_dir == test_dirs["uploads"]

            # 2. Engine uses custom settings
            assert app.state.engine._settings.max_plan_steps == 42

            # 3. Planner and approval manager use custom timeouts & steps
            assert app.state.planner.max_plan_steps == 42
            assert app.state.approval_manager._timeout_seconds == 777

            # 4. Knowledge graph SQLite DB path derived from custom tasks_dir
            assert app.state.graph_store.db_path == test_dirs["tasks"] / "knowledge_graph.db"

            # 5. ModelRouter uses custom settings
            assert app.state.model_router._settings.embedding_model == "custom-embed-model"


# ---------------------------------------------------------------------------
# 2. Authentication-Disabled Production Startup
# ---------------------------------------------------------------------------

class TestAuthDisabledProductionStartup:
    def test_prod_auth_disabled_fails_closed_without_override(self, test_dirs):
        prod_insecure_cfg = Settings(
            app_env="production",
            auth_enabled=False,
            auth_dev_override=False,
            tasks_dir=test_dirs["tasks"],
            tasks_db_path=test_dirs["db"],
            sandbox_dir=test_dirs["sandbox"],
            upload_dir=test_dirs["uploads"],
            chroma_persist_dir=test_dirs["chromadb"],
        )

        validator = ConfigValidator(prod_insecure_cfg)
        is_valid, results = validator.validate()
        assert is_valid is False
        auth_rule = next(r for r in results if r["rule"] == "prod_auth_enabled")
        assert auth_rule["status"] == "FAIL"

        with pytest.raises(ConfigValidationError):
            validator.enforce_or_exit()

        # Full app startup also fails closed during lifespan
        app = create_app(prod_insecure_cfg)
        with pytest.raises(ConfigValidationError):
            with TestClient(app):
                pass

    def test_prod_auth_disabled_allowed_with_explicit_dev_override(self, test_dirs):
        prod_override_cfg = Settings(
            app_env="production",
            auth_enabled=False,
            auth_dev_override=True,  # Explicit development override
            tasks_dir=test_dirs["tasks"],
            tasks_db_path=test_dirs["db"],
            sandbox_dir=test_dirs["sandbox"],
            upload_dir=test_dirs["uploads"],
            chroma_persist_dir=test_dirs["chromadb"],
            code_exec_isolation="docker",
        )

        validator = ConfigValidator(prod_override_cfg)
        is_valid, results = validator.validate()
        auth_rule = next(r for r in results if r["rule"] == "prod_auth_enabled")
        assert auth_rule["status"] == "WARN"

        # Full app startup succeeds with override (mock Docker checks)
        with patch("backend.tools.code_execution.check_docker_daemon_available", return_value=True), \
             patch("backend.tools.code_execution.check_docker_image_available", return_value=True):
            validator2 = ConfigValidator(prod_override_cfg)
            validator2.enforce_or_exit()

            app = create_app(prod_override_cfg)
            with TestClient(app) as client:
                resp = client.get("/api/health/live")
                assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. Clearance Escalation Attempts
# ---------------------------------------------------------------------------

class TestClearanceEscalationAttempts:
    @pytest.fixture
    def prod_app(self, test_dirs):
        cfg = Settings(
            app_env="production",
            auth_enabled=True,
            auth_cookie_secure=False,
            tasks_dir=test_dirs["tasks"],
            tasks_db_path=test_dirs["db"],
            sandbox_dir=test_dirs["sandbox"],
            upload_dir=test_dirs["uploads"],
            chroma_persist_dir=test_dirs["chromadb"],
            code_exec_isolation="docker",
        )
        with patch("backend.tools.code_execution.check_docker_daemon_available", return_value=True), \
             patch("backend.tools.code_execution.check_docker_image_available", return_value=True):
            app = create_app(cfg)
            with TestClient(app) as client:
                yield {"app": app, "client": client}

    def test_viewer_query_param_cannot_escalate_to_admin(self, prod_app, test_dirs):
        viewer_token = create_user_with_token(test_dirs["db"], "viewer_user", UserRole.VIEWER.value)
        client = prod_app["client"]

        # Viewer attempts escalation via ?clearance=admin
        resp = client.get(
            "/api/knowledge-graph?clearance=admin",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()

        # Escalation must be rejected
        assert data["user_role"] == "viewer"
        assert data["effective_clearance"] == "viewer"
        assert data["clearance_level"] == 1

        # Operator and admin defect/classified nodes must not be revealed
        categories = {n["category"] for n in data["nodes"]}
        assert "defect" not in categories
        assert "classified" not in categories

    def test_admin_query_param_allows_down_scoping(self, prod_app, test_dirs):
        admin_token = create_user_with_token(test_dirs["db"], "admin_user", UserRole.ADMIN.value)
        client = prod_app["client"]

        # Admin legitimately requests down-scoped viewer perspective
        resp = client.get(
            "/api/knowledge-graph?clearance=viewer",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()

        # Role remains admin, but effective clearance is down-scoped
        assert data["user_role"] == "admin"
        assert data["effective_clearance"] == "viewer"
        assert data["clearance_level"] == 1


# ---------------------------------------------------------------------------
# 4. Knowledge Graph Failure Behavior
# ---------------------------------------------------------------------------

class TestKnowledgeGraphFailureBehavior:
    def test_prod_failure_returns_503_and_logs_audit_event(self, test_dirs):
        cfg = Settings(
            app_env="production",
            auth_enabled=True,
            auth_cookie_secure=False,
            tasks_dir=test_dirs["tasks"],
            tasks_db_path=test_dirs["db"],
            sandbox_dir=test_dirs["sandbox"],
            upload_dir=test_dirs["uploads"],
            chroma_persist_dir=test_dirs["chromadb"],
            code_exec_isolation="docker",
        )
        admin_token = create_user_with_token(test_dirs["db"], "admin_auditor", UserRole.ADMIN.value)
        with patch("backend.tools.code_execution.check_docker_daemon_available", return_value=True), \
             patch("backend.tools.code_execution.check_docker_image_available", return_value=True):
            app = create_app(cfg)

            with TestClient(app) as client:
                # Simulate KnowledgeGraphService failure
                mock_service = MagicMock()
                mock_service.get_full_graph.side_effect = RuntimeError("SQLite disk I/O error simulation")
                app.state.graph_service = mock_service

                resp = client.get(
                    "/api/knowledge-graph",
                    headers={"Authorization": f"Bearer {admin_token}"},
                )

                # Must return 503, never silent fallback in prod
                assert resp.status_code == 503
                assert "Knowledge graph service is temporarily unavailable" in resp.json()["detail"]

                # Must write audit event
                audit_store = AuditLogger(db_path=test_dirs["db"])
                result = audit_store.query_events(event_type="knowledge_graph.failure")
                events = result["events"]
                assert len(events) >= 1
                assert events[0]["success"] is False
                assert "SQLite disk I/O error simulation" in events[0]["failure_reason"]

    def test_dev_failure_without_legacy_fallback_returns_503(self, test_dirs):
        dev_cfg = Settings(
            app_env="development",
            auth_enabled=False,
            allow_legacy_kg_fallback=False,  # Explicitly disabled
            tasks_dir=test_dirs["tasks"],
            tasks_db_path=test_dirs["db"],
            sandbox_dir=test_dirs["sandbox"],
            upload_dir=test_dirs["uploads"],
            chroma_persist_dir=test_dirs["chromadb"],
        )
        app = create_app(dev_cfg)

        with TestClient(app) as client:
            app.state.graph_service = None  # Service not initialized

            resp = client.get("/api/knowledge-graph")
            assert resp.status_code == 503
            assert "Knowledge graph service unavailable" in resp.json()["detail"]

    def test_dev_failure_with_legacy_fallback_returns_fallback_data(self, test_dirs):
        dev_cfg = Settings(
            app_env="development",
            auth_enabled=False,
            allow_legacy_kg_fallback=True,  # Enabled in dev for test compatibility
            tasks_dir=test_dirs["tasks"],
            tasks_db_path=test_dirs["db"],
            sandbox_dir=test_dirs["sandbox"],
            upload_dir=test_dirs["uploads"],
            chroma_persist_dir=test_dirs["chromadb"],
        )
        app = create_app(dev_cfg)

        with TestClient(app) as client:
            app.state.graph_service = None  # Force fallback

            resp = client.get("/api/knowledge-graph")
            assert resp.status_code == 200
            data = resp.json()
            assert "nodes" in data
            assert len(data["nodes"]) > 0


# ---------------------------------------------------------------------------
# 5. Trusted Document Clearance Propagation
# ---------------------------------------------------------------------------

class TestTrustedDocumentClearancePropagation:
    @pytest.fixture
    def sample_doc_and_chunks(self):
        from backend.rag.ingest import Chunk, Document
        doc = Document(
            document_id="doc_clearance_test_123",
            filename="turbine_inspection.pdf",
            file_type="pdf",
            text="P-204 experienced cavitation pitting erosion on the first stage impeller and requires dynamic balancing.",
            metadata={},
        )
        chunks = [
            Chunk(
                chunk_id="chunk_clearance_0",
                document_id="doc_clearance_test_123",
                filename="turbine_inspection.pdf",
                file_type="pdf",
                text="P-204 experienced cavitation pitting erosion on the first stage impeller and requires dynamic balancing.",
                chunk_index=0,
                metadata={"page": 1},
            )
        ]
        return doc, chunks

    def test_viewer_upload_cannot_create_elevated_records(self, sample_doc_and_chunks):
        from backend.graph.extractor import IndustrialGraphExtractor
        extractor = IndustrialGraphExtractor()
        doc, chunks = sample_doc_and_chunks

        # Extracted with viewer clearance
        nodes, edges = extractor.extract_document_graph(
            document=doc,
            chunks=chunks,
            document_clearance="viewer",
        )

        assert len(nodes) > 0
        assert len(edges) > 0

        # All nodes must have clearance == "viewer" (never elevated to operator or admin)
        for node in nodes:
            assert node.clearance == "viewer", f"Node {node.id} has elevated clearance {node.clearance}"
            for p in node.provenance:
                assert p.clearance == "viewer"

        # All edges must have clearance == "viewer"
        for edge in edges:
            assert edge.clearance == "viewer", f"Edge {edge.id} has elevated clearance {edge.clearance}"
            for p in edge.provenance:
                assert p.clearance == "viewer"

    def test_operator_document_clearance_propagated_correctly(self, sample_doc_and_chunks):
        from backend.graph.extractor import IndustrialGraphExtractor
        extractor = IndustrialGraphExtractor()
        doc, chunks = sample_doc_and_chunks

        nodes, edges = extractor.extract_document_graph(
            document=doc,
            chunks=chunks,
            document_clearance="operator",
        )

        defects = [n for n in nodes if n.category == "defect"]
        actions = [n for n in nodes if n.category == "action"]
        assert len(defects) > 0
        assert len(actions) > 0

        # Defects and actions must have operator clearance
        for d in defects:
            assert d.clearance == "operator"
        for a in actions:
            assert a.clearance == "operator"

        # Provenance must reflect operator clearance
        for n in nodes:
            for p in n.provenance:
                assert p.clearance == "operator"

    def test_admin_document_clearance_propagated_correctly(self, sample_doc_and_chunks):
        from backend.graph.extractor import IndustrialGraphExtractor
        extractor = IndustrialGraphExtractor()
        doc, chunks = sample_doc_and_chunks

        nodes, edges = extractor.extract_document_graph(
            document=doc,
            chunks=chunks,
            document_clearance="admin",
        )

        doc_node = next(n for n in nodes if n.category == "document")
        assert doc_node.clearance == "admin"

        defects = [n for n in nodes if n.category == "defect"]
        actions = [n for n in nodes if n.category == "action"]
        assert len(defects) > 0
        assert len(actions) > 0

        # Admin document findings and actions must have admin clearance
        for d in defects:
            assert d.clearance == "admin"
        for a in actions:
            assert a.clearance == "admin"

        # Provenance must be admin
        for n in nodes:
            for p in n.provenance:
                assert p.clearance == "admin"
        for e in edges:
            for p in e.provenance:
                assert p.clearance == "admin"

    def test_client_provided_clearance_cannot_elevate_privileges_during_upload(self, test_dirs):
        cfg = Settings(
            app_env="production",
            auth_enabled=True,
            auth_cookie_secure=False,
            tasks_dir=test_dirs["tasks"],
            tasks_db_path=test_dirs["db"],
            sandbox_dir=test_dirs["sandbox"],
            upload_dir=test_dirs["uploads"],
            chroma_persist_dir=test_dirs["chromadb"],
            code_exec_isolation="docker",
        )
        with patch("backend.tools.code_execution.check_docker_daemon_available", return_value=True), \
             patch("backend.tools.code_execution.check_docker_image_available", return_value=True):
            app = create_app(cfg)
            # Authenticate as operator
            operator_token = create_user_with_token(test_dirs["db"], "operator_uploader", UserRole.OPERATOR.value)

            with TestClient(app) as client:
                # Operator attempts to pass query param ?clearance=admin or header X-Clearance: admin
                fake_file = ("report.txt", b"P-204 cavitation pitting erosion on impeller.", "text/plain")
                resp = client.post(
                    "/api/documents?clearance=admin",
                    headers={
                        "Authorization": f"Bearer {operator_token}",
                        "X-Clearance": "admin",
                    },
                    files={"file": fake_file},
                )
                assert resp.status_code == 200
                doc_id = resp.json()["document_id"]

                # Query knowledge graph as operator
                graph_resp = client.get(
                    "/api/knowledge-graph",
                    headers={"Authorization": f"Bearer {operator_token}"},
                )
                assert graph_resp.status_code == 200
                data = graph_resp.json()

                # Ensure document node has operator clearance, NOT admin
                doc_node = next((n for n in data["nodes"] if n["id"] == f"DOC_{doc_id[:8]}"), None)
                assert doc_node is not None
                assert doc_node["clearance"] == "operator"

    def test_provenance_clearance_is_consistent_with_trusted_source(self, sample_doc_and_chunks):
        from backend.graph.extractor import IndustrialGraphExtractor
        extractor = IndustrialGraphExtractor()
        doc, chunks = sample_doc_and_chunks

        for test_clearance in ("viewer", "operator", "admin"):
            nodes, edges = extractor.extract_document_graph(
                document=doc,
                chunks=chunks,
                document_clearance=test_clearance,
            )
            for n in nodes:
                for p in n.provenance:
                    assert p.clearance == test_clearance
            for e in edges:
                for p in e.provenance:
                    assert p.clearance == test_clearance

    def test_missing_clearance_defaults_to_viewer(self, sample_doc_and_chunks):
        from backend.graph.extractor import IndustrialGraphExtractor
        extractor = IndustrialGraphExtractor()
        doc, chunks = sample_doc_and_chunks

        # Call with no clearance specified
        nodes, edges = extractor.extract_document_graph(document=doc, chunks=chunks)
        for n in nodes:
            assert n.clearance == "viewer"
            for p in n.provenance:
                assert p.clearance == "viewer"
        for e in edges:
            assert e.clearance == "viewer"
            for p in e.provenance:
                assert p.clearance == "viewer"

    def test_invalid_clearance_defaults_to_viewer(self, sample_doc_and_chunks):
        from backend.graph.extractor import IndustrialGraphExtractor
        extractor = IndustrialGraphExtractor()
        doc, chunks = sample_doc_and_chunks

        # Call with arbitrary invalid clearance strings
        for bad_clearance in ("MALICIOUS_ADMIN", "untrusted", "root", "", None):
            nodes, edges = extractor.extract_document_graph(
                document=doc,
                chunks=chunks,
                document_clearance=bad_clearance,
            )
            for n in nodes:
                assert n.clearance == "viewer", f"Node {n.id} elevated on bad_clearance={bad_clearance}"
                for p in n.provenance:
                    assert p.clearance == "viewer"
            for e in edges:
                assert e.clearance == "viewer", f"Edge {e.id} elevated on bad_clearance={bad_clearance}"
                for p in e.provenance:
                    assert p.clearance == "viewer"

    @pytest.mark.asyncio
    async def test_internal_caller_cannot_elevate_clearance_using_arbitrary_input(self, test_dirs):
        from unittest.mock import AsyncMock, MagicMock
        from backend.rag.service import DocumentService
        from backend.graph.service import KnowledgeGraphService
        from backend.graph.store import KnowledgeGraphStore

        kg_db = test_dirs["tasks"] / "kg_internal_test.db"
        kg_store = KnowledgeGraphStore(db_path=kg_db)
        kg_service = KnowledgeGraphService(store=kg_store)

        settings = Settings(
            tasks_dir=test_dirs["tasks"],
            tasks_db_path=test_dirs["db"],
            sandbox_dir=test_dirs["sandbox"],
            upload_dir=test_dirs["uploads"],
            chroma_persist_dir=test_dirs["chromadb"],
        )
        embedder = AsyncMock()
        embedder.embed_many = AsyncMock(return_value=[[0.1] * 768])
        vstore = MagicMock()
        retriever = MagicMock()
        doc_service = DocumentService(
            settings=settings,
            embedding_service=embedder,
            vector_store=vstore,
            retriever=retriever,
        )
        doc_service.set_graph_service(kg_service)

        # Internal caller attempts to pass arbitrary invalid clearance
        res = await doc_service.ingest_document(
            "p204_test.txt",
            b"P-204 experienced cavitation pitting erosion on the first stage impeller and requires dynamic balancing.",
            clearance="arbitrary_unauthorized_input",
        )
        assert res is not None

        # Verify graph nodes defaulted to viewer
        nodes = kg_store.get_all_nodes()
        doc_node = next(n for n in nodes if n.id == f"DOC_{res.document_id[:8]}")
        assert doc_node.clearance == "viewer"

    @pytest.mark.asyncio
    async def test_document_metadata_cannot_override_verified_clearance(self, test_dirs):
        from unittest.mock import AsyncMock, MagicMock
        from backend.rag.service import DocumentService
        from backend.graph.service import KnowledgeGraphService
        from backend.graph.store import KnowledgeGraphStore

        kg_db = test_dirs["tasks"] / "kg_meta_test.db"
        kg_store = KnowledgeGraphStore(db_path=kg_db)
        kg_service = KnowledgeGraphService(store=kg_store)

        settings = Settings(
            tasks_dir=test_dirs["tasks"],
            tasks_db_path=test_dirs["db"],
            sandbox_dir=test_dirs["sandbox"],
            upload_dir=test_dirs["uploads"],
            chroma_persist_dir=test_dirs["chromadb"],
        )
        embedder = AsyncMock()
        embedder.embed_many = AsyncMock(return_value=[[0.1] * 768])
        vstore = MagicMock()
        retriever = MagicMock()
        doc_service = DocumentService(
            settings=settings,
            embedding_service=embedder,
            vector_store=vstore,
            retriever=retriever,
        )
        doc_service.set_graph_service(kg_service)

        # Ingest document with verified clearance="viewer"
        res = await doc_service.ingest_document(
            "report_spoofed.txt",
            b"--- \nclearance: admin\nclassification: TOP_SECRET\n---\nP-204 cavitation on impeller.",
            clearance="viewer",
        )

        nodes = kg_store.get_all_nodes()
        doc_node = next(n for n in nodes if n.id == f"DOC_{res.document_id[:8]}")
        # Verified clearance must remain viewer despite document text trying to declare admin
        assert doc_node.clearance == "viewer"



# ---------------------------------------------------------------------------
# 6. Protect Static Graph Topology
# ---------------------------------------------------------------------------

class TestProtectStaticGraphTopology:
    @pytest.fixture
    def store(self, test_dirs):
        from backend.graph.store import KnowledgeGraphStore
        db_path = test_dirs["tasks"] / "kg_test.db"
        store = KnowledgeGraphStore(db_path=db_path)
        store.seed_static_topology()
        return store

    def test_extracting_p204_cannot_overwrite_static_node(self, store):
        from backend.graph.seed_data import STATIC_NODES
        from backend.graph.schemas import GraphNode, ProvenanceRecord, ProvenanceType

        # Baseline static EQ_P101A data
        orig_p101a = next(n for n in STATIC_NODES if n["id"] == "EQ_P101A")
        node_before = store.get_node("EQ_P101A")
        assert node_before is not None
        assert node_before.is_static is True
        assert node_before.label == orig_p101a["label"]
        assert node_before.description == orig_p101a["description"]
        assert node_before.properties == orig_p101a["properties"]
        assert node_before.clearance == orig_p101a["clearance"]

        # Attempt to upsert document-derived node with same ID ("EQ_P101A")
        doc_prov = ProvenanceRecord(
            id="prov_test_malicious_doc",
            target_type="node",
            target_id="EQ_P101A",
            provenance_type=ProvenanceType.DOCUMENT_DERIVED,
            clearance="viewer",
            document_id="doc_malicious_override",
            filename="override.txt",
            chunk_id="chunk_0",
            source_snippet="Overwriting pump description",
            extraction_method="rule_pattern",
        )
        fake_extracted_node = GraphNode(
            id="EQ_P101A",
            label="P-101A Overwritten Asset",
            category="equipment",
            clearance="operator",  # Attempt clearance escalation on static node
            description="ATTACK: Overwritten description from document extraction.",
            properties={"tag": "P-101A", "overwritten_key": "hacked"},
            is_static=False,
            provenance=[doc_prov],
        )

        store.upsert_node(fake_extracted_node, doc_prov)

        # Verify static node row was NOT overwritten
        node_after = store.get_node("EQ_P101A")
        assert node_after is not None
        assert node_after.is_static is True
        assert node_after.label == orig_p101a["label"]
        assert node_after.description == orig_p101a["description"]
        assert node_after.properties == orig_p101a["properties"]
        assert node_after.clearance == orig_p101a["clearance"]

    def test_extracting_existing_static_relationship_cannot_modify_static_edge(self, store):
        from backend.graph.seed_data import STATIC_EDGES
        from backend.graph.schemas import GraphEdge, ProvenanceRecord, ProvenanceType

        # Target static edge: UNIT_CDU01:OPERATES:EQ_P101A
        orig_edge = next(e for e in STATIC_EDGES if e["source"] == "UNIT_CDU01" and e["target"] == "EQ_P101A")
        eid = f"{orig_edge['source']}:{orig_edge['relationship']}:{orig_edge['target']}"

        edges_before = {e.id: e for e in store.get_all_edges()}
        assert eid in edges_before
        edge_before = edges_before[eid]
        assert edge_before.clearance == orig_edge["clearance"]
        assert edge_before.is_static is True

        # Attempt to upsert edge with same ID but different clearance and weight
        doc_prov = ProvenanceRecord(
            id="prov_edge_test_doc",
            target_type="edge",
            target_id=eid,
            provenance_type=ProvenanceType.DOCUMENT_DERIVED,
            clearance="admin",
            document_id="doc_edge_override",
            filename="edge_override.txt",
            chunk_id="chunk_0",
            source_snippet="Edge override test snippet",
            extraction_method="rule_pattern",
        )
        fake_edge = GraphEdge(
            id=eid,
            source=orig_edge["source"],
            target=orig_edge["target"],
            relationship=orig_edge["relationship"],
            clearance="admin",
            properties={"overwritten": True},
            is_static=False,
            is_inferred=True,
            weight=999.0,
            provenance=[doc_prov],
        )

        store.upsert_edge(fake_edge, doc_prov)

        # Verify static edge properties and clearance remain unchanged
        edges_after = {e.id: e for e in store.get_all_edges()}
        edge_after = edges_after[eid]
        assert edge_after.clearance == orig_edge["clearance"]
        assert edge_after.is_static is True
        assert edge_after.weight == 1.0
        assert edge_after.is_inferred is False

    def test_document_derived_provenance_stored_correctly_on_static_node(self, store):
        from backend.graph.schemas import GraphNode, ProvenanceRecord, ProvenanceType

        doc_prov = ProvenanceRecord(
            id="prov_doc_legit_evidence",
            target_type="node",
            target_id="EQ_P101A",
            provenance_type=ProvenanceType.DOCUMENT_DERIVED,
            clearance="operator",
            document_id="doc_legit_123",
            filename="maintenance_log.pdf",
            chunk_id="chunk_42",
            source_snippet="P-101A impeller cavitation inspection on 2026-03-15",
            extraction_method="rule_pattern",
        )
        node_update = GraphNode(
            id="EQ_P101A",
            label="P-101A",
            category="equipment",
            clearance="viewer",
            is_static=False,
        )

        store.upsert_node(node_update, doc_prov)

        # Verify node still has canonical properties
        node = store.get_node("EQ_P101A")
        assert node is not None
        assert node.is_static is True

        # Verify provenance has BOTH static origin and document-derived origin
        prov_types = {p.provenance_type for p in node.provenance}
        assert ProvenanceType.STATIC_TOPOLOGY in prov_types
        assert ProvenanceType.DOCUMENT_DERIVED in prov_types

        doc_p = next(p for p in node.provenance if p.provenance_type == ProvenanceType.DOCUMENT_DERIVED)
        assert doc_p.document_id == "doc_legit_123"
        assert doc_p.source_snippet == "P-101A impeller cavitation inspection on 2026-03-15"

    def test_deleting_source_document_preserves_static_topology(self, store):
        from backend.graph.seed_data import STATIC_EDGES, STATIC_NODES
        from backend.graph.schemas import GraphNode, ProvenanceRecord, ProvenanceType

        # Attach provenance to static node EQ_P101A
        doc_prov = ProvenanceRecord(
            id="prov_doc_to_delete",
            target_type="node",
            target_id="EQ_P101A",
            provenance_type=ProvenanceType.DOCUMENT_DERIVED,
            clearance="operator",
            document_id="doc_will_be_deleted",
            filename="temp.pdf",
            chunk_id="c0",
            source_snippet="temporary snippet",
            extraction_method="rule_pattern",
        )
        node_update = GraphNode(
            id="EQ_P101A",
            label="P-101A",
            category="equipment",
            clearance="viewer",
            is_static=False,
        )
        store.upsert_node(node_update, doc_prov)

        # Delete document
        store.delete_document_records("doc_will_be_deleted")

        # Static node must still exist with STATIC_TOPOLOGY provenance
        node = store.get_node("EQ_P101A")
        assert node is not None
        assert node.is_static is True
        assert len(node.provenance) == 1
        assert node.provenance[0].provenance_type == ProvenanceType.STATIC_TOPOLOGY

        # All static nodes and edges must remain intact
        assert len(store.get_all_nodes()) == len(STATIC_NODES)
        assert len(store.get_all_edges()) == len(STATIC_EDGES)

    def test_existing_database_migration_adds_is_static_to_edges(self, test_dirs):
        import sqlite3
        from backend.graph.store import KnowledgeGraphStore

        legacy_db = test_dirs["tasks"] / "legacy_kg_migration.db"
        # Create legacy schema without is_static on kg_edges
        with sqlite3.connect(str(legacy_db)) as conn:
            conn.executescript("""
                CREATE TABLE kg_nodes (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    category TEXT NOT NULL,
                    clearance TEXT NOT NULL DEFAULT 'viewer',
                    description TEXT,
                    properties_json TEXT,
                    is_static BOOLEAN NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE kg_edges (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    clearance TEXT NOT NULL DEFAULT 'viewer',
                    properties_json TEXT,
                    is_inferred BOOLEAN NOT NULL DEFAULT 0,
                    weight REAL NOT NULL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(source_id) REFERENCES kg_nodes(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_id) REFERENCES kg_nodes(id) ON DELETE CASCADE
                );
                CREATE TABLE kg_provenance (
                    id TEXT PRIMARY KEY,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    provenance_type TEXT NOT NULL,
                    clearance TEXT NOT NULL DEFAULT 'viewer',
                    document_id TEXT,
                    filename TEXT,
                    chunk_id TEXT,
                    page_number INTEGER,
                    section_header TEXT,
                    source_snippet TEXT,
                    source_label TEXT,
                    extraction_method TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO kg_nodes (id, label, category, clearance) VALUES ('N1', 'Node 1', 'unit', 'viewer');
                INSERT INTO kg_nodes (id, label, category, clearance) VALUES ('N2', 'Node 2', 'unit', 'viewer');
                INSERT INTO kg_edges (id, source_id, target_id, relationship, clearance) VALUES ('N1:FEEDS:N2', 'N1', 'N2', 'FEEDS', 'viewer');
            """)

        # Verify is_static column does not exist prior to KnowledgeGraphStore initialization
        with sqlite3.connect(str(legacy_db)) as conn:
            cols = [col[1] for col in conn.execute("PRAGMA table_info(kg_edges)").fetchall()]
            assert "is_static" not in cols

        # Initialize KnowledgeGraphStore on legacy DB to trigger automatic migration
        store = KnowledgeGraphStore(db_path=legacy_db)

        # Verify is_static column was added and existing edge preserved with default False
        with sqlite3.connect(str(legacy_db)) as conn:
            cols = [col[1] for col in conn.execute("PRAGMA table_info(kg_edges)").fetchall()]
            assert "is_static" in cols

        edges = store.get_all_edges()
        assert len(edges) == 1
        assert edges[0].id == "N1:FEEDS:N2"
        assert edges[0].is_static is False

        # Verify idempotence on second initialization
        store_second = KnowledgeGraphStore(db_path=legacy_db)
        edges_second = store_second.get_all_edges()
        assert len(edges_second) == 1

    def test_unexpected_sqlite_operational_error_is_reraised(self, test_dirs, monkeypatch):
        import sqlite3
        from backend.graph.store import KnowledgeGraphStore

        bad_db = test_dirs["tasks"] / "kg_error_test.db"

        # Create legacy table lacking is_static column
        with sqlite3.connect(str(bad_db)) as conn:
            conn.executescript("""
                CREATE TABLE kg_nodes (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    category TEXT NOT NULL,
                    clearance TEXT NOT NULL DEFAULT 'viewer'
                );
                CREATE TABLE kg_edges (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    clearance TEXT NOT NULL,
                    properties_json TEXT NOT NULL DEFAULT '{}'
                );
            """)

        real_connect = sqlite3.connect

        class BrokenConn:
            def __init__(self, target):
                self._conn = target

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self._conn.close()

            def __getattr__(self, name):
                return getattr(self._conn, name)

            def __setattr__(self, name, value):
                if name == "_conn":
                    super().__setattr__(name, value)
                else:
                    setattr(self._conn, name, value)

            def execute(self, sql, *args, **kwargs):
                if "ALTER TABLE kg_edges ADD COLUMN is_static" in str(sql):
                    raise sqlite3.OperationalError("database disk image is malformed")
                return self._conn.execute(sql, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: BrokenConn(real_connect(*args, **kwargs)))

        with pytest.raises(sqlite3.OperationalError, match="database disk image is malformed"):
            KnowledgeGraphStore(db_path=bad_db)

    def test_static_node_replaces_a_conflicting_non_static_node(self, test_dirs):
        from backend.graph.seed_data import STATIC_NODES
        from backend.graph.store import KnowledgeGraphStore
        from backend.graph.schemas import GraphNode, ProvenanceRecord, ProvenanceType

        db_path = test_dirs["tasks"] / "kg_conflict_test.db"
        store = KnowledgeGraphStore(db_path=db_path)

        # Pre-seed non-static matching node "EQ_P101A" (as if from early document extraction)
        fake_doc_prov = ProvenanceRecord(
            id="prov_early_doc",
            target_type="node",
            target_id="EQ_P101A",
            provenance_type=ProvenanceType.DOCUMENT_DERIVED,
            clearance="viewer",
            document_id="doc_early",
            filename="early.txt",
            chunk_id="c0",
            source_snippet="Early extracted snippet",
            extraction_method="rule_pattern",
        )
        fake_extracted_node = GraphNode(
            id="EQ_P101A",
            label="Old Draft Pump",
            category="equipment",
            clearance="operator",
            description="Draft non-canonical description",
            properties={"tag": "P-101A", "draft": True},
            is_static=False,
            provenance=[fake_doc_prov],
        )
        store.upsert_node(fake_extracted_node, fake_doc_prov)

        # Verify it starts as non-static
        before = store.get_node("EQ_P101A")
        assert before.is_static is False
        assert before.label == "Old Draft Pump"

        # Now run seed_static_topology()
        store.seed_static_topology()

        # Verify canonical static topology overrode the non-static conflict and asserted authority
        canonical_p101a = next(n for n in STATIC_NODES if n["id"] == "EQ_P101A")
        after = store.get_node("EQ_P101A")
        assert after.is_static is True
        assert after.label == canonical_p101a["label"]
        assert after.clearance == canonical_p101a["clearance"]
        assert after.description == canonical_p101a["description"]
        assert after.properties == canonical_p101a["properties"]

    def test_static_edge_restores_canonical_attributes(self, test_dirs):
        from backend.graph.seed_data import STATIC_EDGES
        from backend.graph.store import KnowledgeGraphStore
        from backend.graph.schemas import GraphNode, GraphEdge, ProvenanceRecord, ProvenanceType

        db_path = test_dirs["tasks"] / "kg_edge_conflict_test.db"
        store = KnowledgeGraphStore(db_path=db_path)

        # Target static edge: UNIT_CDU01:OPERATES:EQ_P101A
        orig_edge = next(e for e in STATIC_EDGES if e["source"] == "UNIT_CDU01" and e["target"] == "EQ_P101A")
        eid = f"{orig_edge['source']}:{orig_edge['relationship']}:{orig_edge['target']}"

        # Insert endpoints first so foreign key constraints pass
        store.upsert_node(GraphNode(id=orig_edge["source"], label="Unit CDU01", category="unit", clearance="viewer"))
        store.upsert_node(GraphNode(id=orig_edge["target"], label="Pump P-101A", category="equipment", clearance="viewer"))

        # Insert non-canonical pre-existing edge
        fake_prov = ProvenanceRecord(
            id="prov_edge_fake",
            target_type="edge",
            target_id=eid,
            provenance_type=ProvenanceType.DOCUMENT_DERIVED,
            clearance="operator",
            document_id="doc_fake",
            filename="fake.txt",
            chunk_id="c0",
            source_snippet="fake snippet",
            extraction_method="rule_pattern",
        )
        fake_edge = GraphEdge(
            id=eid,
            source=orig_edge["source"],
            target=orig_edge["target"],
            relationship=orig_edge["relationship"],
            clearance="operator",
            properties={"corrupted_field": "draft"},
            is_static=False,
            is_inferred=True,
            weight=999.0,
            provenance=[fake_prov],
        )
        store.upsert_edge(fake_edge, fake_prov)

        # Seed static topology
        store.seed_static_topology()

        # Verify edge attributes were restored to canonical baseline
        edges_after = {e.id: e for e in store.get_all_edges()}
        edge_after = edges_after[eid]
        assert edge_after.is_static is True
        assert edge_after.is_inferred is False
        assert edge_after.weight == 1.0
        assert edge_after.clearance == orig_edge["clearance"]
        assert edge_after.properties == {}


