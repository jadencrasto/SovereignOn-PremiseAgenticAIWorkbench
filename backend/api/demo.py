"""
backend/api/demo.py
--------------------
One-Click Industrial Demo Scenarios API.

Provides pre-packaged industrial scenarios for the live internal round:
  1. Automated Industrial Diligence & Reporting (MRPL Chemical Composition & Compliance XLSX)
  2. Multimodal Equipment Diagnostics (Valve MOV-4102-B Pitting Corrosion & SOP Lookup)
  3. Autonomous Incident Runbook (Flare Knock-Out Drum Pressure Anomaly & Dispatch Log)
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.config import settings

router = APIRouter(prefix="/api/demo", tags=["demo"])

DEMO_SCENARIOS = [
    {
        "id": "industrial_diligence",
        "title": "Automated Industrial Diligence & Reporting",
        "category": "Chemical & Quality Assurance",
        "unit": "MRPL Refinery MSQU / DHDS Quality Control",
        "badge": "XLSX Artifact + Verification",
        "description": (
            "Cross-checks lab hydrocarbon composition test data against internal benchmark standard "
            "(MRPL-STD-QC-2026-V4). Identifies sulfur, benzene, and RVP deviations, verifies calculations, "
            "and generates a styled Excel compliance artifact."
        ),
        "prompt": (
            "Analyze the laboratory test dataset 'mrpl_lab_composition_test.csv', retrieve the relevant MRPL "
            "hydrocarbon benchmark standards from our local knowledge base, cross-check each measured parameter, "
            "identify any regulatory or safe tolerance deviations, verify the results, and generate a styled "
            "compliance report spreadsheet named 'mrpl_chemical_compliance_report.xlsx'."
        ),
        "dataset_file": "mrpl_lab_composition_test.csv",
        "benchmark_doc": "mrpl_refinery_specs.md",
        "expected_artifact": "mrpl_chemical_compliance_report.xlsx",
        "is_multimodal": False,
    },
    {
        "id": "equipment_diagnostics",
        "title": "Multimodal Equipment Diagnostics",
        "category": "Mechanical Integrity & Inspection",
        "unit": "Crude Distillation Unit (CDU-1) Bottoms Line",
        "badge": "VLM Vision + SOP Retrieval",
        "description": (
            "Examines digital inspection photo of valve MOV-4102-B, extracts stamped nameplate specifications "
            "and localized flange pitting corrosion, retrieves mechanical SOP-MECH-VLV-402, and compiles a "
            "structured maintenance advisory."
        ),
        "prompt": (
            "Analyze this digital inspection image. Extract the visible equipment tag and defect symptoms, "
            "retrieve our mechanical valve maintenance standard for this tag and defect category, clearly label "
            "any visual observations vs document ground-truth, and produce a maintenance advisory report."
        ),
        "image_file": "corroded_valve_sample.png",
        "benchmark_doc": "equipment_valve_manual.md",
        "expected_artifact": "valve_inspection_report.md",
        "is_multimodal": True,
    },
    {
        "id": "incident_runbook",
        "title": "Autonomous Incident Runbook",
        "category": "Process Safety & Emergency Response",
        "unit": "Main Flare & Hydrocarbon Relief Network",
        "badge": "Emergency SOP + Ticker Dispatch",
        "description": (
            "Responds to a simulated high pressure anomaly on Flare Knock-Out Drum FKOD-101 (2.85 bar gauge). "
            "Retrieves emergency SOP-RUNBOOK-087, determines the required sequential mitigation actions, "
            "and generates a draft incident dispatch log and safety alert plan."
        ),
        "prompt": (
            "Emergency Alarm: Pressure transmitter PT-104A on Flare Knock-Out Drum FKOD-101 has spiked to "
            "2.85 bar gauge (PAH-104 exceeded) with PT-104B indicating 2.42 bar gauge. Retrieve our emergency "
            "runbook procedure for FKOD-101, determine the mandatory sequential response actions, and produce "
            "the draft incident dispatch log and control room ticker alert."
        ),
        "benchmark_doc": "flare_drum_incident_runbook.md",
        "expected_artifact": "incident_dispatch_log.md",
        "is_multimodal": False,
    },
]


@router.get("/scenarios", summary="List preloaded industrial demo scenarios")
async def list_scenarios():
    """Returns the list of 3 official industrial demo scenarios with metadata and prompts."""
    return {"scenarios": DEMO_SCENARIOS}
