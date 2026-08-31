"""
backend/api/knowledge_graph.py
------------------------------
Company & Industrial Knowledge Graph API with Role-Based Authorization (RBAC) filtering.

Entities:
  - Units (Refinery Units, Hydrocracker, Desalter, Flare)
  - Equipment Assets (MOV-4102-B, V-401, FKOD-101, E-302, P-101A)
  - Sensors & Telemetry (PT-4011, TT-302, VIB-101)
  - Defects & Failure Modes (Stem Galling, Pitting Corrosion, Graphite Degradation)
  - Standards & SOPs (MRPL QA Spec, API 570, ASME B16.20, OSHA PSM)
  - Classified Enterprise Records (Proprietary Formulations, Interlock SCADA Keys)

Clearance Levels:
  - viewer (Level 1): Operational topology, public equipment, telemetry
  - operator (Level 2): Technical failure modes, maintenance manuals, tolerances
  - admin (Level 3): Full graph, proprietary formulations, safety interlock overrides
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Request, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge-graph", tags=["knowledge-graph"])

# Role hierarchy levels
ROLE_LEVELS: Dict[str, int] = {
    "viewer": 1,
    "operator": 2,
    "admin": 3,
}

# Complete Company Knowledge Graph Dataset
RAW_NODES = [
    # Units (Level 1)
    {
        "id": "UNIT_HC04",
        "label": "Hydrocracker Unit 04",
        "category": "unit",
        "clearance": "viewer",
        "description": "High-pressure catalytic hydrocracking unit operating at 145 bar and 420°C.",
        "properties": {"capacity_bpd": "45,000", "status": "ACTIVE", "criticality": "HIGH"},
    },
    {
        "id": "UNIT_DS02",
        "label": "Desalter Unit 02",
        "category": "unit",
        "clearance": "viewer",
        "description": "Crude oil electro-static desalting and dehydration train.",
        "properties": {"throughput_m3h": "320", "status": "ACTIVE", "criticality": "MEDIUM"},
    },
    {
        "id": "UNIT_FLARE",
        "label": "Flare & Blowdown System",
        "category": "unit",
        "clearance": "viewer",
        "description": "Emergency high-pressure flare relief and hydrocarbon containment system.",
        "properties": {"design_press_barg": "10.5", "status": "STANDBY_ARMED", "criticality": "CRITICAL"},
    },
    {
        "id": "UNIT_LAB",
        "label": "Central QA/QC Laboratory",
        "category": "unit",
        "clearance": "viewer",
        "description": "Refinery analytical chemistry and chromatography testing laboratory.",
        "properties": {"accreditation": "ISO/IEC 17025", "status": "OPERATIONAL", "criticality": "HIGH"},
    },

    # Equipment Assets (Level 1)
    {
        "id": "EQ_MOV4102B",
        "label": "MOV-4102-B Motor-Operated Valve",
        "category": "equipment",
        "clearance": "viewer",
        "description": "12-inch 300# carbon steel motor-operated isolation valve on desalter crude feed.",
        "properties": {"tag": "MOV-4102-B", "rating": "ASME Class 300", "body_material": "ASTM A216 WCB"},
    },
    {
        "id": "EQ_V401",
        "label": "V-401 Emergency Depressuring Valve",
        "category": "equipment",
        "clearance": "viewer",
        "description": "Critical emergency depressurizing control valve in hydrocracker reaction loop.",
        "properties": {"tag": "V-401", "design_pressure": "165 bar", "trim": "Stellite 6 faced"},
    },
    {
        "id": "EQ_FKOD101",
        "label": "FKOD-101 Knock-Out Drum",
        "category": "equipment",
        "clearance": "viewer",
        "description": "Vertical liquid-vapor separator drum upstream of main flare stack.",
        "properties": {"tag": "FKOD-101", "volume_m3": "85.0", "relief_set_barg": "3.5"},
    },
    {
        "id": "EQ_E302",
        "label": "E-302 Reboiler Exchanger",
        "category": "equipment",
        "clearance": "viewer",
        "description": "Shell and tube thermosiphon reboiler in stabilization column.",
        "properties": {"tag": "E-302", "shell_press_barg": "18.0", "tube_material": "316L SS"},
    },

    # Sensors (Level 1 & 2)
    {
        "id": "SENS_PT4011",
        "label": "PT-4011 Pressure Transmitter",
        "category": "sensor",
        "clearance": "viewer",
        "description": "Smart 4-20mA HART pressure sensor on FKOD-101 vapor dome.",
        "properties": {"range": "0 - 5.0 bar", "trip_threshold": "2.80 bar", "current_val": "2.85 bar [ALERT]"},
    },
    {
        "id": "SENS_TT302",
        "label": "TT-302 Temperature Element",
        "category": "sensor",
        "clearance": "viewer",
        "description": "Duplex PT100 RTD measuring reboiler tube skin temperature.",
        "properties": {"range": "0 - 450°C", "current_val": "242.4°C"},
    },

    # Failure Modes & Defects (Level 2: Operator / Engineer)
    {
        "id": "DEF_CORROSION_01",
        "label": "Atmospheric Pitting Corrosion",
        "category": "defect",
        "clearance": "operator",
        "description": "Localized chloride and marine salt pitting on external valve bonnet flanges.",
        "properties": {"pit_depth_mm": "1.4", "threshold_mm": "1.8", "risk": "MODERATE"},
    },
    {
        "id": "DEF_GALLING_02",
        "label": "Valve Stem Galling & Friction Lock",
        "category": "defect",
        "clearance": "operator",
        "description": "Micro-welding and mechanical binding on 410 SS stem during partial stroke test.",
        "properties": {"actuator_torque_pct": "142%", "status": "REQUIRES_LUBRICATION"},
    },
    {
        "id": "DEF_PACKING_03",
        "label": "Flexible Graphite Thermal Degradation",
        "category": "defect",
        "clearance": "operator",
        "description": "High-pressure graphite packing elasticity loss due to thermal oxidation in sour H2S service.",
        "properties": {"leakage_rate_ppm": "420", "threshold_ppm": "100", "remedy": "Chesterton 1600"},
    },
    {
        "id": "DEF_VAPOR_SURGE",
        "label": "Flare Liquid Carryover Risk",
        "category": "defect",
        "clearance": "operator",
        "description": "Excess liquid slugging causing sudden vapor pressure excursion above 2.8 bar.",
        "properties": {"mitigation": "Immediate FKOD bottom pumpout & steam ratio increase"},
    },

    # Standards & Compliance SOPs (Level 1 & 2)
    {
        "id": "SOP_MRPL_QA",
        "label": "MRPL Hydrocarbon Quality Specification",
        "category": "sop",
        "clearance": "viewer",
        "description": "Plant standard defining allowable thresholds for sulfur, aromaticity, and kinematic viscosity.",
        "properties": {"doc_id": "MRPL-STD-2024-CH01", "sulfur_max_pct": "0.05", "benzene_max_vol_pct": "1.00"},
    },
    {
        "id": "SOP_API_570",
        "label": "API 570 Piping & Valve Inspection Code",
        "category": "sop",
        "clearance": "operator",
        "description": "In-service inspection, rating, repair, and alteration of hydrocarbon pressure piping.",
        "properties": {"edition": "4th Edition", "ndt_methods": "UT Thickness, Visual, Dye Penetrant"},
    },
    {
        "id": "SOP_ASME_B16",
        "label": "ASME B16.20 Spiral Wound Gaskets",
        "category": "sop",
        "clearance": "operator",
        "description": "Standard for metallic gaskets for pipe flanges (316L SS + Flexible Graphite filler).",
        "properties": {"filler_type": "Flexible Graphite", "pressure_class": "150 - 2500#"},
    },
    {
        "id": "SOP_FLARE_EMERGENCY",
        "label": "SOP-FL-4011 Emergency Flare Interlock",
        "category": "sop",
        "clearance": "operator",
        "description": "Standard operating procedure for high-pressure alarm response on Knock-Out Drum.",
        "properties": {"action_1": "Open bypass XV-4012", "action_2": "Energize assist steam", "action_3": "Trip feed"},
    },

    # Classified & Secret Enterprise Assets (Level 3: Admin Only)
    {
        "id": "SEC_CATALYST_FORMULA",
        "label": "Proprietary Hydrocracking Catalyst Ratio",
        "category": "classified",
        "clearance": "admin",
        "description": "RESTRICTED: Proprietary Zeolite-Alumina NiMo/CoMo catalyst loading ratio and bed activation curve.",
        "properties": {"classification": "SECRET_SOVEREIGN", "formula_hash": "e9b28a71c828d841e4", "margin_impact": "+$3.40/bbl"},
    },
    {
        "id": "SEC_SCADA_OVERRIDE",
        "label": "Safety Instrumented System (SIS) Override Keys",
        "category": "classified",
        "clearance": "admin",
        "description": "RESTRICTED: Plant master SCADA SIL-3 trip bypass cryptographic authorization certificates.",
        "properties": {"classification": "RESTRICTED_AIRGAP", "key_store": "HSM_SLOT_0", "audit_requirement": "MANDATORY_TWO_PERSON"},
    },
    {
        "id": "SEC_LEDGER_ROOT",
        "label": "Master Sovereign Audit Root Authority",
        "category": "classified",
        "clearance": "admin",
        "description": "RESTRICTED: Root ed25519 cryptographic identity authority signing all compliance artifacts.",
        "properties": {"classification": "TOP_SECRET", "storage": "Isolated Secure Enclave", "revocation_epoch": "2030"},
    },
]

RAW_EDGES = [
    # Unit -> Equipment
    {"source": "UNIT_DS02", "target": "EQ_MOV4102B", "label": "CONTAINS", "clearance": "viewer"},
    {"source": "UNIT_HC04", "target": "EQ_V401", "label": "CONTAINS", "clearance": "viewer"},
    {"source": "UNIT_FLARE", "target": "EQ_FKOD101", "label": "CONTAINS", "clearance": "viewer"},
    {"source": "UNIT_HC04", "target": "EQ_E302", "label": "CONTAINS", "clearance": "viewer"},

    # Equipment -> Sensors
    {"source": "EQ_FKOD101", "target": "SENS_PT4011", "label": "MONITORED_BY", "clearance": "viewer"},
    {"source": "EQ_E302", "target": "SENS_TT302", "label": "MONITORED_BY", "clearance": "viewer"},

    # Equipment -> Defects (Operator)
    {"source": "EQ_MOV4102B", "target": "DEF_CORROSION_01", "label": "VULNERABLE_TO", "clearance": "operator"},
    {"source": "EQ_V401", "target": "DEF_GALLING_02", "label": "VULNERABLE_TO", "clearance": "operator"},
    {"source": "EQ_V401", "target": "DEF_PACKING_03", "label": "VULNERABLE_TO", "clearance": "operator"},
    {"source": "EQ_FKOD101", "target": "DEF_VAPOR_SURGE", "label": "RISK_OF", "clearance": "operator"},

    # Defects -> Standards & SOPs (Operator)
    {"source": "DEF_CORROSION_01", "target": "SOP_API_570", "label": "REMEDIATED_BY", "clearance": "operator"},
    {"source": "DEF_PACKING_03", "target": "SOP_ASME_B16", "label": "SPECIFIES_REPLACEMENT", "clearance": "operator"},
    {"source": "DEF_VAPOR_SURGE", "target": "SOP_FLARE_EMERGENCY", "label": "TRIGGERS", "clearance": "operator"},

    # Lab / Process -> QA Specs
    {"source": "UNIT_LAB", "target": "SOP_MRPL_QA", "label": "GOVERNS_BATCHES", "clearance": "viewer"},

    # Classified Admin Edges (Admin Only)
    {"source": "UNIT_HC04", "target": "SEC_CATALYST_FORMULA", "label": "UTILIZES_SECRET_RECIPE", "clearance": "admin"},
    {"source": "UNIT_FLARE", "target": "SEC_SCADA_OVERRIDE", "label": "PROTECTED_BY_SIL3", "clearance": "admin"},
    {"source": "SEC_SCADA_OVERRIDE", "target": "SEC_LEDGER_ROOT", "label": "BOUND_TO_ROOT_KEY", "clearance": "admin"},
]


@router.get("", summary="Fetch company knowledge graph filtered by role authorization")
async def get_knowledge_graph(
    request: Request,
    clearance: Optional[str] = Query(None, description="Requested clearance level override: viewer, operator, admin"),
):
    """
    Returns nodes and edges filtered according to user role authorization.
    Higher clearance levels see more granular, proprietary, and restricted nodes.
    """
    # Detect current user role from session
    current_role = "viewer"
    try:
        if hasattr(request.app.state, "session_manager"):
            token = request.cookies.get("session_token")
            if token:
                session = request.app.state.session_manager.get_session(token)
                if session and not session.is_expired():
                    current_role = session.role
    except Exception:
        pass

    # Allow query parameter override for demo role testing
    effective_role = clearance if clearance in ROLE_LEVELS else current_role
    effective_level = ROLE_LEVELS.get(effective_role, 1)

    # Filter nodes based on authorization level
    filtered_nodes = []
    hidden_node_count = 0

    for node in RAW_NODES:
        node_req_level = ROLE_LEVELS.get(node["clearance"], 1)
        if effective_level >= node_req_level:
            filtered_nodes.append(node)
        else:
            hidden_node_count += 1
            # For viewer, show a redacted placeholder for classified nodes to demonstrate RBAC gating
            if node["category"] == "classified" and effective_level < 3:
                filtered_nodes.append({
                    "id": node["id"],
                    "label": f"[LOCKED: {node['clearance'].upper()} CLEARANCE REQUIRED]",
                    "category": "restricted_stub",
                    "clearance": node["clearance"],
                    "description": "Classified asset. Elevated cryptographic authorization required to decrypt entity properties.",
                    "properties": {"access_status": "DENIED_RBAC_GATE"},
                })

    visible_node_ids = {n["id"] for n in filtered_nodes}

    # Filter edges
    filtered_edges = []
    for edge in RAW_EDGES:
        edge_req_level = ROLE_LEVELS.get(edge["clearance"], 1)
        if (
            effective_level >= edge_req_level
            and edge["source"] in visible_node_ids
            and edge["target"] in visible_node_ids
        ):
            filtered_edges.append(edge)

    return {
        "user_role": current_role,
        "effective_clearance": effective_role,
        "clearance_level": effective_level,
        "nodes_count": len(filtered_nodes),
        "edges_count": len(filtered_edges),
        "hidden_nodes": hidden_node_count,
        "nodes": filtered_nodes,
        "edges": filtered_edges,
        "categories": {
            "unit": "Plant Operational Units",
            "equipment": "Mechanical & Electrical Assets",
            "sensor": "Telemetry & Sensor Probes",
            "defect": "NDT Defects & Failure Modes (Level 2+)",
            "sop": "Standards & Compliance SOPs",
            "classified": "Sovereign Classified Formulas & Keys (Level 3)",
        },
    }
