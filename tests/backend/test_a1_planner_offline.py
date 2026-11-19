"""Planner with in-memory provider; synchronous coroutine driver forbids I/O."""
from unittest.mock import AsyncMock, MagicMock
import pytest
from backend.agent.planner import AgentPlanner
from backend.agent.engine import AgentEngine
from backend.models.base import ChatResponse
from backend.tools.registry import ToolDefinition, ToolRegistry
from pydantic import BaseModel


def immediate(coro):
    try:
        try:
            coro.send(None)
        except StopIteration as done:
            return done.value
        raise AssertionError('unexpected asynchronous suspension')
    finally:
        coro.close()


def test_planner_calls_fake_provider_without_executing_tools():
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=ChatResponse(content='[{"description":"Write", "tool_name":"file_write", "arguments":{}}]', model='fake', provider='fake'))
    registry = MagicMock()
    registry.list_enabled_tools.return_value = []
    plan = immediate(AgentPlanner().create_plan('t', 'write a report', registry, provider, 'fake'))
    provider.chat.assert_awaited_once()
    registry.execute.assert_not_called()
    assert plan.steps[0].requires_approval


def test_planner_provider_failure_does_not_execute_tool():
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=RuntimeError('fake failure'))
    registry = MagicMock()
    registry.list_enabled_tools.return_value = []
    with pytest.raises(RuntimeError, match='fake failure'):
        immediate(AgentPlanner().create_plan('t', 'write a report', registry, provider, 'fake'))
    provider.chat.assert_awaited_once()
    registry.execute.assert_not_called()


@pytest.mark.parametrize('failure', [False, True])
def test_basic_chat_fake_provider(failure):
    engine = AgentEngine.__new__(AgentEngine)
    engine._ensure_session = MagicMock()
    engine._memory = MagicMock()
    engine._memory.stream_identity.return_value = ('owner', 'generation')
    engine._router = MagicMock()
    engine._agent_config = {}
    engine._retrieve_context = AsyncMock(return_value=[])
    engine._build_messages = MagicMock(return_value=[])
    provider = MagicMock(provider_name='fake')
    provider.provider_name = 'fake'
    provider.chat = AsyncMock(side_effect=RuntimeError('fake failure') if failure else None,
                              return_value=ChatResponse(content='Hello', model='fake', provider='fake'))
    engine._router.get_provider_for_model.return_value = (provider, 'fake')
    if failure:
        with pytest.raises(RuntimeError, match='fake failure'):
            immediate(engine.chat('s', 'hello'))
        engine._memory.record_plain_response.assert_not_called()
    else:
        assert immediate(engine.chat('s', 'hello')) == ('Hello', [])
        engine._memory.record_plain_response.assert_called_once_with(
            's', ('owner', 'generation'), 'Hello', status=None, producer=None, access_provenance=None,
        )
    engine._memory.add_assistant_message.assert_not_called()
    provider.chat.assert_awaited_once()


def test_real_registry_denies_unapproved_tool():
    execute = AsyncMock()
    registry = ToolRegistry()
    registry.register(ToolDefinition(name='fake_write', description='test', input_schema=BaseModel,
                                     execute_fn=execute, read_only=False, requires_approval=True))
    result = immediate(registry.execute('fake_write', {}, user_role='admin'))
    assert not result.success and 'approval' in result.error.lower()
    execute.assert_not_awaited()
