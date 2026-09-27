"""
tests/backend/test_diagnostic_tools.py
--------------------------------------
Unit and regression tests for new agent tools:
1. hardware_status
2. model_scan
3. security_diagnostics (RBAC permission enforcement)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.auth.models import Permission, UserRole
from backend.tools.hardware_status import (
    HardwareStatusInput,
    create_hardware_status,
)
from backend.tools.model_scan import (
    ModelScanInput,
    create_model_scan,
)
from backend.tools.security_diagnostics import (
    SecurityDiagnosticsInput,
    create_security_diagnostics,
)


@pytest.mark.asyncio
async def test_hardware_status_execution():
    """Test hardware_status executes and returns system telemetry."""
    tool_fn = create_hardware_status()
    result = await tool_fn(HardwareStatusInput())

    assert "cpu_percent" in result
    assert "ram_total_mb" in result
    assert "ram_used_mb" in result
    assert "ram_percent" in result
    assert "gpu_available" in result
    assert "summary" in result
    assert isinstance(result["summary"], str)
    assert len(result["summary"]) > 0


@pytest.mark.asyncio
async def test_model_scan_execution():
    """Test model_scan queries Ollama and falls back gracefully."""
    tool_fn = create_model_scan()
    result = await tool_fn(ModelScanInput())

    assert "status" in result
    assert "models_count" in result
    assert "models" in result
    assert isinstance(result["models"], list)


@pytest.mark.asyncio
async def test_model_scan_with_mocked_ollama():
    """Test model_scan parsing when Ollama returns mock model tags."""
    mock_tags = {
        "models": [
            {
                "name": "qwen2.5-coder:7b",
                "size": 4700000000,
                "details": {
                    "parameter_size": "7B",
                    "quantization_level": "Q4_K_M",
                    "family": "qwen2",
                },
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_tags

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        tool_fn = create_model_scan()
        result = await tool_fn(ModelScanInput())

        assert result["models_count"] >= 1
        model_names = [m["name"] for m in result["models"]]
        assert "qwen2.5-coder:7b" in model_names


@pytest.mark.asyncio
async def test_security_diagnostics_rbac_denial():
    """Test security_diagnostics raises PermissionError for role without VIEW_SECURITY."""
    tool_fn = create_security_diagnostics()

    # viewer role lacks VIEW_SECURITY
    with pytest.raises(PermissionError) as exc_info:
        await tool_fn(SecurityDiagnosticsInput(), user_role=UserRole.VIEWER.value)

    assert "Permission denied" in str(exc_info.value)
    assert "VIEW_SECURITY" in str(exc_info.value)


@pytest.mark.asyncio
async def test_security_diagnostics_rbac_allowed():
    """Test security_diagnostics succeeds for role with VIEW_SECURITY (admin)."""
    tool_fn = create_security_diagnostics()

    result = await tool_fn(SecurityDiagnosticsInput(), user_role=UserRole.ADMIN.value)

    assert "overall_status" in result
    assert result["overall_status"] in ("PASS", "WARN", "FAIL")
    assert "total_checks" in result
    assert result["total_checks"] > 0
    assert "diagnostics" in result
    assert len(result["diagnostics"]) > 0
