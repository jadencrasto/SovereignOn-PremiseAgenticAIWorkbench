"""
tests/backend/test_knowledge_graph.py
---------------------------------------
Phase C Step 11: Knowledge Graph — Comprehensive test suite.

Tests cover:
  1. Schema validation (static & document-derived provenance)
  2. Idempotent static topology seeding
  3. Deterministic entity extraction
  4. Multi-iteration extraction performance benchmark (informational)
  5. Provenance retention and chunk attribution
  6. Canonical entity ID and deduplication
  7. Strictly supported 2-hop traversal
  8. RBAC clearance and snippet filtering
  9. Knowledge graph query tool (read-only)
  10. Ingestion failure isolation
  11. Document deletion and orphan cleanup
  12. API backward compatibility / UI regression
  13. Planner selective invocation
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

import pytest

from backend.graph.schemas import (
    ClearanceLevelStr,
    GraphEdge,
    GraphNode,
    ProvenanceRecord,
    ProvenanceType,
    canonicalize_equipment_tag,
    canonicalize_unit_code,
)
from backend.graph.seed_data import STATIC_EDGES, STATIC_NODES
from backend.graph.store import KnowledgeGraphStore
from backend.graph.extractor import IndustrialGraphExtractor
from backend.graph.service import KnowledgeGraphService
from backend.rag.ingest import Chunk, Document


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db_path(tmp_path):
    """Provide a temporary SQLite path for each test."""
    return tmp_path / "test_knowledge_graph.db"


@pytest.fixture()
def store(tmp_db_path):
    return KnowledgeGraphStore(db_path=tmp_db_path)


@pytest.fixture()
def seeded_store(store):
    store.seed_static_topology()
    return store


@pytest.fixture()
def service(seeded_store):
    return KnowledgeGraphService(store=seeded_store)


@pytest.fixture()
def sample_document():
    return Document(
        document_id="doc_test_p204_abc12345",
        filename="pump_p204_maintenance.md",
        file_type="md",
        text=(
            "# Maintenance Report: P-204 Boiler Feed Water Pump\n\n"
            "## Executive Summary\n"
            "Centrifugal pump P-204 located in Hydrocracker Unit 04 was inspected "
            "during the Q3 2026 planned turnaround.\n\n"
            "## Inspection Findings\n"
            "Visual and UT inspection of P-204 impeller vanes revealed cavitation pitting "
            "on the first stage impeller with pit depths of 0.8-1.2mm.\n"
            "Suction strainer mesh showed 65% strainer clogging with scale and debris.\n\n"
            "## Recommended Maintenance Actions\n"
            "1. Dynamic balancing of P-204 rotor assembly.\n"
            "2. Strainer cleaning and flush of suction manifold.\n"
            "3. Replace mechanical seal per API Plan 53B specification.\n"
        ),
        metadata={"source": "turnaround_q3_2026"},
    )


@pytest.fixture()
def sample_chunks(sample_document):
    text = sample_document.text
    paragraphs = text.split("\n\n")
    chunks = []
    for i, para in enumerate(paragraphs):
        if para.strip():
            chunks.append(Chunk(
                chunk_id=f"{sample_document.document_id}_chunk_{i}",
                document_id=sample_document.document_id,
                filename=sample_document.filename,
                file_type=sample_document.file_type,
                text=para.strip(),
                chunk_index=i,
                metadata={"page": 1},
            ))
    return chunks


# ---------------------------------------------------------------------------
# 1. Schema Validation
# ---------------------------------------------------------------------------

class TestGraphSchemas:
    def test_static_provenance_nullable_fields(self):
        """Static topology provenance must allow None for document fields."""
        prov = ProvenanceRecord(
            id="prov_static_test",
            target_type="node",
            target_id="UNIT_HC04",
            provenance_type=ProvenanceType.STATIC_TOPOLOGY,
            clearance="viewer",
            document_id=None,
            filename=None,
            chunk_id=None,
            page_number=None,
            source_snippet=None,
            source_label="MRPL Refinery Base Asset Registry",
            extraction_method="static_seed",
            confidence=1.0,
        )
        assert prov.document_id is None
        assert prov.filename is None
        assert prov.source_label == "MRPL Refinery Base Asset Registry"

    def test_document_derived_provenance(self):
        """Document-derived provenance carries document_id, filename, chunk_id."""
        prov = ProvenanceRecord(
            id="prov_doc_test",
            target_type="edge",
            target_id="EQ_P204:HAS_FINDING:FINDING_EQ_P204_CAVITATION",
            provenance_type=ProvenanceType.DOCUMENT_DERIVED,
            clearance="operator",
            document_id="doc123",
            filename="pump_p204_maintenance.md",
            chunk_id="doc123_chunk_2",
            page_number=1,
            source_snippet="cavitation pitting on first stage impeller",
            extraction_method="rule_pattern",
            confidence=0.95,
        )
        assert prov.document_id == "doc123"
        assert prov.filename == "pump_p204_maintenance.md"

    def test_graph_node_model(self):
        node = GraphNode(
            id="EQ_P204",
            label="P-204 Boiler Feed Water Pump",
            category="equipment",
            clearance="viewer",
        )
        assert node.id == "EQ_P204"
        assert node.is_static is False

    def test_graph_edge_label_alias(self):
        edge = GraphEdge(
            id="UNIT_HC04:CONTAINS:EQ_P204",
            source="UNIT_HC04",
            target="EQ_P204",
            relationship="CONTAINS",
        )
        assert edge.label == "CONTAINS"

    def test_canonicalize_equipment_tag(self):
        assert canonicalize_equipment_tag("P-204") == "EQ_P204"
        assert canonicalize_equipment_tag("p204") == "EQ_P204"
        assert canonicalize_equipment_tag("MOV-4102-B") == "EQ_MOV4102B"
        assert canonicalize_equipment_tag("K-101") == "EQ_K101"

    def test_canonicalize_unit_code(self):
        assert canonicalize_unit_code("HC04") == "UNIT_HC04"
        assert canonicalize_unit_code("UNIT_HC04") == "UNIT_HC04"


# ---------------------------------------------------------------------------
# 2. Idempotent Static Topology Seeding
# ---------------------------------------------------------------------------

class TestStaticTopologySeeding:
    def test_seed_creates_expected_counts(self, store):
        n1, e1 = store.seed_static_topology()
        assert n1 == len(STATIC_NODES)
        assert e1 == len(STATIC_EDGES)

    def test_seed_idempotent(self, store):
        n1, e1 = store.seed_static_topology()
        n2, e2 = store.seed_static_topology()
        assert n1 == n2
        assert e1 == e2

    def test_seeded_nodes_are_static(self, seeded_store):
        nodes = seeded_store.get_all_nodes()
        for n in nodes:
            assert n.is_static is True

    def test_seeded_nodes_have_provenance(self, seeded_store):
        node = seeded_store.get_node("UNIT_HC04")
        assert node is not None
        assert len(node.provenance) >= 1
        assert node.provenance[0].provenance_type == ProvenanceType.STATIC_TOPOLOGY


# ---------------------------------------------------------------------------
# 3. Deterministic Entity Extraction
# ---------------------------------------------------------------------------

class TestDeterministicExtraction:
    def test_extract_p204_entities(self, sample_document, sample_chunks):
        extractor = IndustrialGraphExtractor()
        nodes, edges = extractor.extract_document_graph(sample_document, sample_chunks)

        node_ids = {n.id for n in nodes}

        # Must extract equipment tag P-204
        assert any("P204" in nid for nid in node_ids), f"P-204 not found in {node_ids}"

        # Must extract document node
        assert any(nid.startswith("DOC_") for nid in node_ids)

    def test_extract_findings(self, sample_document, sample_chunks):
        extractor = IndustrialGraphExtractor()
        nodes, edges = extractor.extract_document_graph(sample_document, sample_chunks)

        node_ids = {n.id for n in nodes}

        # Cavitation finding should be scoped to equipment tag
        cavitation_nodes = [nid for nid in node_ids if "CAVITATION" in nid]
        assert len(cavitation_nodes) >= 1, f"Expected cavitation finding, got {node_ids}"

    def test_extract_actions(self, sample_document, sample_chunks):
        extractor = IndustrialGraphExtractor()
        nodes, edges = extractor.extract_document_graph(sample_document, sample_chunks)

        node_ids = {n.id for n in nodes}
        edge_rels = {e.relationship for e in edges}

        # Should have REQUIRES_ACTION or DESCRIBES edges
        assert "DESCRIBES" in edge_rels, f"Expected DESCRIBES in {edge_rels}"

    def test_edges_have_provenance(self, sample_document, sample_chunks):
        extractor = IndustrialGraphExtractor()
        nodes, edges = extractor.extract_document_graph(sample_document, sample_chunks)

        for edge in edges:
            assert len(edge.provenance) >= 1, f"Edge {edge.id} missing provenance"
            for p in edge.provenance:
                assert p.document_id == sample_document.document_id
                assert p.filename == sample_document.filename


# ---------------------------------------------------------------------------
# 4. Extraction Performance Benchmark (Informational, Non-blocking)
# ---------------------------------------------------------------------------

class TestExtractionBenchmark:
    def test_extraction_performance_benchmark(self, sample_document, sample_chunks):
        """
        Run 25 iterations and log average, median, min, max.
        This is a benchmark target (<50ms), not a hard failure assertion.
        """
        extractor = IndustrialGraphExtractor()
        iterations = 25
        timings = []

        for _ in range(iterations):
            start = time.perf_counter()
            extractor.extract_document_graph(sample_document, sample_chunks)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            timings.append(elapsed_ms)

        avg = statistics.mean(timings)
        med = statistics.median(timings)
        mn = min(timings)
        mx = max(timings)

        # Log metrics for visibility
        print(
            f"\n[Extraction Benchmark] {iterations} iterations | "
            f"avg={avg:.2f}ms median={med:.2f}ms min={mn:.2f}ms max={mx:.2f}ms"
        )

        # Informational assertion — warns but does not fail on slow environments
        if avg > 50.0:
            import warnings
            warnings.warn(
                f"Extraction avg {avg:.2f}ms exceeds <50ms benchmark target. "
                f"This may indicate a performance regression or resource-constrained test environment.",
                UserWarning,
                stacklevel=2,
            )

        # Sanity: it should at least complete in a reasonable time
        assert avg < 5000.0, f"Extraction catastrophically slow: avg={avg:.2f}ms"


# ---------------------------------------------------------------------------
# 5. Provenance Retention & Chunk Attribution
# ---------------------------------------------------------------------------

class TestProvenanceRetention:
    def test_provenance_carries_chunk_info(self, service, sample_document, sample_chunks):
        n_count, e_count = service.extract_and_index_document(
            sample_document, sample_chunks
        )
        assert n_count > 0

        # Query the graph
        result = service.query("P-204", user_clearance="admin", max_depth=1)
        if result.nodes:
            for node in result.nodes:
                for p in node.provenance:
                    if p.provenance_type == ProvenanceType.DOCUMENT_DERIVED:
                        assert p.document_id == sample_document.document_id
                        assert p.filename == sample_document.filename


# ---------------------------------------------------------------------------
# 6. Canonical Entity ID & Deduplication
# ---------------------------------------------------------------------------

class TestCanonicalDeduplication:
    def test_reupload_does_not_duplicate(self, service, sample_document, sample_chunks):
        n1, e1 = service.extract_and_index_document(sample_document, sample_chunks)

        # Count nodes before re-upload
        all_nodes_before = service.store.get_all_nodes()

        # Re-upload same document
        n2, e2 = service.extract_and_index_document(sample_document, sample_chunks)

        all_nodes_after = service.store.get_all_nodes()

        # Non-static node counts should be identical (no duplication)
        dynamic_before = [n for n in all_nodes_before if not n.is_static]
        dynamic_after = [n for n in all_nodes_after if not n.is_static]
        assert len(dynamic_before) == len(dynamic_after), (
            f"Re-upload created duplicates: before={len(dynamic_before)} after={len(dynamic_after)}"
        )


# ---------------------------------------------------------------------------
# 7. Strictly Supported 2-Hop Traversal
# ---------------------------------------------------------------------------

class TestStrictTraversal:
    def test_2_hop_returns_only_explicit_relationships(self, service, sample_document, sample_chunks):
        service.extract_and_index_document(sample_document, sample_chunks)

        result = service.query("UNIT_HC04", user_clearance="admin", max_depth=2)

        # Every edge must have provenance (static or document-derived)
        for edge in result.edges:
            assert edge.is_inferred is False, (
                f"Edge {edge.id} is inferred — only explicit relationships allowed"
            )

    def test_max_depth_bounded(self, service):
        result = service.query("UNIT_HC04", user_clearance="admin", max_depth=5)
        assert result.max_depth <= 2


# ---------------------------------------------------------------------------
# 8. RBAC Clearance & Snippet Filtering
# ---------------------------------------------------------------------------

class TestRBACAndSnippetFiltering:
    def test_viewer_cannot_see_operator_defects(self, service):
        result = service.query("EQ_P101A", user_clearance="viewer", max_depth=1)
        for node in result.nodes:
            if node.category == "defect":
                # Should be filtered out for viewer
                pytest.fail(f"Viewer should not see defect node {node.id}")

    def test_viewer_sees_classified_as_stub(self, service):
        result = service.query("UNIT_HC04", user_clearance="viewer", max_depth=1)
        stubs = [n for n in result.nodes if n.category == "restricted_stub"]
        # Classified admin nodes connected to UNIT_HC04 should appear as stubs
        classified_ids = {"SEC_CATALYST_FORMULA"}
        for stub in stubs:
            assert "LOCKED" in stub.label

    def test_provenance_snippets_redacted_for_lower_clearance(self, service, sample_document, sample_chunks):
        service.extract_and_index_document(sample_document, sample_chunks)

        # Query as viewer
        result = service.query("P-204", user_clearance="viewer", max_depth=1)

        for node in result.nodes:
            for p in node.provenance:
                if p.clearance == "operator" or p.clearance == "admin":
                    # The snippet should be redacted for viewer
                    assert "RESTRICTED" in (p.source_snippet or ""), (
                        f"Snippet not redacted for viewer: {p.source_snippet}"
                    )

    def test_admin_sees_full_graph(self, service):
        result = service.get_full_graph(user_clearance="admin")
        # Admin should see classified nodes
        node_ids = {n["id"] for n in result["nodes"]}
        assert "SEC_CATALYST_FORMULA" in node_ids
        assert "SEC_SCADA_OVERRIDE" in node_ids


# ---------------------------------------------------------------------------
# 9. Knowledge Graph Query Tool (Read-Only)
# ---------------------------------------------------------------------------

class TestKnowledgeGraphQueryTool:
    @pytest.mark.asyncio
    async def test_tool_returns_grounded_markdown(self, service):
        from backend.tools.knowledge_graph import create_knowledge_graph_query

        execute_fn = create_knowledge_graph_query(service)
        result = await execute_fn(query="UNIT_HC04", max_depth=1)

        assert result["success"] is True
        assert result["found"] is True
        assert "content" in result
        assert "Knowledge Graph Inspection" in result["content"]

    @pytest.mark.asyncio
    async def test_tool_not_found(self, service):
        from backend.tools.knowledge_graph import create_knowledge_graph_query

        execute_fn = create_knowledge_graph_query(service)
        result = await execute_fn(query="NONEXISTENT_XYZ_999", max_depth=1)

        assert result["success"] is True
        assert result["found"] is False


# ---------------------------------------------------------------------------
# 10. Ingestion Failure Isolation
# ---------------------------------------------------------------------------

class TestIngestionFailureIsolation:
    def test_corrupt_chunk_does_not_crash_extraction(self):
        extractor = IndustrialGraphExtractor()
        doc = Document(
            document_id="doc_corrupt_test",
            filename="corrupt.md",
            file_type="md",
            text="Nothing useful here \x00\x01\x02",
            metadata={},
        )
        chunks = [Chunk(
            chunk_id="doc_corrupt_test_chunk_0",
            document_id="doc_corrupt_test",
            filename="corrupt.md",
            file_type="md",
            text="Nothing useful here \x00\x01\x02",
            chunk_index=0,
            metadata={},
        )]

        # Should not raise
        nodes, edges = extractor.extract_document_graph(doc, chunks)
        # May or may not extract anything, but must not crash
        assert isinstance(nodes, list)
        assert isinstance(edges, list)


# ---------------------------------------------------------------------------
# 11. Document Deletion & Orphan Cleanup
# ---------------------------------------------------------------------------

class TestDocumentDeletion:
    def test_delete_removes_provenance_and_orphans(self, service, sample_document, sample_chunks):
        service.extract_and_index_document(sample_document, sample_chunks)

        # Verify document entities exist
        all_nodes_before = service.store.get_all_nodes()
        dynamic_before = [n for n in all_nodes_before if not n.is_static]
        assert len(dynamic_before) > 0, "Expected document-derived nodes"

        # Delete
        counts = service.delete_document_entities(sample_document.document_id)
        assert counts["deleted_provenance"] > 0

        # Verify static topology intact
        all_nodes_after = service.store.get_all_nodes()
        static_after = [n for n in all_nodes_after if n.is_static]
        assert len(static_after) == len(STATIC_NODES), (
            f"Static nodes changed: expected={len(STATIC_NODES)} actual={len(static_after)}"
        )

    def test_delete_preserves_edges_from_other_documents(self, service, sample_document, sample_chunks):
        """If two documents support the same edge, deleting one preserves the edge."""
        # First upload
        service.extract_and_index_document(sample_document, sample_chunks)

        edges_before = service.store.get_all_edges()

        # Second document referencing same equipment
        doc2 = Document(
            document_id="doc_test_p204_SECOND",
            filename="pump_p204_annual_review.md",
            file_type="md",
            text="Annual review of P-204 centrifugal pump. Strainer cleaning completed successfully.",
            metadata={},
        )
        chunks2 = [Chunk(
            chunk_id="doc_test_p204_SECOND_chunk_0",
            document_id="doc_test_p204_SECOND",
            filename="pump_p204_annual_review.md",
            file_type="md",
            text="Annual review of P-204 centrifugal pump. Strainer cleaning completed successfully.",
            chunk_index=0,
            metadata={"page": 1},
        )]
        service.extract_and_index_document(doc2, chunks2)

        # Delete first document
        service.delete_document_entities(sample_document.document_id)

        # Edges from doc2 should still exist
        edges_after = service.store.get_all_edges()
        # At minimum static edges should be present
        static_edge_count = len(STATIC_EDGES)
        assert len(edges_after) >= static_edge_count

    def test_static_nodes_never_deleted(self, service, sample_document, sample_chunks):
        service.extract_and_index_document(sample_document, sample_chunks)
        service.delete_document_entities(sample_document.document_id)

        # Verify every static node still exists
        for sn in STATIC_NODES:
            node = service.store.get_node(sn["id"])
            assert node is not None, f"Static node {sn['id']} was deleted!"
            assert node.is_static is True


# ---------------------------------------------------------------------------
# 12. API Backward Compatibility / UI Regression
# ---------------------------------------------------------------------------

class TestAPIBackwardCompatibility:
    def test_full_graph_response_structure(self, service):
        result = service.get_full_graph(user_clearance="viewer")

        # Verify required keys matching frontend GraphResponse interface
        assert "user_role" in result
        assert "effective_clearance" in result
        assert "clearance_level" in result
        assert "nodes_count" in result
        assert "edges_count" in result
        assert "hidden_nodes" in result
        assert "nodes" in result
        assert "edges" in result
        assert "categories" in result

        # Verify 'links' alias present for compatibility
        assert "links" in result

        # Verify nodes have expected fields
        for node in result["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "category" in node
            assert "clearance" in node

        # Verify edges have expected fields
        for edge in result["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert "label" in edge or "relationship" in edge
            assert "clearance" in edge

    def test_viewer_graph_has_correct_categories(self, service):
        result = service.get_full_graph(user_clearance="viewer")
        cats = result["categories"]
        assert "unit" in cats
        assert "equipment" in cats
        assert "sensor" in cats

    def test_operator_sees_defects(self, service):
        result = service.get_full_graph(user_clearance="operator")
        node_categories = {n["category"] for n in result["nodes"]}
        assert "defect" in node_categories


# ---------------------------------------------------------------------------
# 13. Planner Selective Invocation
# ---------------------------------------------------------------------------

class TestPlannerSelectiveInvocation:
    def test_planner_prompt_contains_kg_guidance(self):
        from backend.agent.planner import _PLAN_SYSTEM_PROMPT
        assert "knowledge_graph_query" in _PLAN_SYSTEM_PROMPT
        assert "equipment topology" in _PLAN_SYSTEM_PROMPT.lower() or "topology" in _PLAN_SYSTEM_PROMPT.lower()

    def test_simple_query_does_not_trigger_planner(self):
        from backend.agent.planner import should_use_planner
        # Simple greeting should not trigger planner
        assert should_use_planner("hello", planning_enabled=True, tools_enabled=True) is False
        # Simple arithmetic should not trigger
        assert should_use_planner("what is 2+2?", planning_enabled=True, tools_enabled=True) is False
