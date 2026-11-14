import pytest

from backend.agent.engine import AgentEngine
from backend.tools.registry import ToolResult


@pytest.mark.parametrize('tool', ['file_write', 'docx_create', 'xlsx_report', 'artifact_verifier'])
def test_artifact_results_do_not_expose_storage_to_model_or_direct_answer(tool):
    filename = 'a' * 32 + '.docx'
    raw = {'artifact_id': 'a' * 32, 'display_name': 'report.docx',
           'filename': filename, 'created_path': 'data/sandbox/' + filename,
           'nested': {'path': 'private/storage'}}
    result = ToolResult(tool=tool, success=True, result=raw)
    outputs = [AgentEngine._format_observation(tool, result),
               AgentEngine._format_step_result_content(tool, raw),
               AgentEngine._format_direct_tool_answer(tool, result)]
    for index, output in enumerate(outputs):
        assert filename not in output and 'data/sandbox' not in output and 'private/storage' not in output
        if tool != 'artifact_verifier' or index != 2:
            assert 'report.docx' in output


def test_artifact_error_does_not_expose_storage_to_model():
    result = ToolResult(tool='docx_create', success=False, error='failure at C:\\private\\secret')
    assert 'private' not in AgentEngine._format_observation('docx_create', result)
    assert 'private' not in AgentEngine._format_direct_tool_answer('docx_create', result)


@pytest.mark.asyncio
async def test_planner_sse_does_not_serialize_nested_artifact_storage(tmp_path):
    from backend.agent.memory import ConversationMemory
    from backend.agent.conversation_registry import ConversationRegistry
    from backend.api.chat import _stream_sse_with_planning
    from backend.auth.request_context import effective_owner
    token = effective_owner.set('a')
    try:
        registry = ConversationRegistry(tmp_path / 'tasks.db')
        memory = ConversationMemory(tmp_path / 'memory.json', security_registry=registry)
        memory.create_session(session_id='s')
        class Engine:
            _memory = memory
            async def run_agent_task(self, *args, **kwargs):
                yield {'type': 'plan_step', 'task_id': 'task_1', 'step_id': 'step_1',
                       'status': 'completed', 'result': {'nested': {'created_path': 'data/sandbox/private'}}}
        events = [e async for e in _stream_sse_with_planning(Engine(), 's', 'synthetic', None, 'test')]
        assert any('plan_step' in event for event in events)
        assert all('data/sandbox' not in event for event in events)
    finally:
        effective_owner.reset(token)


def test_planner_reasoning_does_not_replay_artifact_arguments_or_errors(tmp_path):
    from unittest.mock import MagicMock
    from backend.agent.memory import ConversationMemory
    from backend.auth.request_context import effective_owner
    from backend.config import Settings
    token = effective_owner.set('a')
    try:
        memory = ConversationMemory(tmp_path / 'memory.json')
        memory.create_session(session_id='s')
        engine = AgentEngine(Settings(), MagicMock(), memory)
        steps = [{'tool': 'docx_create', 'success': False, 'arguments': {'path': 'data/sandbox/private'},
                  'error': 'data/sandbox/private'}]
        messages = engine._build_task_reasoning_messages('s', 'synthetic', steps, [])
        assert all('data/sandbox/private' not in message.content for message in messages)
    finally:
        effective_owner.reset(token)


def test_planner_projection_preserves_structural_fields_and_omits_nested_data():
    from backend.security.artifact_presentation import public_planner_event
    event = {'type': 'plan_created', 'task_id': 'task_1', 'result': {'path': 'data/sandbox/private'},
             'plan': {'objective': 'data/sandbox/private', 'steps': [
                 {'id': 'step_1', 'tool_name': 'docx_create', 'status': 'pending',
                  'requires_approval': True, 'arguments': {'filename': 'data/sandbox/private'}}]}}
    public = public_planner_event(event)
    assert public['task_id'] == 'task_1'
    assert public['plan']['steps'][0]['tool_name'] == 'docx_create'
    assert public['plan']['steps'][0]['requires_approval'] is True
    assert 'data/sandbox/private' not in str(public)


def test_artifact_presentation_keeps_only_valid_public_fields():
    from backend.security.artifact_presentation import artifact_presentation
    public = artifact_presentation('docx_create', {'artifact_id': 'a' * 32, 'display_name': 'report.docx',
                'filename': 'a' * 32 + '.docx', 'nested': {'path': 'data/sandbox/private'}})
    assert public == {'artifact_type': 'docx', 'artifact_id': 'a' * 32, 'display_name': 'report.docx'}


def test_persisted_legacy_artifact_result_is_sanitized_before_reasoning(tmp_path):
    import json
    from backend.agent.task_store import TaskStore
    path = tmp_path / 'tasks.db'
    store = TaskStore(path)
    raw = {'artifact_id': 'a' * 32, 'display_name': 'report.docx',
           'created_path': 'data/sandbox/private.docx', 'nested': {'filename': 'private.docx'}}
    store.save_task({'task_id': 't', 'session_id': 's', 'user_request': 'synthetic',
                     'status': 'completed', 'result': json.dumps(raw), 'user_id': 'a'})
    recovered = json.loads(TaskStore(path).get_task('t')['result'])
    rendered = AgentEngine._format_step_result_content('docx_create', recovered)
    assert 'report.docx' in rendered
    assert 'data/sandbox' not in rendered and 'private.docx' not in rendered
