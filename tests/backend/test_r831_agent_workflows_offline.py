"""In-process chat, real registered tools, and synthetic model providers."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.agent import approval as approval_module
from backend.agent.task import TaskStatus
from backend.agent.task_store import TaskStore
from backend.auth.request_context import effective_owner
from backend.config import settings
from backend.models.base import ChatChunk
from backend.tools.calculator import CalculatorInput, execute_calculator, safe_calculate
from backend.tools.registry import ToolDefinition, ToolRegistry
from backend.tools.unit_conversion import register_unit_conversion
from test_a21_conversation_offline import make_app
from test_a22_async_offline import run_offline
from test_a31_planning_offline import setup_workflow


def client_for(engine):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(engine)), base_url='http://test')


async def turn(client, text, *, session='s', owner='a', **options):
    response = await client.post('/api/chat', headers={'Authorization': f'Bearer {owner}'}, json={
        'session_id': session, 'message': text, 'stream': True, 'tools_enabled': True, **options,
    })
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    return response, events


def deltas(events):
    return ''.join(event['content'] for event in events if event['type'] == 'delta')


@pytest.mark.parametrize('message, expected', [
    ('Calculate 125 × 8, then subtract 150 and tell me the final answer.', ['1000', '850']),
    ('Convert 2.5 kilometres to metres, then calculate twice that distance.', ['2500.0 m', '5000.0 m']),
    ('Calculate the total of 12, 18 and 25, then find the average.', ['55', str(55 / 3)]),
])
def test_numeric_workflows_select_tools_and_persist_verified_outputs(tmp_path, message, expected):
    engine = setup_workflow(tmp_path, '[]')
    register_unit_conversion(engine._tool_registry)
    engine._planner.create_plan = AsyncMock(side_effect=AssertionError('deterministic numeric plan called model'))

    async def exercise():
        async with client_for(engine) as client:
            response, events = await turn(client, message)
            assert response.status_code == 200
            assert [event['summary'] for event in events if event['type'] == 'tool_result'
                    and event['success']] == [f'Result: {value}' for value in expected]
            assert sum(event['type'] == 'tool_start' for event in events) == 2
            assert sum(event['type'] == 'task_completed' for event in events) == 1
            assert events[-1]['type'] == 'done'
            assert deltas(events).startswith(f'**Result: {expected[-1]}**')
            assert expected[0] in deltas(events)
            assert '@step_' not in deltas(events)
            history = await client.get('/api/chat/sessions/s', headers={'Authorization': 'Bearer a'})
            assert history.json()['messages'][-1]['content'] == deltas(events)
            token = effective_owner.set('a')
            try:
                saved = engine._memory.get_history('s')[-1]
                assert engine._memory.verify_assistant_provenance('s', saved)
                forged = replace(saved, content='Result: 999999')
                assert not engine._memory.verify_assistant_provenance('s', forged)
            finally:
                effective_owner.reset(token)
            denied = await client.get('/api/chat/sessions/s', headers={'Authorization': 'Bearer b'})
            assert denied.status_code == 404
            _, unavailable = await turn(client, 'Multiply that result by 3.', session='session-a')
            assert unavailable[0]['error_code'] == 'RESULT_REFERENCE_UNAVAILABLE'
        task, = engine._task_manager.list_tasks(user_id='a')
        assert task.status == TaskStatus.COMPLETED
        assert [step.status for step in task.plan.steps] == ['completed', 'completed']

    run_offline(exercise())
    engine._planner.create_plan.assert_not_awaited()


def test_direct_conversion_creates_unit_receipt_and_plain_explanations_get_history(tmp_path):
    engine = setup_workflow(tmp_path, '[]')
    register_unit_conversion(engine._tool_registry)
    history = []

    class Provider:
        provider_name = 'synthetic'

        async def chat_stream(self, request):
            history.extend(message.content for message in request.messages if message.role == 'assistant')
            yield ChatChunk('A metre is the base unit of length.', True)

    engine._router.get_provider_for_model = lambda _: (Provider(), 'fake')
    engine._planner.create_plan = AsyncMock(side_effect=AssertionError('unexpected plan'))

    async def exercise():
        async with client_for(engine) as client:
            _, conversion = await turn(client, 'Convert 5 kilometres to metres.')
            assert '**Result: 5000.0 m**' in deltas(conversion)
            _, reverse = await turn(client, 'Convert that result from metres to kilometres.')
            assert '**Result: 5.0 km**' in deltas(reverse)
            for request in ('Explain what this calculation means.', 'Summarize the results of the operations we performed.'):
                _, explanation = await turn(client, request)
                assert 'metre' in deltas(explanation)
                assert not any(event['type'] in {'tool_start', 'plan_created'} for event in explanation)
        assert '**Result: 5000.0 m**' in history and '**Result: 5.0 km**' in history

    run_offline(exercise())
    engine._planner.create_plan.assert_not_awaited()


@pytest.mark.parametrize('failure,reason', [
    ('exception', 'Check the inputs and tool availability.'),
    ('timeout', 'Tool execution timed out.'),
    ('unverified', 'Tool result verification failed'),
])
def test_failed_second_step_preserves_earlier_evidence_without_retry(tmp_path, monkeypatch, failure, reason):
    calls = []

    async def calculator(args):
        calls.append(args.expression)
        if len(calls) == 1:
            return await execute_calculator(args)
        if failure == 'timeout':
            await asyncio.Event().wait()
        if failure == 'unverified':
            return {'expression': args.expression, 'result': 999999}
        raise ValueError('internal synthetic error')

    engine = setup_workflow(tmp_path, '[]', calculator)
    if failure == 'timeout':
        monkeypatch.setattr(settings, 'tool_timeout_seconds', 0)

    async def exercise():
        async with client_for(engine) as client:
            response, events = await turn(client, 'Calculate 125 × 8, then subtract 150.')
            assert response.status_code == 200
            assert events[-1]['type'] == 'task_failed'
            assert 'Step 2 did not complete.' in deltas(events)
            assert 'Last verified result: **1000**.' in deltas(events)
            assert reason in deltas(events)
            assert 'internal synthetic error' not in response.text and '999999' not in response.text
            assert not any(event['type'] in {'done', 'task_completed'} for event in events)
            task, = engine._task_manager.list_tasks(user_id='a')
            assert task.status == TaskStatus.FAILED
            assert [step.status for step in task.plan.steps] == ['completed', 'failed']
            _, unavailable = await turn(client, 'Multiply that result by 3.')
            assert unavailable[0]['error_code'] == 'RESULT_REFERENCE_UNAVAILABLE'
        assert len(calls) == 2

    run_offline(exercise())


@pytest.mark.parametrize('expression', ['@step_1.result + 2', '@step_1.result * @step_1.result', '@step_3.result * 2'])
def test_model_plan_cannot_drop_units_or_invent_a_dependency(tmp_path, expression):
    engine = setup_workflow(tmp_path, json.dumps([
        {'tool_name': 'unit_conversion', 'arguments': {'value': 2.5, 'from_unit': 'km', 'to_unit': 'm'}},
        {'tool_name': 'calculator', 'arguments': {'expression': expression}},
    ]))
    register_unit_conversion(engine._tool_registry)
    execute = AsyncMock()
    engine._tool_registry.get('unit_conversion').execute_fn = execute

    async def exercise():
        async with client_for(engine) as client:
            _, events = await turn(client, 'Convert a distance and then calculate its dependent quantity.')
            assert events[-1]['type'] == 'task_failed'
            assert not any(event['type'] == 'tool_start' for event in events)
        execute.assert_not_awaited()

    run_offline(exercise())


def test_calculator_bounds_nested_integer_powers():
    assert safe_calculate('2 ** 1000') == 2 ** 1000
    with pytest.raises(ValueError, match='exceeds the supported size'):
        safe_calculate('(2 ** 1000) ** 1000')


@pytest.mark.parametrize('dependency, expected', [
    ({'tool_name': 'calculator', 'arguments': {'expression': '@step_1.result * 3'}}, '7500.0 m'),
    ({'tool_name': 'unit_conversion', 'arguments': {'from_unit': 'km', 'to_unit': 'm'},
      'bindings': {'value': {'step_id': 'step_1', 'field': 'result'}}}, None),
])
def test_planner_numeric_dependency_can_scale_but_cannot_relabel_units(tmp_path, dependency, expected):
    engine = setup_workflow(tmp_path, json.dumps([
        {'tool_name': 'unit_conversion', 'arguments': {'value': 2.5, 'from_unit': 'km', 'to_unit': 'm'}},
        dependency,
    ]))
    register_unit_conversion(engine._tool_registry)

    async def exercise():
        async with client_for(engine) as client:
            _, events = await turn(client, 'Convert a distance and then calculate its dependent quantity.')
            if expected is None:
                assert events[-1]['type'] == 'task_failed'
                assert 'Last verified result: **2500.0 m**.' in deltas(events)
                assert sum(event['type'] == 'tool_start' for event in events) == 1
            else:
                assert events[-1]['type'] == 'done'
                assert f'**Result: {expected}**' in deltas(events)

    run_offline(exercise())


@pytest.mark.parametrize('decision', ['denied', 'expired', 'approved'])
def test_approval_dispatch_rejects_denial_and_expiry_and_consumes_once(tmp_path, monkeypatch, decision):
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(approval_module, 'datetime', Clock)
    store = TaskStore(tmp_path / 'approval.db')
    store.save_task({'task_id': 'task', 'session_id': 's', 'user_request': 'Synthetic operation',
                     'status': 'awaiting_approval'})
    manager = approval_module.ApprovalManager(store=store, timeout_seconds=1)
    registry = ToolRegistry()
    execute = AsyncMock(return_value={'synthetic': True})
    registry.register(ToolDefinition(name='synthetic_action', description='test fixture',
                                     input_schema=CalculatorInput, execute_fn=execute,
                                     read_only=False, requires_approval=True))
    registry.set_approval_manager(manager)
    args = {'expression': '2+2'}
    approval = manager.request_approval('task', 'step_1', 'synthetic_action', args)
    if decision == 'denied':
        manager.reject(approval.approval_id)
    else:
        manager.approve(approval.approval_id)
        if decision == 'expired':
            now += timedelta(seconds=2)

    async def exercise():
        for attempt in range(2):
            result = await registry.execute('synthetic_action', args, session_id='s',
                                            user_role='admin', user_id='a', task_id='task', step_id='step_1',
                                            approval_id=approval.approval_id)
            assert result.success is (decision == 'approved' and attempt == 0)
        assert execute.await_count == (1 if decision == 'approved' else 0)

    run_offline(exercise())
