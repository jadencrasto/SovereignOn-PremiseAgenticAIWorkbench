from backend.agent.approval import ApprovalManager
from backend.agent.task import TaskManager, TaskStatus
from backend.agent.task_store import TaskStore
from backend.auth.request_context import effective_owner


def test_task_update_preserves_approval_and_events(tmp_path):
    token = effective_owner.set('a')
    try:
        store = TaskStore(tmp_path / 'tasks.db')
        manager = TaskManager(store)
        task = manager.create_task('s', 'synthetic', user_id='a')
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        approval = ApprovalManager(store).request_approval(task.task_id, 'step', 'calculator', {'expression':'1+1'})
        manager.update_status(task.task_id, TaskStatus.EXECUTING)
        assert store.get_approval(approval.approval_id)['status'] == 'pending'
        assert store.list_events_for_task(task.task_id)
        assert store.get_task_security_provenance(task.task_id)['verification_state'] == 'unverified'
        assert store.get_task(task.task_id)['status'] == 'executing'
    finally:
        effective_owner.reset(token)


async def _collect(async_iter):
    return [item async for item in async_iter]


import pytest
from unittest.mock import MagicMock
from backend.agent.engine import AgentEngine
from backend.agent.memory import ConversationMemory
from backend.agent.planner import AgentPlan, PlanStep
from backend.config import Settings


@pytest.mark.asyncio
async def test_mutated_unverified_task_cannot_resume_or_consume_approval(tmp_path):
    token = effective_owner.set('a')
    try:
        store = TaskStore(tmp_path / 'tasks.db')
        manager = TaskManager(store)
        task = manager.create_task('s', 'synthetic', user_id='a', user_role='operator')
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        plan = AgentPlan(task_id=task.task_id, objective='synthetic', steps=[PlanStep(
            id='step', description='synthetic', tool_name='calculator',
            arguments={'expression':'1+1'}, requires_approval=True)])
        manager.set_plan(task.task_id, plan)
        manager.update_step_status(task.task_id, 'step', 'awaiting_approval')
        manager.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)
        approvals = ApprovalManager(store)
        approval = approvals.request_approval(task.task_id, 'step', 'calculator', {'expression':'1+1'})
        plan.steps[0].arguments = {'expression':'2+2'}
        manager.set_plan(task.task_id, plan)
        engine = AgentEngine(Settings(), MagicMock(), ConversationMemory(tmp_path/'memory.json'))
        engine._task_manager = manager
        engine._approval_manager = approvals
        engine._tool_registry = MagicMock()
        result = await _collect(engine.resume_agent_task(task.task_id, approval.approval_id, True,
                                                        user_role='operator', user_id='a'))
        assert result[0]['type'] == 'error'
        assert store.get_approval(approval.approval_id)['status'] == 'pending'
        assert store.get_task_security_provenance(task.task_id)['verification_state'] == 'unverified'
        engine._tool_registry.execute.assert_not_called()
    finally:
        effective_owner.reset(token)
