from backend.agent.engine import AgentEngine
from backend.tools.registry import ToolResult


def test_artifact_tool_summary_does_not_expose_storage_filename():
    result = ToolResult(tool='docx_create', success=True, result={
        'artifact_id': 'a' * 32, 'display_name': 'report.docx',
        'filename': 'a' * 32 + '.docx', 'created_path': 'data/sandbox/' + 'a' * 32 + '.docx',
        'nested': {'path': 'data/sandbox/private'},
    })
    summary = AgentEngine._format_tool_result_summary(result)
    assert 'report.docx' in summary
    assert 'data/sandbox' not in summary and 'a' * 32 + '.docx' not in summary


def test_artifact_summary_rejects_path_shaped_name_and_error_details():
    result = ToolResult(tool='file_write', success=True, result={'display_name': '../secret', 'filename': 'secret'})
    assert AgentEngine._format_tool_result_summary(result) == 'Artifact created'
    failed = ToolResult(tool='file_write', success=False, error='private/path')
    assert AgentEngine._format_tool_result_summary(failed) == 'Artifact creation failed'


def test_non_artifact_summary_preserves_normal_behavior():
    result = ToolResult(tool='calculator', success=True, result={'result': 4})
    assert AgentEngine._format_tool_result_summary(result) == 'Result: 4'
