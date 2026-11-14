import pytest
from backend.security.artifact_presentation import public_planner_event


def test_planner_uses_safe_operation_labels_without_model_text():
    result = public_planner_event({'type': 'plan_created', 'task_id': 'task_1', 'plan': {
        'objective': 'data/sandbox/secret.docx', 'steps': [
            {'id': 's1', 'tool_name': 'document_search', 'status': 'pending', 'description': 'data/sandbox/secret'},
            {'id': 's2', 'tool_name': 'docx_create', 'status': 'pending', 'description': 'C:\\secret'}]}})
    descriptions = [step['description'] for step in result['plan']['steps']]
    assert descriptions == ['Search documents', 'Create Word document']
    assert 'Search documents' in result['plan']['objective']
    assert 'data/sandbox' not in str(result) and 'C:\\' not in str(result)


def test_unknown_planner_variant_and_tool_fail_closed():
    assert public_planner_event({'type': 'strange', 'result': {'path': 'secret'}}) == {'type': 'error', 'content': 'Event unavailable'}
    result = public_planner_event({'type': 'plan_created', 'plan': {'steps': [
        {'id': 's', 'tool_name': 'unrecognized_tool', 'description': 'secret'}]}})
    assert result['plan']['steps'][0]['description'] == 'Task step'
    assert 'secret' not in str(result)


def test_tool_event_fields_do_not_forward_untrusted_nested_values():
    from backend.security.artifact_presentation import public_agent_status, public_tool_arguments, public_tool_summary
    assert public_tool_arguments('unrecognized', {'nested': {'path': 'data/sandbox/private'}}) == {}
    assert 'data/sandbox' not in public_tool_summary('unrecognized', 'data/sandbox/private', True)
    assert public_tool_arguments('calculator', {'expression': '2 + 2'}) == {'expression': '2 + 2'}
    assert public_tool_summary('calculator', 'Result: 4', True) == 'Result: 4'
    assert public_agent_status('data/sandbox/private') == 'thinking'
    assert public_agent_status('analyzing_image') == 'analyzing_image'
