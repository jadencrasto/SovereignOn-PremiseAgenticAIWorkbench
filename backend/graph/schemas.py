"""
backend/graph/schemas.py
-------------------------
Pydantic data models and enums for the Sovereign Knowledge Graph.

Entities & Relationships:
  - ProvenanceType: STATIC_TOPOLOGY, DOCUMENT_DERIVED, INFERRED
  - NodeCategory: unit, equipment, component, defect, sop, sensor, action, document, classified, restricted_stub
  - Canonical naming conventions:
      Equipment: EQ_<TAG> (e.g. EQ_P204, EQ_MOV4102B)
      Unit: UNIT_<NAME> (e.g. UNIT_HC04)
      Defect/Finding: FINDING_<TAG>_<SLUG> or DEF_<SLUG>
      Action: ACTION_<TAG>_<SLUG> or ACT_<SLUG>
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProvenanceType(str, Enum):
    STATIC_TOPOLOGY = "STATIC_TOPOLOGY"
    DOCUMENT_DERIVED = "DOCUMENT_DERIVED"
    INFERRED = "INFERRED"


class NodeCategory(str, Enum):
    UNIT = "unit"
    EQUIPMENT = "equipment"
    COMPONENT = "component"
    DEFECT = "defect"
    SOP = "sop"
    SENSOR = "sensor"
    ACTION = "action"
    DOCUMENT = "document"
    CLASSIFIED = "classified"
    RESTRICTED_STUB = "restricted_stub"


class ClearanceLevelStr(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class ProvenanceRecord(BaseModel):
    """
    Evidence citation supporting an entity node or graph edge.
    For STATIC_TOPOLOGY: document_id, filename, chunk_id, and source_snippet are None.
    For DOCUMENT_DERIVED: document_id, filename, and chunk_id are mandatory.
    """
    id: str = Field(description="Unique provenance identifier")
    target_type: str = Field(description="'node' or 'edge'")
    target_id: str = Field(description="Canonical node ID or composite edge ID")
    provenance_type: ProvenanceType = Field(default=ProvenanceType.DOCUMENT_DERIVED)
    clearance: str = Field(
        default="viewer",
        description="Clearance level of the source evidence (viewer, operator, admin)",
    )
    document_id: Optional[str] = Field(default=None, description="Source document ID")
    filename: Optional[str] = Field(default=None, description="Source document filename")
    chunk_id: Optional[str] = Field(default=None, description="Source chunk ID")
    page_number: Optional[int] = Field(default=None, description="Page number if available")
    section_header: Optional[str] = Field(default=None, description="Section header if available")
    source_snippet: Optional[str] = Field(default=None, description="Supporting verbatim text excerpt")
    source_label: Optional[str] = Field(default=None, description="Human readable origin (e.g. MRPL Plant Registry)")
    extraction_method: str = Field(default="rule_pattern", description="static_seed, rule_pattern, inference")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class GraphNode(BaseModel):
    """An entity node in the Knowledge Graph."""
    id: str = Field(description="Canonical entity ID (e.g. 'EQ_P204', 'UNIT_HC04')")
    label: str = Field(description="Human readable display label (e.g. 'P-204 Boiler Feed Water Pump')")
    category: str = Field(description="Entity category")
    clearance: str = Field(default="viewer", description="viewer, operator, admin")
    description: Optional[str] = Field(default="", description="Detailed description")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Metadata dictionary")
    is_static: bool = Field(default=False, description="True for company baseline topology, False for document-derived")
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    provenance: List[ProvenanceRecord] = Field(default_factory=list, description="Grounding citations")


class GraphEdge(BaseModel):
    """A directed relationship edge between two entities."""
    id: str = Field(description="Composite key: source_id + ':' + relationship + ':' + target_id")
    source: str = Field(description="Source canonical entity ID")
    target: str = Field(description="Target canonical entity ID")
    relationship: str = Field(description="Relationship verb (e.g. 'LOCATED_IN', 'HAS_FINDING')")
    clearance: str = Field(default="viewer", description="viewer, operator, admin")
    properties: Dict[str, Any] = Field(default_factory=dict)
    is_static: bool = Field(default=False, description="True for company baseline topology, False for document-derived")
    is_inferred: bool = Field(default=False, description="True only if inferred, False if explicitly supported")
    weight: float = Field(default=1.0)
    created_at: Optional[str] = None
    provenance: List[ProvenanceRecord] = Field(default_factory=list, description="Grounding citations")

    # Property alias for backward compatibility with existing frontend expectations
    @property
    def label(self) -> str:
        return self.relationship


class GraphQueryResult(BaseModel):
    """Result of querying the Knowledge Graph."""
    query: str
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    total_nodes: int
    total_edges: int
    max_depth: int
    user_clearance: str


def canonicalize_equipment_tag(tag: str) -> str:
    """
    Standardizes industrial equipment tags into canonical IDs.
    e.g. 'P-204' -> 'EQ_P204', 'p204' -> 'EQ_P204', 'MOV-4102-B' -> 'EQ_MOV4102B'.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]", "", tag).upper()
    if cleaned.startswith("EQ_"):
        return cleaned
    if cleaned.startswith("EQ"):
        cleaned = cleaned[2:]
    return f"EQ_{cleaned}"


def canonicalize_unit_code(unit: str) -> str:
    """
    Standardizes plant unit names into canonical IDs.
    e.g. 'UNIT_HC04', 'HC04', 'Hydrocracker Unit 04' -> 'UNIT_HC04'
    """
    cleaned = re.sub(r"[^A-Za-z0-9]", "", unit).upper()
    if cleaned.startswith("UNIT_"):
        return cleaned
    if cleaned.startswith("UNIT"):
        cleaned = cleaned[4:]
    return f"UNIT_{cleaned}"
