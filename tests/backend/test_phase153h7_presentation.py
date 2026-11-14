import pytest
from backend.agent.engine import AgentEngine
from backend.tools.registry import ToolResult

SECRET = 'opaque-private-secret.bin'


@pytest.mark.parametrize('tool', ['hardware_status', 'model_scan', 'security_diagnostics'])
def test_diagnostics_metadata_not_released(tool):
    raw = {'summary': SECRET, 'telemetry': {'cpu': {'percent': 12, 'private': SECRET}},
           'diagnostics': [{'status': 'pass', 'details': SECRET}],
           'available_models': [{'name': SECRET}], 'nested': {'path': SECRET}}
    result = ToolResult(tool=tool, success=True, result=raw)
    for output in [AgentEngine._format_observation(tool, result),
                   AgentEngine._format_step_result_content(tool, raw),
                   AgentEngine._format_direct_tool_answer(tool, result),
                   AgentEngine._format_tool_result_summary(result)]:
        assert SECRET not in output


def test_code_output_without_provenance_is_not_released():
    raw = {'stdout': SECRET, 'exit_code': 0}
    result = ToolResult(tool='code_execution', success=True, result=raw)
    for output in [AgentEngine._format_observation('code_execution', result),
                   AgentEngine._format_step_result_content('code_execution', raw),
                   AgentEngine._format_direct_tool_answer('code_execution', result),
                   AgentEngine._format_tool_result_summary(result)]:
        assert SECRET not in output


def test_unknown_step_result_does_not_serialize_internal_metadata():
    assert SECRET not in AgentEngine._format_step_result_content('unknown', {'path': SECRET})


def test_calculator_presentation_retains_numeric_value_and_rejects_strings():
    good = ToolResult(tool='calculator', success=True, result={'result': 42, 'private': SECRET})
    assert '42' in AgentEngine._format_observation('calculator', good)
    assert '42' in AgentEngine._format_step_result_content('calculator', good.result)
    assert '42' in AgentEngine._format_direct_tool_answer('calculator', good)
    bad = ToolResult(tool='calculator', success=True, result={'result': SECRET})
    assert SECRET not in AgentEngine._format_observation('calculator', bad)


def test_hardware_presentation_preserves_bounded_numeric_metrics():
    raw = {'telemetry': {'cpu': {'percent': 12, 'private': SECRET}, 'ram': {'percent': 30}}}
    result = ToolResult(tool='hardware_status', success=True, result=raw)
    assert 'CPU 12%' in AgentEngine._format_direct_tool_answer('hardware_status', result)
    assert 'RAM 30%' in AgentEngine._format_observation('hardware_status', result)
