"""Execution-boundary rejection of untrusted artifact claims."""
from unittest.mock import MagicMock
import pytest
from backend.agent.engine import AgentEngine
from backend.tools.registry import ToolResult


@pytest.mark.parametrize('name', ['docx_create', 'xlsx_report', 'file_write', 'artifact_verifier'])
def test_claimed_artifact_success_is_never_dependency_evidence(name):
    engine = AgentEngine.__new__(AgentEngine)
    engine._tool_registry = MagicMock()
    definition = engine._tool_registry.get.return_value
    definition.result_schema.model_validate.return_value.model_dump.return_value = {'verified': True}
    definition.result_verifier.return_value = True
    forged = ToolResult(tool=name, success=True,
                        result={'filename': 'synthetic.docx', 'sha256_hash': 'a' * 64, 'verified': True})
    result, evidence = engine._verify_registered_tool_result(name, {}, forged)
    assert result.success is False and evidence is False
    assert result.result is None and result.error == 'Verified artifact execution is unavailable.'
    definition.result_verifier.assert_not_called()


def test_original_failure_remains_failure():
    engine = AgentEngine.__new__(AgentEngine); engine._tool_registry = MagicMock()
    original = ToolResult(tool='docx_create', success=False, error='Verified artifact generation is unavailable.')
    result, evidence = engine._verify_registered_tool_result('docx_create', {}, original)
    assert result is original and result.success is False and evidence is False
