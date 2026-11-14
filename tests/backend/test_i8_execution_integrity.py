"""Deterministic in-process handoff tests; no services or real sockets."""
import json
import pytest

from backend.auth.request_context import effective_owner, request_tools_enabled
from backend.agent.task import TaskStatus, TaskStateError
from backend.tools.calculator import CalculatorInput, execute_calculator
from backend.tools.registry import ToolDefinition
from test_a21_conversation_offline import immediate
from test_a31_planning_offline import setup_workflow
from backend.auth.request_context import dispatch_identity_resolver
from types import SimpleNamespace


@pytest.fixture(autouse=True)
def synthetic_dispatch_identity():
    token = dispatch_identity_resolver.set(lambda: SimpleNamespace(id='a', role='admin', clearance='L3', is_active=True))
    try:
        yield
    finally:
        dispatch_identity_resolver.reset(token)


PLAN = json.dumps([{'description': 'Add', 'tool_name': 'calculator',
                    'arguments': {'expression': '25 + 17'}}])


@pytest.mark.parametrize('change', ['replace', 'remove', 'mutate', 'disable_enable'])
def test_registration_change_after_validation_denies_dispatch(tmp_path, change):
    calls = []
    async def replacement(args):
        calls.append(args.expression)
        return await execute_calculator(args)
    engine = setup_workflow(tmp_path, PLAN)
    owner = effective_owner.set('a'); enabled = request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s', 'a', 'Be helpful.')
        async def run():
            events = []
            async for event in engine.run_agent_task('s', 'Calculate 25+17', user_role='admin', user_id='a'):
                events.append(event)
                if isinstance(event, dict) and event.get('type') == 'plan_created':
                    registry = engine._tool_registry
                    if change == 'replace':
                        registry.register(ToolDefinition('calculator', 'replacement', CalculatorInput, replacement))
                    elif change == 'remove':
                        registry.unregister('calculator')
                    elif change == 'disable_enable':
                        registry.disable('calculator')
                        registry.enable('calculator')
                    else:
                        registry.get('calculator').execute_fn = replacement
            return events
        events = immediate(run())
        assert not calls
        assert any(isinstance(e, dict) and e.get('type') == 'task_failed' for e in events)
        assert engine._task_manager.list_tasks(user_id='a')[0].status == TaskStatus.FAILED
    finally:
        request_tools_enabled.reset(enabled); effective_owner.reset(owner)


def test_registration_change_during_result_is_not_verified(tmp_path):
    engine = setup_workflow(tmp_path, PLAN)
    async def changing(args):
        result = await execute_calculator(args)
        engine._tool_registry.unregister('calculator')
        return result
    engine._tool_registry.get('calculator').execute_fn = changing
    owner = effective_owner.set('a'); enabled = request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s', 'a', 'Be helpful.')
        from test_a31_planning_offline import collect
        events = collect(engine)
        assert any(isinstance(e, dict) and e.get('type') == 'task_failed' for e in events)
        assert not any(isinstance(e, dict) and e.get('type') == 'task_completed' for e in events)
    finally:
        request_tools_enabled.reset(enabled); effective_owner.reset(owner)


def test_original_plan_mutation_does_not_change_execution(tmp_path):
    seen = []
    async def calculate(args):
        seen.append(args.expression)
        return await execute_calculator(args)
    engine = setup_workflow(tmp_path, PLAN, calculate)
    original_create = engine._planner.create_plan
    original = []
    async def create(*args, **kwargs):
        plan = await original_create(*args, **kwargs)
        original.append(plan)
        return plan
    engine._planner.create_plan = create
    owner = effective_owner.set('a'); enabled = request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s', 'a', 'Be helpful.')
        async def run():
            events = []
            async for event in engine.run_agent_task('s', 'Calculate 25+17', user_role='admin', user_id='a'):
                events.append(event)
                if isinstance(event, dict) and event.get('type') == 'plan_created':
                    original[0].steps[0].arguments['expression'] = '999 + 1'
                    original[0].steps[0].tool_name = 'unknown'
            return events
        events = immediate(run())
        assert seen == ['25 + 17']
        assert any(isinstance(e, dict) and e.get('type') == 'task_completed' for e in events)
    finally:
        request_tools_enabled.reset(enabled); effective_owner.reset(owner)


def test_terminal_transition_cannot_be_overwritten(tmp_path):
    engine = setup_workflow(tmp_path, PLAN)
    task = engine._task_manager.create_task('s', 'synthetic', user_id='a', user_role='admin')
    engine._task_manager.update_status(task.task_id, TaskStatus.CANCELLED)
    with pytest.raises(TaskStateError):
        engine._task_manager.update_status(task.task_id, TaskStatus.COMPLETED)
    assert engine._task_manager.get_task(task.task_id).status == TaskStatus.CANCELLED
