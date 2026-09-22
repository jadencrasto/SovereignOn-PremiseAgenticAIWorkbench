"""
backend/graph/seed_data.py
--------------------------
Static baseline company topology and plant asset registry for MRPL Refinery.
Migrated from hardcoded dataset in backend/api/knowledge_graph.py.
"""

from __future__ import annotations
from typing import Any, Dict, List

STATIC_NODES: List[Dict[str, Any]] = [
    # -------------------------------------------------------------
    # 1. Operational Units (Level 1: Viewer)
    # -------------------------------------------------------------
    {
        "id": "UNIT_HC04",
        "label": "Hydrocracker Unit 04",
        "category": "unit",
        "clearance": "viewer",
        "description": "High-pressure catalytic hydrocracking unit operating at 145 bar and 420°C for diesel yield maximization.",
        "properties": {"capacity_bpd": "45,000", "status": "ACTIVE", "criticality": "HIGH", "operating_temp": "420°C", "design_pressure": "145 bar"},
    },
    {
        "id": "UNIT_DS02",
        "label": "Desalter Unit 02",
        "category": "unit",
        "clearance": "viewer",
        "description": "Crude oil electro-static desalting and dehydration train for BS&W salt removal.",
        "properties": {"throughput_m3h": "320", "status": "ACTIVE", "criticality": "MEDIUM", "salt_target": "< 3.0 PTB"},
    },
    {
        "id": "UNIT_CDU01",
        "label": "Crude Distillation Unit 01",
        "category": "unit",
        "clearance": "viewer",
        "description": "Primary atmospheric crude fractionation column separating naphtha, kerosene, and gas oils.",
        "properties": {"capacity_bpd": "120,000", "status": "ACTIVE", "criticality": "HIGH", "column_trays": "48"},
    },
    {
        "id": "UNIT_DCU03",
        "label": "Delayed Coker Unit 03",
        "category": "unit",
        "clearance": "viewer",
        "description": "Thermal cracking unit upgrading vacuum residue into light distillate products and petroleum coke.",
        "properties": {"cycle_hours": "18.0", "status": "ACTIVE", "criticality": "HIGH", "coke_drums": "4"},
    },
    {
        "id": "UNIT_HGU02",
        "label": "Hydrogen Generation Unit 02",
        "category": "unit",
        "clearance": "viewer",
        "description": "Steam methane reforming unit producing 99.9% pure hydrogen for hydroprocessing.",
        "properties": {"output_nm3h": "70,000", "purity": "99.95%", "status": "ACTIVE", "criticality": "HIGH"},
    },
    {
        "id": "UNIT_FLARE",
        "label": "Emergency Flare & Relief Stack",
        "category": "unit",
        "clearance": "viewer",
        "description": "Emergency high-pressure flare relief and hydrocarbon containment system with sonic tip burners.",
        "properties": {"design_press_barg": "10.5", "status": "STANDBY_ARMED", "criticality": "CRITICAL", "smokeless_steam": "AUTO"},
    },
    {
        "id": "UNIT_LAB",
        "label": "Central QA/QC Laboratory",
        "category": "unit",
        "clearance": "viewer",
        "description": "Refinery analytical chemistry, gas chromatography, and ASTM compliance testing facility.",
        "properties": {"accreditation": "ISO/IEC 17025", "status": "OPERATIONAL", "criticality": "HIGH", "daily_assays": "140"},
    },

    # -------------------------------------------------------------
    # 2. Major Equipment Assets (Level 1: Viewer)
    # -------------------------------------------------------------
    {
        "id": "EQ_MOV4102B",
        "label": "MOV-4102-B Motor-Operated Valve",
        "category": "equipment",
        "clearance": "viewer",
        "description": "12-inch 300# carbon steel motor-operated isolation valve on desalter crude feed manifold.",
        "properties": {"tag": "MOV-4102-B", "rating": "ASME Class 300", "body_material": "ASTM A216 WCB", "actuator": "Rotork IQ3"},
    },
    {
        "id": "EQ_V401",
        "label": "V-401 Emergency Depressuring Valve",
        "category": "equipment",
        "clearance": "viewer",
        "description": "Critical emergency depressurizing control valve in hydrocracker reaction loop (SIL-3 rated).",
        "properties": {"tag": "V-401", "design_pressure": "165 bar", "trim": "Stellite 6 faced", "failsafe": "FAIL_OPEN"},
    },
    {
        "id": "EQ_FKOD101",
        "label": "FKOD-101 Knock-Out Drum",
        "category": "equipment",
        "clearance": "viewer",
        "description": "Vertical liquid-vapor separator drum upstream of main flare stack preventing liquid carryover.",
        "properties": {"tag": "FKOD-101", "volume_m3": "85.0", "relief_set_barg": "3.5", "demister_pad": "316L Mesh"},
    },
    {
        "id": "EQ_E302",
        "label": "E-302 Reboiler Exchanger",
        "category": "equipment",
        "clearance": "viewer",
        "description": "Shell and tube thermosiphon reboiler in stabilization column.",
        "properties": {"tag": "E-302", "shell_press_barg": "18.0", "tube_material": "316L SS", "duty_mw": "14.2"},
    },
    {
        "id": "EQ_P101A",
        "label": "P-101A Heavy Feed Charge Pump",
        "category": "equipment",
        "clearance": "viewer",
        "description": "Multi-stage centrifugal boiler feed and heavy gas oil charge pump driven by 450kW induction motor.",
        "properties": {"tag": "P-101A", "flow_m3h": "280", "head_m": "620", "seal_plan": "API Plan 53B"},
    },
    {
        "id": "EQ_C201",
        "label": "C-201 Wet Gas Compressor Train",
        "category": "equipment",
        "clearance": "viewer",
        "description": "Two-stage centrifugal hydrocarbon compressor driven by condensing steam turbine.",
        "properties": {"tag": "C-201", "speed_rpm": "8,450", "discharge_press": "24.5 bar", "power_mw": "6.8"},
    },

    # -------------------------------------------------------------
    # 3. Telemetry & Sensor Probes (Level 1: Viewer)
    # -------------------------------------------------------------
    {
        "id": "SENS_PT4011",
        "label": "PT-4011 Pressure Transmitter",
        "category": "sensor",
        "clearance": "viewer",
        "description": "Smart 4-20mA HART pressure sensor on FKOD-101 vapor dome.",
        "properties": {"range": "0 - 5.0 bar", "trip_threshold": "2.80 bar", "current_val": "2.85 bar [ALERT]", "sample_rate": "100ms"},
    },
    {
        "id": "SENS_TT302",
        "label": "TT-302 Temperature Element",
        "category": "sensor",
        "clearance": "viewer",
        "description": "Duplex PT100 RTD measuring reboiler tube skin temperature.",
        "properties": {"range": "0 - 450°C", "current_val": "242.4°C", "drift_pct": "< 0.05%"},
    },
    {
        "id": "SENS_VIB101",
        "label": "VIB-101 Tri-Axial Vibration Probe",
        "category": "sensor",
        "clearance": "viewer",
        "description": "Continuous piezoelectric accelerometer monitoring P-101A pump bearing housing.",
        "properties": {"rms_velocity_mms": "4.2", "iso_limit_mms": "4.5", "status": "WARNING_ELEVATED"},
    },
    {
        "id": "SENS_AT501",
        "label": "AT-501 H2S Gas Toxic Detector",
        "category": "sensor",
        "clearance": "viewer",
        "description": "Electrochemical trace gas sensor array deployed in hydrocracker compressor shelter.",
        "properties": {"range": "0 - 100 ppm", "current_val": "0.4 ppm [NORMAL]", "calibration_due": "2026-11-15"},
    },

    # -------------------------------------------------------------
    # 4. Failure Modes & Defects (Level 2: Operator / Engineer)
    # -------------------------------------------------------------
    {
        "id": "DEF_CORROSION_01",
        "label": "Atmospheric Pitting Corrosion",
        "category": "defect",
        "clearance": "operator",
        "description": "Localized chloride and marine salt pitting on external valve bonnet flanges.",
        "properties": {"pit_depth_mm": "1.4", "threshold_mm": "1.8", "risk": "MODERATE", "ndt_technique": "Phased Array UT"},
    },
    {
        "id": "DEF_GALLING_02",
        "label": "Valve Stem Galling & Friction Lock",
        "category": "defect",
        "clearance": "operator",
        "description": "Micro-welding and mechanical binding on 410 SS stem during partial stroke test.",
        "properties": {"actuator_torque_pct": "142%", "status": "REQUIRES_LUBRICATION", "remedy": "Molykote paste"},
    },
    {
        "id": "DEF_PACKING_03",
        "label": "Flexible Graphite Thermal Degradation",
        "category": "defect",
        "clearance": "operator",
        "description": "High-pressure graphite packing elasticity loss due to thermal oxidation in sour H2S service.",
        "properties": {"leakage_rate_ppm": "420", "threshold_ppm": "100", "remedy": "Chesterton 1600 replacement"},
    },
    {
        "id": "DEF_VAPOR_SURGE",
        "label": "Flare Liquid Carryover Risk",
        "category": "defect",
        "clearance": "operator",
        "description": "Excess liquid slugging causing sudden vapor pressure excursion above 2.8 bar.",
        "properties": {"mitigation": "Immediate FKOD bottom pumpout & steam ratio increase", "criticality": "HIGH"},
    },
    {
        "id": "DEF_CAVITATION",
        "label": "Impeller Vane Cavitation Erosion",
        "category": "defect",
        "clearance": "operator",
        "description": "Low suction NPSHa margin causing vapor bubble collapse and micro-pitting on P-101A first stage impeller.",
        "properties": {"npsh_margin_m": "0.35", "vibration_signature": "High Frequency Acoustic Shock"},
    },

    # -------------------------------------------------------------
    # 5. Standards & Compliance SOPs (Level 1 & 2)
    # -------------------------------------------------------------
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
    {
        "id": "SOP_OSHA_PSM",
        "label": "OSHA PSM 1910.119 Process Safety",
        "category": "sop",
        "clearance": "operator",
        "description": "Mandatory Management of Change (MOC) and Mechanical Integrity verification framework.",
        "properties": {"audit_frequency": "3 Years", "compliance_tier": "MANDATORY_FEDERAL"},
    },

    # -------------------------------------------------------------
    # 6. Classified Enterprise Assets (Level 3: Admin Only)
    # -------------------------------------------------------------
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

STATIC_EDGES: List[Dict[str, Any]] = [
    # Units -> Units & Pipelines
    {"source": "UNIT_CDU01", "target": "UNIT_DS02", "relationship": "RECEIVES_DESALTED_CRUDE", "clearance": "viewer"},
    {"source": "UNIT_CDU01", "target": "UNIT_HC04", "relationship": "FEEDS_VACUUM_GAS_OIL", "clearance": "viewer"},
    {"source": "UNIT_CDU01", "target": "UNIT_DCU03", "relationship": "TRANSFERS_VACUUM_RESIDUE", "clearance": "viewer"},
    {"source": "UNIT_HGU02", "target": "UNIT_HC04", "relationship": "SUPPLIES_HIGH_PURITY_H2", "clearance": "viewer"},
    {"source": "UNIT_HC04", "target": "UNIT_FLARE", "relationship": "RELIEVES_OVERPRESSURE_TO", "clearance": "viewer"},

    # Unit -> Equipment
    {"source": "UNIT_DS02", "target": "EQ_MOV4102B", "relationship": "CONTAINS", "clearance": "viewer"},
    {"source": "UNIT_HC04", "target": "EQ_V401", "relationship": "CONTAINS", "clearance": "viewer"},
    {"source": "UNIT_FLARE", "target": "EQ_FKOD101", "relationship": "CONTAINS", "clearance": "viewer"},
    {"source": "UNIT_HC04", "target": "EQ_E302", "relationship": "CONTAINS", "clearance": "viewer"},
    {"source": "UNIT_CDU01", "target": "EQ_P101A", "relationship": "OPERATES", "clearance": "viewer"},
    {"source": "UNIT_DCU03", "target": "EQ_C201", "relationship": "OPERATES", "clearance": "viewer"},

    # Equipment -> Sensors
    {"source": "EQ_FKOD101", "target": "SENS_PT4011", "relationship": "MONITORED_BY", "clearance": "viewer"},
    {"source": "EQ_E302", "target": "SENS_TT302", "relationship": "MONITORED_BY", "clearance": "viewer"},
    {"source": "EQ_P101A", "target": "SENS_VIB101", "relationship": "MONITORED_BY", "clearance": "viewer"},
    {"source": "EQ_C201", "target": "SENS_AT501", "relationship": "SURROUNDED_BY", "clearance": "viewer"},

    # Equipment -> Defects (Operator)
    {"source": "EQ_MOV4102B", "target": "DEF_CORROSION_01", "relationship": "VULNERABLE_TO", "clearance": "operator"},
    {"source": "EQ_V401", "target": "DEF_GALLING_02", "relationship": "VULNERABLE_TO", "clearance": "operator"},
    {"source": "EQ_V401", "target": "DEF_PACKING_03", "relationship": "VULNERABLE_TO", "clearance": "operator"},
    {"source": "EQ_FKOD101", "target": "DEF_VAPOR_SURGE", "relationship": "RISK_OF", "clearance": "operator"},
    {"source": "EQ_P101A", "target": "DEF_CAVITATION", "relationship": "PRONE_TO", "clearance": "operator"},

    # Defects -> Standards & SOPs (Operator)
    {"source": "DEF_CORROSION_01", "target": "SOP_API_570", "relationship": "REMEDIATED_BY", "clearance": "operator"},
    {"source": "DEF_PACKING_03", "target": "SOP_ASME_B16", "relationship": "SPECIFIES_REPLACEMENT", "clearance": "operator"},
    {"source": "DEF_VAPOR_SURGE", "target": "SOP_FLARE_EMERGENCY", "relationship": "TRIGGERS", "clearance": "operator"},
    {"source": "DEF_CAVITATION", "target": "SOP_OSHA_PSM", "relationship": "LOGGED_UNDER", "clearance": "operator"},

    # Lab / Process -> QA Specs
    {"source": "UNIT_LAB", "target": "SOP_MRPL_QA", "relationship": "GOVERNS_BATCHES", "clearance": "viewer"},
    {"source": "UNIT_HC04", "target": "SOP_MRPL_QA", "relationship": "AUDITED_AGAINST", "clearance": "viewer"},

    # Classified Admin Edges (Admin Only)
    {"source": "UNIT_HC04", "target": "SEC_CATALYST_FORMULA", "relationship": "UTILIZES_SECRET_RECIPE", "clearance": "admin"},
    {"source": "UNIT_FLARE", "target": "SEC_SCADA_OVERRIDE", "relationship": "PROTECTED_BY_SIL3", "clearance": "admin"},
    {"source": "SEC_SCADA_OVERRIDE", "target": "SEC_LEDGER_ROOT", "relationship": "BOUND_TO_ROOT_KEY", "clearance": "admin"},
]
