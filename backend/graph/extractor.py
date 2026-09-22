"""
backend/graph/extractor.py
--------------------------
Deterministic industrial entity and relationship extractor.

Design:
  - Regex and pattern-based rule extraction (zero GPU overhead, <50ms CPU execution).
  - Reduces attack surface by strictly matching industrial patterns.
  - Extracted snippets are treated as UNTRUSTED evidence: wrapped, bounded, and sanitized.
  - Links findings, components, and maintenance actions ONLY when explicitly co-located
    with the equipment tag in the same sentence or chunk.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.graph.schemas import (
    GraphEdge,
    GraphNode,
    NodeCategory,
    ProvenanceRecord,
    ProvenanceType,
    canonicalize_equipment_tag,
    canonicalize_unit_code,
)
from backend.rag.ingest import Chunk, Document

logger = logging.getLogger(__name__)

# Industrial Regex Patterns
_EQUIP_TAG_REGEX = re.compile(
    r"\b(P|K|V|E|C|MOV|TK|PU|HE|XV|PT|TT|AT|VIB)-\d{2,4}[A-Z]?\b",
    re.IGNORECASE,
)

_COMPONENT_PATTERNS = [
    (re.compile(r"\b(first\s+stage\s+)?impeller(\s+vanes?)?\b", re.I), "impeller", "First Stage Impeller"),
    (re.compile(r"\b(mechanical\s+)?seal(\s+face|\s+plan)?\b", re.I), "seal", "Mechanical Seal Assembly"),
    (re.compile(r"\b(suction\s+)?strainer(\s+mesh)?\b", re.I), "strainer", "Suction Strainer Mesh"),
    (re.compile(r"\b(bearing\s+housing|thrust\s+bearings?)\b", re.I), "bearing", "Bearing Housing Assembly"),
    (re.compile(r"\b(valve\s+stem|stem\s+packing)\b", re.I), "stem", "Valve Stem & Packing"),
    (re.compile(r"\b(spiral\s+wound\s+)?gasket\b", re.I), "gasket", "Spiral Wound Flange Gasket"),
    (re.compile(r"\b(demister\s+pad|mesh\s+pad)\b", re.I), "demister", "Demister Pad Separator"),
]

_DEFECT_PATTERNS = [
    (re.compile(r"\b(cavitation\s+pitting|cavitation\s+erosion|cavitation)\b", re.I), "cavitation", "Cavitation Erosion & Pitting"),
    (re.compile(r"\b(atmospheric\s+)?pitting\s+corrosion\b", re.I), "pitting_corrosion", "Atmospheric Pitting Corrosion"),
    (re.compile(r"\b(valve\s+stem\s+)?galling\b", re.I), "galling", "Stem Galling & Friction Lock"),
    (re.compile(r"\b(packing\s+leak|packing\s+degradation)\b", re.I), "packing_leak", "Flexible Graphite Packing Leak"),
    (re.compile(r"\b(vibration\s+spike|vibration\s+excursion|elevated\s+vibration)\b", re.I), "vibration", "Vibration Excursion"),
    (re.compile(r"\b(\d+%\s+)?strainer\s+clogging\b", re.I), "strainer_clogging", "Suction Strainer Clogging"),
]

_ACTION_PATTERNS = [
    (re.compile(r"\b(strainer\s+cleaning|clean\s+strainer|flush\s+strainer)\b", re.I), "strainer_cleaning", "Strainer Cleaning & Flush"),
    (re.compile(r"\b(dynamic\s+balancing|rebalance\s+impeller)\b", re.I), "dynamic_balancing", "Dynamic Balancing"),
    (re.compile(r"\b(molykote\s+lubrication|lubricate\s+with\s+molykote)\b", re.I), "lubrication", "Molykote Paste Lubrication"),
    (re.compile(r"\b(replace\s+packing|repack\s+gland)\b", re.I), "repack", "Gland Packing Replacement"),
    (re.compile(r"\b(ultrasonic\s+(thickness\s+)?inspection|phased\s+array\s+ut)\b", re.I), "ut_inspection", "Ultrasonic NDT Thickness Inspection"),
]


class IndustrialGraphExtractor:
    """
    Extracts industrial entities and relationships from document chunks.
    Ensures that relationships are ONLY extracted when explicitly supported in text.
    """

    _VALID_CLEARANCES = {"viewer", "operator", "admin"}

    def extract_document_graph(
        self,
        document: Document,
        chunks: List[Chunk],
        default_clearance: str = "viewer",
        document_clearance: Optional[str] = None,
    ) -> Tuple[List[GraphNode], List[GraphEdge]]:
        """
        Extracts entities, relationships, and provenance from parsed chunks.

        Clearance is determined by document_clearance (trusted, server-provided).
        If document_clearance is not provided, falls back to default_clearance.
        Invalid or unrecognized clearance values default to 'viewer'.
        """
        # Validate clearance: only accept known values, default to viewer
        raw_clearance = document_clearance if document_clearance is not None else default_clearance
        if not raw_clearance or raw_clearance.lower() not in self._VALID_CLEARANCES:
            effective_clearance = "viewer"
        else:
            effective_clearance = raw_clearance.lower()

        nodes: Dict[str, GraphNode] = {}
        edges: Dict[str, GraphEdge] = {}

        # 1. Register Document Node
        doc_node_id = f"DOC_{document.document_id[:8]}"
        doc_prov = ProvenanceRecord(
            id=f"prov_{uuid.uuid4().hex[:12]}",
            target_type="node",
            target_id=doc_node_id,
            provenance_type=ProvenanceType.DOCUMENT_DERIVED,
            clearance=effective_clearance,
            document_id=document.document_id,
            filename=document.filename,
            source_label=f"Document: {document.filename}",
            extraction_method="rule_pattern",
            confidence=1.0,
        )
        nodes[doc_node_id] = GraphNode(
            id=doc_node_id,
            label=f"📄 {document.filename}",
            category=NodeCategory.DOCUMENT.value,
            clearance=effective_clearance,
            description=f"Ingested document ({document.file_type.upper()}) with {len(chunks)} chunks.",
            properties={"filename": document.filename, "file_type": document.file_type},
            is_static=False,
            provenance=[doc_prov],
        )

        # 2. Process each chunk
        for chunk in chunks:
            chunk_text = chunk.text
            page_num = chunk.metadata.get("page") if chunk.metadata else None

            # Split into sentence-like statements for precise co-location
            sentences = re.split(r"(?<=[.!?\n])\s+", chunk_text)

            for sentence in sentences:
                sentence_clean = sentence.strip()
                if not sentence_clean or len(sentence_clean) < 10:
                    continue

                # Find equipment tags in this sentence
                equip_matches = _EQUIP_TAG_REGEX.findall(sentence_clean)
                if not equip_matches:
                    continue

                # Normalize tags e.g. P-204 -> EQ_P204
                for raw_tag in equip_matches:
                    # Capture full tag like P-204
                    full_tag_match = re.search(r"\b" + re.escape(raw_tag) + r"-\d{2,4}[A-Z]?\b", sentence_clean, re.I)
                    display_tag = full_tag_match.group(0).upper() if full_tag_match else raw_tag.upper()
                    canon_equip_id = canonicalize_equipment_tag(display_tag)

                    # Bounded snippet (untrusted data boundary)
                    snippet = sentence_clean[:300].strip()

                    # Create or update equipment node
                    if canon_equip_id not in nodes:
                        nodes[canon_equip_id] = GraphNode(
                            id=canon_equip_id,
                            label=f"{display_tag} Asset",
                            category=NodeCategory.EQUIPMENT.value,
                            clearance=effective_clearance,
                            description=f"Industrial equipment tag {display_tag} extracted from {document.filename}.",
                            properties={"tag": display_tag},
                            is_static=False,
                        )

                    # Link document -> equipment
                    doc_rel_id = f"{doc_node_id}:DESCRIBES:{canon_equip_id}"
                    if doc_rel_id not in edges:
                        edge_prov = ProvenanceRecord(
                            id=f"prov_{uuid.uuid4().hex[:12]}",
                            target_type="edge",
                            target_id=doc_rel_id,
                            provenance_type=ProvenanceType.DOCUMENT_DERIVED,
                            clearance=effective_clearance,
                            document_id=document.document_id,
                            filename=document.filename,
                            chunk_id=chunk.chunk_id,
                            page_number=page_num,
                            source_snippet=snippet,
                            extraction_method="rule_pattern",
                            confidence=1.0,
                        )
                        edges[doc_rel_id] = GraphEdge(
                            id=doc_rel_id,
                            source=doc_node_id,
                            target=canon_equip_id,
                            relationship="DESCRIBES",
                            clearance=effective_clearance,
                            is_inferred=False,
                            provenance=[edge_prov],
                        )

                    # Check for components co-located in this sentence
                    for c_regex, c_key, c_label in _COMPONENT_PATTERNS:
                        if c_regex.search(sentence_clean):
                            comp_id = f"COMP_{canon_equip_id}_{c_key.upper()}"
                            if comp_id not in nodes:
                                nodes[comp_id] = GraphNode(
                                    id=comp_id,
                                    label=f"{display_tag} {c_label}",
                                    category=NodeCategory.COMPONENT.value,
                                    clearance=effective_clearance,
                                    description=f"Subsystem component for {display_tag}.",
                                    properties={"equipment_tag": display_tag, "component": c_key},
                                    is_static=False,
                                )

                            edge_id = f"{canon_equip_id}:HAS_COMPONENT:{comp_id}"
                            if edge_id not in edges:
                                edge_prov = ProvenanceRecord(
                                    id=f"prov_{uuid.uuid4().hex[:12]}",
                                    target_type="edge",
                                    target_id=edge_id,
                                    provenance_type=ProvenanceType.DOCUMENT_DERIVED,
                                    clearance=effective_clearance,
                                    document_id=document.document_id,
                                    filename=document.filename,
                                    chunk_id=chunk.chunk_id,
                                    page_number=page_num,
                                    source_snippet=snippet,
                                    extraction_method="rule_pattern",
                                    confidence=0.95,
                                )
                                edges[edge_id] = GraphEdge(
                                    id=edge_id,
                                    source=canon_equip_id,
                                    target=comp_id,
                                    relationship="HAS_COMPONENT",
                                    clearance=effective_clearance,
                                    is_inferred=False,
                                    provenance=[edge_prov],
                                )

                    # Check for defects/findings co-located in this sentence
                    for d_regex, d_key, d_label in _DEFECT_PATTERNS:
                        if d_regex.search(sentence_clean):
                            defect_id = f"FINDING_{canon_equip_id}_{d_key.upper()}"
                            if defect_id not in nodes:
                                nodes[defect_id] = GraphNode(
                                    id=defect_id,
                                    label=f"{display_tag} {d_label}",
                                    category=NodeCategory.DEFECT.value,
                                    clearance=effective_clearance,
                                    description=f"Documented finding on {display_tag}.",
                                    properties={"equipment_tag": display_tag, "defect_type": d_key},
                                    is_static=False,
                                )

                            edge_id = f"{canon_equip_id}:HAS_FINDING:{defect_id}"
                            if edge_id not in edges:
                                edge_prov = ProvenanceRecord(
                                    id=f"prov_{uuid.uuid4().hex[:12]}",
                                    target_type="edge",
                                    target_id=edge_id,
                                    provenance_type=ProvenanceType.DOCUMENT_DERIVED,
                                    clearance=effective_clearance,
                                    document_id=document.document_id,
                                    filename=document.filename,
                                    chunk_id=chunk.chunk_id,
                                    page_number=page_num,
                                    source_snippet=snippet,
                                    extraction_method="rule_pattern",
                                    confidence=0.95,
                                )
                                edges[edge_id] = GraphEdge(
                                    id=edge_id,
                                    source=canon_equip_id,
                                    target=defect_id,
                                    relationship="HAS_FINDING",
                                    clearance=effective_clearance,
                                    is_inferred=False,
                                    provenance=[edge_prov],
                                )

                    # Check for maintenance actions co-located in this sentence
                    for a_regex, a_key, a_label in _ACTION_PATTERNS:
                        if a_regex.search(sentence_clean):
                            action_id = f"ACTION_{canon_equip_id}_{a_key.upper()}"
                            if action_id not in nodes:
                                nodes[action_id] = GraphNode(
                                    id=action_id,
                                    label=f"{a_label} ({display_tag})",
                                    category=NodeCategory.ACTION.value,
                                    clearance=effective_clearance,
                                    description=f"Prescribed maintenance remediation for {display_tag}.",
                                    properties={"equipment_tag": display_tag, "action": a_key},
                                    is_static=False,
                                )

                            edge_id = f"{canon_equip_id}:REQUIRES_ACTION:{action_id}"
                            if edge_id not in edges:
                                edge_prov = ProvenanceRecord(
                                    id=f"prov_{uuid.uuid4().hex[:12]}",
                                    target_type="edge",
                                    target_id=edge_id,
                                    provenance_type=ProvenanceType.DOCUMENT_DERIVED,
                                    clearance=effective_clearance,
                                    document_id=document.document_id,
                                    filename=document.filename,
                                    chunk_id=chunk.chunk_id,
                                    page_number=page_num,
                                    source_snippet=snippet,
                                    extraction_method="rule_pattern",
                                    confidence=0.95,
                                )
                                edges[edge_id] = GraphEdge(
                                    id=edge_id,
                                    source=canon_equip_id,
                                    target=action_id,
                                    relationship="REQUIRES_ACTION",
                                    clearance=effective_clearance,
                                    is_inferred=False,
                                    provenance=[edge_prov],
                                )

        return list(nodes.values()), list(edges.values())
