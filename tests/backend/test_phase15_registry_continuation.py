from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from backend.tools.registry import ToolDefinition, ToolRegistry


class Args(BaseModel):
    value: str


@pytest.mark.asyncio
async def test_registry_denies_disabled_request_even_when_role_is_valid():
    from backend.auth.request_context import request_tools_enabled
    registry = ToolRegistry()
    action = AsyncMock(return_value='ok')
    registry.register(ToolDefinition(name='read', description='read', input_schema=Args, execute_fn=action))
    result = await registry.execute('read', {'value': 'x'}, user_role='admin', tools_enabled=False)
    assert not result.success
    token = request_tools_enabled.set(False)
    try:
        assert not (await registry.execute('read', {'value': 'x'}, user_role='admin')).success
    finally:
        request_tools_enabled.reset(token)
    action.assert_not_awaited()


@pytest.mark.asyncio
async def test_registry_requires_role_and_bound_approval():
    registry = ToolRegistry()
    action = AsyncMock(return_value={'ok': True})
    registry.register(ToolDefinition(name='write', description='write', input_schema=Args,
                                     execute_fn=action, read_only=False, requires_approval=True))
    assert not (await registry.execute('write', {'value': 'one'}, user_role='admin')).success
    manager = MagicMock()
    manager.verify_approval_for_execution.return_value = True
    registry.set_approval_manager(manager)
    params = dict(approval_id='a', task_id='t', step_id='s')
    assert not (await registry.execute('write', {'value': 'one'}, **params)).success
    assert not (await registry.execute('write', {'value': 'one'}, user_role='viewer', **params)).success
    manager.verify_approval_for_execution.return_value = False
    assert not (await registry.execute('write', {'value': 'one'}, user_role='admin', **params)).success
    manager.verify_approval_for_execution.return_value = True
    assert (await registry.execute('write', {'value': 'one'}, user_role='admin', **params)).success
    assert action.await_count == 1


@pytest.mark.asyncio
async def test_registry_checks_real_approval_binding(tmp_path):
    from backend.agent.approval import ApprovalManager
    from backend.agent.task_store import TaskStore

    store = TaskStore(tmp_path / 'tasks.db')
    store.save_task({'task_id': 't', 'session_id': 's', 'user_request': 'fixture', 'status': 'awaiting_approval'})
    manager = ApprovalManager(store, timeout_seconds=300)
    registry = ToolRegistry()
    action = AsyncMock(return_value={'ok': True})
    registry.register(ToolDefinition(name='write', description='write', input_schema=Args,
                                     execute_fn=action, read_only=False, requires_approval=True))
    registry.set_approval_manager(manager)
    approval = manager.request_approval('t', 'step', 'write', {'value': 'approved'})
    manager.approve(approval.approval_id)
    context = dict(user_role='admin', task_id='t', step_id='step', approval_id=approval.approval_id)
    assert not (await registry.execute('write', {'value': 'changed'}, **context)).success
    assert not (await registry.execute('write', {'value': 'approved'}, **{**context, 'user_role': 'viewer'})).success
    assert (await registry.execute('write', {'value': 'approved'}, **context)).success
    assert not (await registry.execute('write', {'value': 'approved'}, **context)).success
    assert action.await_count == 1


@pytest.mark.asyncio
async def test_concurrent_dispatch_consumes_approval_once(tmp_path):
    import asyncio
    from backend.agent.approval import ApprovalManager
    from backend.agent.task_store import TaskStore

    store = TaskStore(tmp_path / 'tasks.db')
    store.save_task({'task_id': 't', 'session_id': 's', 'user_request': 'fixture', 'status': 'awaiting_approval'})
    manager = ApprovalManager(store, timeout_seconds=300)
    registry = ToolRegistry()
    action = AsyncMock(return_value={'ok': True})
    registry.register(ToolDefinition(name='write', description='write', input_schema=Args,
                                     execute_fn=action, read_only=False, requires_approval=True))
    registry.set_approval_manager(manager)
    approval = manager.request_approval('t', 'step', 'write', {'value': 'approved'})
    manager.approve(approval.approval_id)
    context = dict(user_role='admin', task_id='t', step_id='step', approval_id=approval.approval_id)
    results = await asyncio.gather(*(registry.execute('write', {'value': 'approved'}, **context) for _ in range(2)))
    assert sum(result.success for result in results) == 1
    assert action.await_count == 1
