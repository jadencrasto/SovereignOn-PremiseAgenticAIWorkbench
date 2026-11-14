from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from backend.agent.engine import AgentEngine
from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner
from backend.config import Settings
from backend.models.base import ChatChunk
from backend.tools.registry import ToolResult

@pytest.mark.asyncio
@pytest.mark.parametrize('tools_enabled', [True, False])
async def test_multimodal_history_records_only_permitted_dispatch(tmp_path, tools_enabled):
    token = effective_owner.set('user-a')
    try:
        memory = ConversationMemory(tmp_path / 'memory.json')
        router, provider, registry = MagicMock(), MagicMock(), MagicMock()
        router.resolve_vision_model.return_value = (provider, 'vision')
        router.resolve_chat_model.return_value = (provider, 'reasoner')
        registry.list_enabled_tools.return_value = ['file_read']
        registry.format_tools_for_prompt.return_value = 'tools'
        registry.execute = AsyncMock(return_value=ToolResult(tool='file_read', success=True, result={'content':'private'}))
        async def stream(_):
            yield ChatChunk(delta='synthetic output', done=True)
        provider.chat_stream = stream
        engine = AgentEngine(Settings(), router, memory, tool_registry=registry)
        engine._parse_tool_call = MagicMock(side_effect=[{'name':'file_read', 'arguments':{'filename':'secret'}}, None])
        engine._extract_pre_tool_text = MagicMock(return_value='')
        with patch('backend.multimodal.service.MultimodalService.analyze_image', new_callable=AsyncMock, return_value='synthetic image'), patch('backend.multimodal.grounding.VisualGroundingVerifier.verify', return_value=MagicMock(guarded_text='synthetic answer')):
            _ = [item async for item in engine.chat_stream_with_tools_multimodal('s', 'describe', 'fixture', user_role='admin', tools_enabled=tools_enabled)]
        message = ConversationMemory(tmp_path / 'memory.json').get_history('s')[-1]
        events = message.extra_fields.get('tool_events_v1', [])
        if tools_enabled:
            assert len(events) == 1 and events[0]['tool'] == 'file_read' and events[0]['success'] is True
            assert 'secret' not in str(events) and 'private' not in str(events)
            registry.execute.assert_awaited_once()
        else:
            assert events == []
            registry.execute.assert_not_awaited()
    finally:
        effective_owner.reset(token)

@pytest.mark.asyncio
async def test_planned_task_saves_tool_status_at_final_response(tmp_path):
    from backend.agent.task_store import TaskStore
    from backend.agent.task import TaskManager
    from backend.agent.planner import AgentPlan, PlanStep
    token = effective_owner.set('user-a')
    try:
        memory = ConversationMemory(tmp_path / 'memory.json')
        router, registry, planner, validator = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        router.get_provider_for_model.return_value = (MagicMock(), 'synthetic')
        registry.list_enabled_tools.return_value = ['calculator']
        registry.execute = AsyncMock(return_value=ToolResult(tool='calculator', success=True, result={'value': 4}))
        async def create_plan(task_id, objective, **kwargs):
            return AgentPlan(task_id=task_id, objective=objective, steps=[PlanStep(id='s1', description='calculate', tool_name='calculator', arguments={'expression':'2+2'})])
        planner.create_plan = create_plan
        validator.validate.return_value = []
        manager = TaskManager(TaskStore(tmp_path / 'tasks.db'))
        engine = AgentEngine(Settings(), router, memory, tool_registry=registry)
        engine.set_task_manager(manager)
        engine.set_planner(planner)
        engine.set_plan_validator(validator)
        engine._is_standalone_non_rag_task = MagicMock(return_value=True)
        engine._synthesize_task_completion_response = MagicMock(return_value='Synthetic completion')
        items = [item async for item in engine.run_agent_task('s', 'compute', user_role='admin', user_id='user-a')]
        assert any(isinstance(item, dict) and item.get('type') == 'tool_result' for item in items)
        saved = ConversationMemory(tmp_path / 'memory.json').get_history('s')[-1]
        assert saved.extra_fields['tool_events_v1'][0]['tool'] == 'calculator'
        assert 'expression' not in str(saved.extra_fields['tool_events_v1'])
    finally:
        effective_owner.reset(token)
