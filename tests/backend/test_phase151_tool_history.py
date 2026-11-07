from unittest.mock import AsyncMock, MagicMock
import pytest
from backend.agent.engine import AgentEngine
from backend.agent.memory import ConversationMemory, safe_tool_history
from backend.auth.request_context import effective_owner
from backend.config import Settings
from backend.models.base import ChatChunk
from backend.tools.registry import ToolResult


@pytest.mark.asyncio
async def test_actual_tool_stream_persists_only_safe_status_on_restart(tmp_path):
    token = effective_owner.set('user-a')
    try:
        path = tmp_path / 'memory.json'
        memory = ConversationMemory(path)
        router, provider, tools = MagicMock(), MagicMock(), MagicMock()
        router.get_provider_for_model.return_value = (provider, 'synthetic')
        tools.list_enabled_tools.return_value = ['file_read']
        tools.format_tools_for_prompt.return_value = 'tools'
        tools.execute = AsyncMock(return_value=ToolResult(tool='file_read', success=False, error='secret-do-not-persist'))
        async def stream(_):
            yield ChatChunk(delta='file_read(filename="private-path")', done=True)
        provider.chat_stream = stream
        engine = AgentEngine(settings=Settings(), router=router, memory=memory, tool_registry=tools)
        engine._parse_tool_call = MagicMock(side_effect=[{'name':'file_read','arguments':{'filename':'private-path'}}, None])
        engine._clean_reasoning_response = MagicMock(return_value='Synthetic answer')
        items = [item async for item in engine.chat_stream_with_tools('s', 'Read a file', user_role='admin')]
        assert any(isinstance(item, dict) and item.get('type') == 'tool_result' for item in items)
        messages = ConversationMemory(path).get_history('s')
        saved = messages[-1].extra_fields['tool_events_v1']
        assert len(saved) == 1 and saved[0]['tool'] == 'file_read' and saved[0]['success'] is False
        assert 'private-path' not in str(saved) and 'secret-do-not-persist' not in str(saved)
        assert len(saved[0]['invocation_id']) == 36
        other = effective_owner.set('user-b')
        try:
            with pytest.raises(PermissionError):
                ConversationMemory(path).get_history('s')
        finally:
            effective_owner.reset(other)
    finally:
        effective_owner.reset(token)


def test_tool_history_allowlist_rejects_untrusted_payloads():
    safe = {'tool':'calculator','success':True,'invocation_id':'12345678-1234-1234-1234-123456789abc', 'summary':'sensitive'}
    result = safe_tool_history([safe, {'tool':'../secret','success':True,'invocation_id':safe['invocation_id']}])
    assert len(result) == 1 and 'summary' not in result[0]
    assert len(safe_tool_history([safe] * 40)) == 32
