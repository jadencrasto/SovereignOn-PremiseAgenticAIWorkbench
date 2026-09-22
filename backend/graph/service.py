"""
backend/graph/service.py
------------------------
Knowledge Graph business logic service.
Coordinates SQLite storage, rule-based extraction, RBAC clearance filtering,
and safe provenance-aware queries.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.auth.models import ClearanceLevel, parse_clearance
from backend.graph.extractor import IndustrialGraphExtractor
from backend.graph.schemas import (
    GraphEdge,
    GraphNode,
    GraphQueryResult,
    ProvenanceRecord,
    ProvenanceType,
    canonicalize_equipment_tag,
    canonicalize_unit_code,
)
from backend.graph.store import KnowledgeGraphStore
from backend.rag.ingest import Chunk, Document

logger = logging.getLogger(__name__)

ROLE_HIERARCHY: Dict[str, int] = {
    "viewer": 1,
    "operator": 2,
    "admin": 3,
}


class KnowledgeGraphService:
    """
    High-level service managing the Sovereign Knowledge Graph.
    """

    def __init__(self, store: KnowledgeGraphStore) -> None:
        self.store = store
        self.extractor = IndustrialGraphExtractor()
        # Seed base topology on initialization
        self.store.seed_static_topology()

    def extract_and_index_document(
        self,
        document: Document,
        chunks: List[Chunk],
        clearance: str = "operator",
    ) -> Tuple[int, int]:
        """
        Extracts entities and relationships from document chunks and updates the graph.
        Runs idempotently: cleans any pre-existing records for this document_id first.
        """
        # Clean pre-existing entries for this document to handle re-uploads cleanly
        self.store.delete_document_records(document.document_id)

        nodes, edges = self.extractor.extract_document_graph(
            document=document,
            chunks=chunks,
            default_clearance=clearance,
        )

        for node in nodes:
            prov = node.provenance[0] if node.provenance else None
            self.store.upsert_node(node, prov)

        for edge in edges:
            prov = edge.provenance[0] if edge.provenance else None
            self.store.upsert_edge(edge, prov)

        logger.info(
            "Indexed document %s into knowledge graph | nodes=%d edges=%d",
            document.filename, len(nodes), len(edges),
        )
        return len(nodes), len(edges)

    def delete_document_entities(self, document_id: str) -> Dict[str, int]:
        """
        Safely deletes document-derived graph entries while preserving static topology.
        """
        return self.store.delete_document_records(document_id)

    def query(
        self,
        query_term: str,
        user_clearance: str = "viewer",
        max_depth: int = 1,
        limit: int = 25,
    ) -> GraphQueryResult:
        """
        Queries the knowledge graph around an entity, applying RBAC clearance
        and provenance snippet filtering.
        """
        user_role = user_clearance.lower()
        user_level = ROLE_HIERARCHY.get(user_role, 1)

        # Bound depth to safe range
        effective_depth = min(max(1, max_depth), 2)

        # Resolve query term to canonical ID if applicable
        term_clean = query_term.strip()
        canon_unit = canonicalize_unit_code(term_clean)
        canon_tag = canonicalize_equipment_tag(term_clean)

        # Try exact match first, then canonical unit, then canonical equipment tag
        target_node = self.store.get_node(term_clean)
        if not target_node:
            target_node = self.store.get_node(canon_unit)
        if not target_node:
            target_node = self.store.get_node(canon_tag)

        search_id = target_node.id if target_node else term_clean

        nodes, edges = self.store.query_neighbors(
            node_id=search_id,
            max_depth=effective_depth,
            limit=limit,
        )

        # RBAC Filtering on nodes, edges, and provenance snippets
        filtered_nodes = []
        visible_node_ids = set()

        for node in nodes:
            req_level = ROLE_HIERARCHY.get(node.clearance.lower(), 1)
            if user_level >= req_level:
                # Filter node's provenance snippets according to clearance
                filtered_prov = self._filter_provenance_list(node.provenance, user_level)
                node_copy = node.model_copy(update={"provenance": filtered_prov})
                filtered_nodes.append(node_copy)
                visible_node_ids.add(node.id)
            else:
                # For viewer, render classified or elevated node as redacted stub
                if node.category == "classified" and user_level < 3:
                    stub = GraphNode(
                        id=node.id,
                        label=f"[LOCKED: {node.clearance.upper()} CLEARANCE REQUIRED]",
                        category="restricted_stub",
                        clearance=node.clearance,
                        description="Classified asset. Elevated cryptographic authorization required to view properties.",
                        properties={"access_status": "DENIED_RBAC_GATE"},
                        is_static=node.is_static,
                    )
                    filtered_nodes.append(stub)
                    visible_node_ids.add(node.id)

        filtered_edges = []
        for edge in edges:
            req_level = ROLE_HIERARCHY.get(edge.clearance.lower(), 1)
            if (
                user_level >= req_level
                and edge.source in visible_node_ids
                and edge.target in visible_node_ids
            ):
                filtered_prov = self._filter_provenance_list(edge.provenance, user_level)
                edge_copy = edge.model_copy(update={"provenance": filtered_prov})
                filtered_edges.append(edge_copy)

        return GraphQueryResult(
            query=query_term,
            nodes=filtered_nodes,
            edges=filtered_edges,
            total_nodes=len(filtered_nodes),
            total_edges=len(filtered_edges),
            max_depth=effective_depth,
            user_clearance=user_role,
        )

    def get_full_graph(
        self,
        user_clearance: str = "viewer",
    ) -> Dict[str, Any]:
        """
        Returns full graph compatible with existing frontend API response contract.
        """
        user_role = user_clearance.lower()
        user_level = ROLE_HIERARCHY.get(user_role, 1)

        all_nodes = self.store.get_all_nodes()
        all_edges = self.store.get_all_edges()

        filtered_nodes: List[Dict[str, Any]] = []
        hidden_node_count = 0
        visible_ids: Set[str] = set()

        for node in all_nodes:
            req_level = ROLE_HIERARCHY.get(node.clearance.lower(), 1)
            if user_level >= req_level:
                filtered_prov = self._filter_provenance_list(node.provenance, user_level)
                node_dict = {
                    "id": node.id,
                    "label": node.label,
                    "category": node.category,
                    "clearance": node.clearance,
                    "description": node.description,
                    "properties": node.properties,
                    "is_static": node.is_static,
                    "provenance": [p.model_dump() for p in filtered_prov],
                }
                filtered_nodes.append(node_dict)
                visible_ids.add(node.id)
            else:
                hidden_node_count += 1
                if node.category == "classified" and user_level < 3:
                    stub = {
                        "id": node.id,
                        "label": f"[LOCKED: {node.clearance.upper()} CLEARANCE REQUIRED]",
                        "category": "restricted_stub",
                        "clearance": node.clearance,
                        "description": "Classified asset. Elevated cryptographic authorization required to decrypt entity properties.",
                        "properties": {"access_status": "DENIED_RBAC_GATE"},
                    }
                    filtered_nodes.append(stub)
                    visible_ids.add(node.id)

        filtered_edges: List[Dict[str, Any]] = []
        for edge in all_edges:
            req_level = ROLE_HIERARCHY.get(edge.clearance.lower(), 1)
            if (
                user_level >= req_level
                and edge.source in visible_ids
                and edge.target in visible_ids
            ):
                filtered_prov = self._filter_provenance_list(edge.provenance, user_level)
                edge_dict = {
                    "id": edge.id,
                    "source": edge.source,
                    "target": edge.target,
                    "label": edge.relationship,
                    "relationship": edge.relationship,
                    "clearance": edge.clearance,
                    "properties": edge.properties,
                    "is_inferred": edge.is_inferred,
                    "provenance": [p.model_dump() for p in filtered_prov],
                }
                filtered_edges.append(edge_dict)

        categories = {
            "unit": "Plant Operational Units",
            "equipment": "Mechanical & Electrical Assets",
            "sensor": "Telemetry & Sensor Probes",
            "defect": "NDT Defects & Failure Modes (Level 2+)",
            "sop": "Standards & Compliance SOPs",
            "component": "Subsystems & Assemblies",
            "action": "Maintenance Remediations",
            "document": "Live Ingested Documents (ChromaDB)",
            "classified": "Sovereign Classified Formulas & Keys (Level 3)",
        }

        # Structure matches frontend GraphResponse and API expectations
        return {
            "user_role": user_role,
            "effective_clearance": user_role,
            "clearance_level": user_level,
            "nodes_count": len(filtered_nodes),
            "edges_count": len(filtered_edges),
            "hidden_nodes": hidden_node_count,
            "nodes": filtered_nodes,
            "edges": filtered_edges,
            "links": filtered_edges,  # Compatibility alias
            "categories": categories,
        }

    def _filter_provenance_list(
        self,
        prov_list: List[ProvenanceRecord],
        user_level: int,
    ) -> List[ProvenanceRecord]:
        """
        Filters provenance records and redacts source snippets if source evidence
        requires a clearance higher than user_level.
        """
        filtered = []
        for p in prov_list:
            p_level = ROLE_HIERARCHY.get(p.clearance.lower(), 1)
            if user_level >= p_level:
                filtered.append(p)
            else:
                # Redact the verbatim text snippet to prevent clearance bypass
                redacted_p = p.model_copy(
                    update={
                        "source_snippet": f"[RESTRICTED CITATION - REQUIRES {p.clearance.upper()} CLEARANCE]",
                    }
                )
                filtered.append(redacted_p)
        return filtered
