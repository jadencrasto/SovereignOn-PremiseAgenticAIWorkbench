"""
tests/backend/test_standalone_code_and_security_diagnostics.py
--------------------------------------------------------------
Regression tests for:
1. Security diagnostics path sanitization (Local / data/uploads, no C:\\Users).
2. Standalone code execution single output (no duplicated result, no <tool_call> tags, no sources).
3. Semantic XLSX Problem -> Recommended Improvement row extraction and cell quality gate.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
import pytest

from backend.agent.engine import AgentEngine
from backend.agent.memory import ConversationMemory
from backend.config import settings
from backend.tools.security_diagnostics import create_security_diagnostics, SecurityDiagnosticsInput
from backend.security.checker import SecurityChecker


def _make_engine():
    memory = ConversationMemory()
    router = MagicMock()
    return AgentEngine(settings=settings, router=router, memory=memory)


# ===========================================================================
# 1. Security Diagnostics Path Sanitization
# ===========================================================================

@pytest.mark.asyncio
async def test_security_diagnostics_sanitized_paths():
    """Verify security_diagnostics tool returns logical path without absolute Windows paths."""
    fn = create_security_diagnostics(settings_obj=settings)
    res = await fn(SecurityDiagnosticsInput(), user_role="admin")

    doc_storage = res["document_storage_location"]
    assert doc_storage == "Local / data/uploads"
    assert "C:" not in doc_storage
    assert "\\" not in doc_storage

    # Check individual diagnostic checks
    diagnostics = res["diagnostics"]
    sec_004 = next((d for d in diagnostics if d["id"] == "SEC-004"), None)
    assert sec_004 is not None
    assert "C:\\" not in sec_004["details"]
    assert "data/sandbox" in sec_004["details"]

    sec_011 = next((d for d in diagnostics if d["id"] == "SEC-011"), None)
    assert sec_011 is not None
    assert "C:\\" not in sec_011["details"]
    assert "Local / data/uploads" in sec_011["details"]


def test_security_diagnostics_direct_format_no_windows_path():
    """Verify _format_direct_tool_answer scrubs all Windows absolute paths and outputs standard headers."""
    tool_result = {
        "overall_status": "pass",
        "current_model": "ollama/qwen2.5:7b",
        "external_api_connections": "None / local-only",
        "network_access_status": "Restricted / local loopback only",
        "document_storage_location": "Local / data/uploads",
        "audit_logging_status": "Active (SQLite audit trail, 180-day retention, max 50000 rows)",
        "diagnostics": [
            {
                "id": "SEC-004",
                "category": "Filesystem Sandbox",
                "title": "Sandbox Containment",
                "status": "PASS",
                "details": "Sandbox directory is properly contained at 'Local / data/sandbox'.",
            },
            {
                "id": "SEC-011",
                "category": "Storage",
                "title": "Document Storage Location",
                "status": "PASS",
                "details": "Local sovereign document storage contained at 'Local / data/uploads'.",
            }
        ]
    }

    formatted = AgentEngine._format_direct_tool_answer("security_diagnostics", tool_result)
    assert "- **Model**: ollama/qwen2.5:7b" in formatted
    assert "- **External APIs**: None / local-only" in formatted
    assert "- **Network Access**: Restricted / local loopback only" in formatted
    assert "- **Document Storage**: Local / data/uploads" in formatted
    assert "- **Audit Logging**:" in formatted

    # Verify no Windows drive or user directory leak
    assert "C:\\" not in formatted
    assert "Users" not in formatted
    assert "OneDrive" not in formatted


# ===========================================================================
# 2. Standalone Code Execution Single Output & Markup Stripping
# ===========================================================================

def test_clean_reasoning_response_strips_tool_call_markup():
    """Ensure raw <tool_call> tags and multi-line function call signatures are stripped."""
    raw = (
        "Here is the energy analysis:\n"
        "<tool_call>code_execution(code='total = sum([1200, 1350, 1280, 1500, 1420, 1300])')</tool_call>\n"
        "Total: 8050 kWh"
    )
    cleaned = AgentEngine._clean_reasoning_response(raw)
    assert "<tool_call>" not in cleaned
    assert "</tool_call>" not in cleaned
    assert "Total: 8050 kWh" in cleaned


def test_enforce_code_execution_truth_emits_once():
    """Ensure Python code is emitted ONCE and sandbox execution output is emitted ONCE."""
    executed_results = [
        {
            "tool": "code_execution",
            "arguments": {
                "code": "energy = [1200, 1350, 1280, 1500, 1420, 1300]\ntotal = sum(energy)\navg = total / len(energy)\nprint(f'Total: {total} kWh')\nprint(f'Average: {avg:.2f} kWh')"
            },
            "result": {
                "exit_code": 0,
                "stdout": "Total: 8050 kWh\nAverage: 1341.67 kWh\nHighest: Month 4, 1500 kWh\nPercentage increase: 25.00%",
                "stderr": "",
            },
            "success": True,
        }
    ]

    llm_reasoning = "I have calculated the monthly energy consumption based on the sandbox execution."
    final_output = AgentEngine._enforce_code_execution_truth(
        llm_reasoning,
        executed_results,
        "Write and run a Python program that analyzes the monthly energy consumption."
    )

    # User must see Python code ONCE
    assert final_output.count("**Python Code:**") == 1
    # User must see execution output ONCE
    assert final_output.count("**Sandbox Execution Output:**") == 1
    # No duplicate "Calculated Results" block repeating stdout
    assert "**Calculated Results:**" not in final_output
    # Must contain the truth values from stdout
    assert "8050 kWh" in final_output
    assert "1341.67 kWh" in final_output
    assert "25.00%" in final_output


def test_standalone_code_query_is_detected():
    """Standalone energy analysis query is detected as non-RAG task (no sources)."""
    q = (
        "Write and run a Python program that analyzes the monthly energy consumption for six months: "
        "1200, 1350, 1280, 1500, 1420, and 1300 kWh. Calculate the total consumption, average monthly "
        "consumption, highest-consumption month, and percentage increase from the first month to the highest month."
    )
    assert AgentEngine._is_standalone_non_rag_task(q) is True


# ===========================================================================
# 3. XLSX Semantic Problem -> Improvement Quality Gate
# ===========================================================================

def test_pumping_system_semantic_relationship_extraction():
    """Verify semantic problem -> recommended improvement extraction from PDF context."""
    pdf_context = (
        "Common Pumping System Problems\n"
        "As rotating equipment, pumps are subject to wear, erosion, cavitation, and leakage.\n"
        "Cavitation occurs when fluid static pressure drops below vapor pressure, causing bubbles to collapse on impeller blades.\n"
        "Internal recirculation at low flow causes cavitation-like damage and vibration.\n"
        "Packing overtightening creates excessive friction and heat against the shaft sleeve.\n"
        "Oversized pumps operating against throttled control valves generate high backpressures and accelerate bearing wear.\n"
        "Excessive flow noise and pipe vibrations loosen mechanical joints.\n"
        "Bypass lines recirculate excess flow causing high energy loss."
    )

    rows = AgentEngine._extract_relationship_tabular_rows(pdf_context, "Problem", "Recommended Improvement")
    assert len(rows) >= 3

    # Quality Gate checks
    for prob, impr in rows:
        # Cell length strictly <= 150 characters
        assert len(prob) <= 150, f"Problem cell exceeded 150 chars: {prob}"
        assert len(impr) <= 150, f"Improvement cell exceeded 150 chars: {impr}"
        # No OCR chops or markdown table pipes
        assert "|" not in prob
        assert "|" not in impr
        # No document metadata or figure captions
        assert "Figure" not in prob and "Figure" not in impr
        assert "Sourcebook" not in prob and "Sourcebook" not in impr
        # Genuine technical content
        assert len(prob.split()) >= 4
        assert len(impr.split()) >= 4


def test_p204_maintenance_semantic_relationship_extraction():
    """Verify P-204 maintenance report extracts concise genuine problem/improvement pairs."""
    doc_path = Path("data/demo/pump_p204_maintenance.md")
    assert doc_path.exists()
    doc_text = doc_path.read_text(encoding="utf-8")

    rows = AgentEngine._extract_relationship_tabular_rows(doc_text, "Problem", "Recommended Improvement")
    assert len(rows) == 3

    for prob, impr in rows:
        assert len(prob) <= 150
        assert len(impr) <= 150
        assert "## 4. POST-OVERHAUL" not in impr
        assert "Suction Pressure:" not in impr


# ===========================================================================
# 4. Specific Regression Tests for User Presentation Criteria
# ===========================================================================

def test_code_execution_formats_average_to_2_decimal_places():
    """Verify that unformatted raw float average is formatted cleanly to 2 decimal places."""
    raw_stdout = (
        "Total: 8050 kWh\n"
        "Average monthly consumption: 1341.6666666666667 kWh\n"
        "Highest month: Month 4, 1500 kWh"
    )
    user_req = "Calculate the total consumption, average monthly consumption, and highest month."
    formatted = AgentEngine._format_clean_numeric_stdout(raw_stdout, user_req)

    assert "Average monthly consumption: 1341.67 kWh" in formatted
    assert "1341.6666666666667" not in formatted


def test_code_execution_preserves_requested_percentage_wording():
    """Verify requested percentage increase wording and 2 decimal place formatting."""
    raw_stdout = (
        "Total: 8050 kWh\n"
        "Average: 1341.67 kWh\n"
        "Percentage increase: 25%"
    )
    user_req = (
        "Calculate total, average, and percentage increase from first month to highest. "
        "Show the Python code and results."
    )
    formatted = AgentEngine._format_clean_numeric_stdout(raw_stdout, user_req)

    assert "Percentage increase from first month to highest: 25.00%" in formatted


def test_security_diagnostics_cors_output_not_corrupted():
    """Verify CORS diagnostic output is not corrupted into httdata/uploads and displays safe configured origin."""
    tool_result = {
        "overall_status": "pass",
        "current_model": "ollama/qwen2.5:7b",
        "external_api_connections": "None / local-only",
        "network_access_status": "Restricted / local loopback only",
        "document_storage_location": "Local / data/uploads",
        "audit_logging_status": "Active",
        "diagnostics": [
            {
                "id": "SEC-005",
                "category": "API Security",
                "title": "CORS Access Policy",
                "status": "PASS",
                "details": "CORS origins restricted to: http://localhost:5173",
            }
        ]
    }

    formatted = AgentEngine._format_direct_tool_answer("security_diagnostics", tool_result)

    assert "httdata/uploads" not in formatted
    assert "CORS origins restricted to: http://localhost:5173" in formatted
    assert "`[PASS]` **CORS Access Policy**:" in formatted


def test_security_diagnostics_never_exposes_absolute_windows_path():
    """Verify any check detail with an absolute Windows path is sanitized to a safe relative logical path."""
    tool_result = {
        "overall_status": "pass",
        "current_model": "ollama/qwen2.5:7b",
        "external_api_connections": "None / local-only",
        "network_access_status": "Restricted / local loopback only",
        "document_storage_location": "C:\\Users\\jason\\OneDrive\\Desktop\\data\\uploads",
        "audit_logging_status": "Active",
        "diagnostics": [
            {
                "id": "SEC-004",
                "category": "Filesystem Sandbox",
                "title": "Sandbox Containment",
                "status": "PASS",
                "details": r"Sandbox directory is properly contained at C:\Users\jason\OneDrive\Desktop\SovereignOn-PremiseAgenticAIWorkbench\data\sandbox.",
            },
            {
                "id": "SEC-006",
                "category": "Database",
                "title": "SQLite Hardening",
                "status": "PASS",
                "details": "Database at C:/Users/jason/OneDrive/Desktop/data/tasks.db WAL journal mode active.",
            }
        ]
    }

    formatted = AgentEngine._format_direct_tool_answer("security_diagnostics", tool_result)

    assert "C:\\Users" not in formatted
    assert "C:/Users" not in formatted
    assert "OneDrive" not in formatted
    assert "Local / data/sandbox" in formatted
    assert "data/tasks.db" in formatted
    assert "- **Document Storage**: Local / data/uploads" in formatted


def test_format_step_result_content_security_diagnostics():
    """Verify _format_step_result_content formats security_diagnostics without NameError, displays required fields, sanitizes Windows paths, and preserves CORS URL."""
    tool_result = {
        "overall_status": "pass",
        "current_model": "ollama/qwen2.5:7b",
        "external_api_connections": "None / local-only",
        "network_access_status": "Restricted / local loopback only",
        "document_storage_location": r"C:\Users\jason\OneDrive\Desktop\SovereignOn-PremiseAgenticAIWorkbench\data\uploads",
        "audit_logging_status": "Active",
        "diagnostics": [
            {
                "id": "SEC-004",
                "category": "Filesystem Sandbox",
                "title": "Sandbox Containment",
                "status": "PASS",
                "details": r"Sandbox directory is properly contained at C:\Users\jason\OneDrive\Desktop\SovereignOn-PremiseAgenticAIWorkbench\data\sandbox.",
            },
            {
                "id": "SEC-005",
                "category": "API Security",
                "title": "CORS Access Policy",
                "status": "PASS",
                "details": "CORS origins restricted to: http://localhost:5173",
            },
            {
                "id": "SEC-006",
                "category": "Database",
                "title": "SQLite Hardening",
                "status": "PASS",
                "details": "Database at C:/Users/jason/OneDrive/Desktop/data/tasks.db WAL journal mode active.",
            }
        ]
    }

    # Verify formatting completes without NameError
    formatted = AgentEngine._format_step_result_content("security_diagnostics", tool_result)

    # Verify required fields are displayed
    assert "Overall Posture: PASS" in formatted
    assert "Model: ollama/qwen2.5:7b" in formatted
    assert "External APIs: None / local-only" in formatted
    assert "Network Access: Restricted / local loopback only" in formatted
    assert "Audit Logging: Active" in formatted

    # Verify document storage is shown as Local / data/uploads
    assert "Document Storage: Local / data/uploads" in formatted

    # Verify no absolute Windows path such as C:\Users\... is exposed
    assert "C:\\Users" not in formatted
    assert "C:/Users" not in formatted
    assert "OneDrive" not in formatted
    assert "Local / data/sandbox" in formatted
    assert "data/tasks.db" in formatted

    # Verify CORS URL remains intact and is not corrupted into httdata/uploads
    assert "httdata/uploads" not in formatted
    assert "http://localhost:5173" in formatted


def test_full_security_diagnostics_task_flow():
    """Verify full security diagnostics workflow through step formatting and response synthesis."""
    tool_result = {
        "overall_status": "pass",
        "current_model": "ollama/qwen2.5:7b",
        "external_api_connections": "None / local-only",
        "network_access_status": "Restricted / local loopback only",
        "document_storage_location": r"C:\Users\jason\OneDrive\Desktop\SovereignOn-PremiseAgenticAIWorkbench\data\uploads",
        "audit_logging_status": "Active",
        "diagnostics": [
            {
                "id": "SEC-005",
                "title": "CORS Access Policy",
                "status": "PASS",
                "details": "CORS origins restricted to: http://localhost:5173",
            },
            {
                "id": "SEC-004",
                "title": "Sandbox Containment",
                "status": "PASS",
                "details": r"Sandbox directory is properly contained at C:\Users\jason\OneDrive\Desktop\SovereignOn-PremiseAgenticAIWorkbench\data\sandbox.",
            }
        ]
    }

    # 1. Step result formatting during execution
    step_formatted = AgentEngine._format_step_result_content("security_diagnostics", tool_result)
    assert "Overall Posture: PASS" in step_formatted
    assert "http://localhost:5173" in step_formatted
    assert "C:\\Users" not in step_formatted

    # 2. Response synthesis at task completion
    step_item = {
        "tool": "security_diagnostics",
        "arguments": {},
        "result": tool_result,
        "success": True,
    }
    user_request = "Run a complete security diagnostics check and verify local containment."
    completion_resp = AgentEngine._synthesize_task_completion_response(user_request, [step_item])

    assert "Sovereign Security Diagnostics Posture: **PASS**" in completion_resp
    assert "- **Document Storage**: Local / data/uploads" in completion_resp
    assert "CORS origins restricted to: http://localhost:5173" in completion_resp
    assert "httdata/uploads" not in completion_resp
    assert "C:\\Users" not in completion_resp
    assert "Local / data/sandbox" in completion_resp
