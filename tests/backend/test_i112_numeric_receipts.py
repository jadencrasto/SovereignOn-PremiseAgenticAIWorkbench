import json

import httpx
import pytest

from backend.tools.calculator import execute_calculator
from backend.tools.unit_conversion import register_unit_conversion
from test_a21_conversation_offline import make_app
from test_a22_async_offline import run_offline
from test_a31_planning_offline import setup_workflow


def test_production_route_chains_verified_results_and_preserves_units(tmp_path):
    engine = setup_workflow(tmp_path, '[]')
    register_unit_conversion(engine._tool_registry)
    app = make_app(engine)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            async def turn(message, session='s', owner='a'):
                response = await client.post('/api/chat', headers={'Authorization': f'Bearer {owner}'},
                    json={'session_id': session, 'message': message, 'stream': True, 'tools_enabled': True})
                return response, [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]

            for message, result in [
                ('What is 25 + 17?', 'Result: 42'),
                ('Multiply that result by 3.', 'Result: 126'),
                ('Add 5 to that result.', 'Result: 131'),
                ('Subtract 5 from the previous result.', 'Result: 126'),
                ('Divide that result by 2.', 'Result: 63.0'),
                ('Convert that result from kilometres to metres.', 'Result: 63000.0 m'),
                ('Convert that result from metres to kilometres.', 'Result: 63.0 km'),
            ]:
                response, events = await turn(message)
                assert response.status_code == 200
                assert [e['summary'] for e in events if e['type'] == 'tool_result' and e['success']] == [result]
                assert sum(e['type'] == 'task_completed' for e in events) == 1
                assert events[-1]['type'] == 'done'

            from dataclasses import replace
            from backend.auth.request_context import effective_owner
            receipt = engine._memory._numeric_receipts['s']
            token = effective_owner.set('a')
            try:
                user_turn = engine._memory.get_history('s')[-2]
                engine._memory.remember_numeric_receipt('s', replace(receipt, value=999), user_turn.id)
                assert engine._memory.numeric_receipt('s') is receipt
            finally:
                effective_owner.reset(token)
            _, events = await turn('Convert that result from metres to kilometres.')
            assert [e['type'] for e in events] == ['error']
            _, events = await turn('add 5')
            assert [(e['type'], e['error_code']) for e in events] == [('error', 'RESULT_REFERENCE_UNAVAILABLE')]
            _, events = await turn('Multiply that result by 3.', session='session-a')
            assert [e['type'] for e in events] == ['error']
            response, _ = await turn('Multiply that result by 3.', owner='b')
            assert response.status_code == 404
            restarted = setup_workflow(tmp_path, '[]')
            app.dependency_overrides.update(make_app(restarted).dependency_overrides)
            _, events = await turn('Convert that result from kilometres to metres.')
            assert [e['type'] for e in events] == ['error']

    run_offline(exercise())


@pytest.mark.parametrize('case', ['failed', 'ambiguous', 'intervening', 'revoked', 'deleted', 'expired'])
def test_ineligible_result_never_dispatches_followup(tmp_path, case):
    calls = []

    async def calculate(args):
        calls.append(args.expression)
        result = await execute_calculator(args)
        if case == 'failed':
            result['result'] += 1
        return result

    plan = json.dumps([
        {'description': 'Add', 'tool_name': 'calculator', 'arguments': {'expression': '25 + 17'}},
        {'description': 'Multiply', 'tool_name': 'calculator', 'arguments': {'expression': '@step_1.result * 3'}},
    ])
    engine = setup_workflow(tmp_path, plan, calculate)
    app = make_app(engine)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            async def turn(message):
                return await client.post('/api/chat', json={'session_id': 's', 'message': message, 'stream': True})
            await turn('Calculate 25 + 17, then multiply the result by 3.' if case == 'ambiguous' else 'What is 25 + 17?')
            assert len(calls) == (2 if case == 'ambiguous' else 1)
            if case == 'intervening':
                from backend.auth.request_context import effective_owner
                token = effective_owner.set('a')
                try:
                    engine._memory.add_assistant_message('s', 'The verified result was 999')
                finally:
                    effective_owner.reset(token)
            if case == 'revoked':
                # Keep the normal request authentication. Revoke only at dispatch.
                from backend.auth.request_context import dispatch_identity_resolver

                original = engine._dispatch_tool

                async def dispatch(*args, **kwargs):
                    token = dispatch_identity_resolver.set(lambda: None)
                    try:
                        return await original(*args, **kwargs)
                    finally:
                        dispatch_identity_resolver.reset(token)

                engine._dispatch_tool = dispatch
            if case == 'deleted':
                assert (await client.delete('/api/chat/sessions/s')).status_code == 200
            if case == 'expired':
                from unittest.mock import patch
                from backend.agent import memory
                expires_at = engine._memory._numeric_receipts['s'].expires_at
                with patch.object(memory, 'time') as clock:
                    clock.monotonic.return_value = expires_at
                    response = await turn('Multiply that result by 3.')
                assert 'RESULT_REFERENCE_UNAVAILABLE' in response.text
                assert len(calls) == 1
                return
            before = list(calls)
            response = await turn('Multiply that result by 3.')
            assert calls == before
            assert 'task_completed' not in response.text
            assert ('task_failed' if case == 'revoked' else 'RESULT_REFERENCE_UNAVAILABLE') in response.text or (
                case == 'deleted' and response.status_code == 404)

    run_offline(exercise())


def test_interrupted_execution_does_not_publish_intermediate_result(tmp_path):
    from types import SimpleNamespace
    from backend.agent.task import TaskStatus
    from backend.auth.request_context import dispatch_identity_resolver, effective_owner
    from test_a21_conversation_offline import immediate

    engine = setup_workflow(tmp_path, '[]')
    make_app(engine)
    owner = effective_owner.set('a')
    identity = dispatch_identity_resolver.set(lambda: SimpleNamespace(
        id='a', is_active=True, role='admin', clearance=None,
    ))
    try:
        async def exercise():
            stream = engine.run_agent_task('s', 'What is 25 + 17?', user_role='admin',
                                           user_id='a', numeric_expression='25 + 17')
            async for event in stream:
                if isinstance(event, dict) and event['type'] == 'task_started':
                    task_id = event['task_id']
                if isinstance(event, dict) and event['type'] == 'tool_result':
                    assert event['success']
                    break
            await stream.aclose()
            assert engine._task_manager.get_task(task_id).status == TaskStatus.FAILED_INTERRUPTED
            assert engine._memory.numeric_receipt('s') is None

        immediate(exercise())
    finally:
        dispatch_identity_resolver.reset(identity)
        effective_owner.reset(owner)


def test_conversation_revocation_during_receipt_lookup_fails_closed(tmp_path, monkeypatch):
    engine = setup_workflow(tmp_path, '[]')
    app = make_app(engine)
    def unavailable(_session_id):
        raise PermissionError('synthetic revocation')
    monkeypatch.setattr(engine._memory, 'numeric_receipt', unavailable)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/chat', headers={'Authorization': 'Bearer a'},
                json={'session_id': 's', 'message': 'Multiply that result by 3.', 'stream': True})
            assert response.status_code == 404
            assert 'synthetic revocation' not in response.text
            assert not engine._task_manager.list_tasks(user_id='a')
    run_offline(exercise())
