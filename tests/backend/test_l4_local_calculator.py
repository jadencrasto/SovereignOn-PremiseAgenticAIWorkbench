"""Local ASGI acceptance with the real registry, calculator and transcript."""

import asyncio
import json
import sqlite3
from contextlib import closing
from uuid import uuid4

import httpx
import pytest

from backend.health import routes as health
from backend.models.base import ChatChunk, ChatResponse
from backend.tools.calculator import CalculatorInput, execute_calculator
from backend.tools.registry import ToolDefinition, ToolRegistry, ToolResult
from test_a22_async_offline import run_offline
from test_l2_local_turns import api_client, cancel_path, history_path, login, new_turn, parse_events, workbench
from test_l3_durable_history import durable_environment


def record_calls(app, monkeypatch):
    registry = app.state.engine._tool_registry
    dispatch = registry.execute
    calls = []

    async def execute(name, arguments, **kwargs):
        calls.append((name, arguments, kwargs.get('step_id')))
        return await dispatch(name, arguments, **kwargs)

    monkeypatch.setattr(registry, 'execute', execute)
    return registry, dispatch, calls


def test_direct_and_automatic_verified_calculations(workbench, capsys, monkeypatch):
    requests = []

    async def plain(request):
        requests.append(request)
        yield ChatChunk('A conversational answer.', True)

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            monkeypatch.setattr(workbench.state.model_router.get_provider('ollama'), 'chat_stream', plain)
            _, _, calls = record_calls(workbench, monkeypatch)
            greeting = await new_turn(client, message='Hello!')
            assert [event['type'] for event in parse_events(await client.post('/api/chat', json=greeting))] == [
                'accepted', 'delta', 'delta', 'done',
            ]
            assert len(requests) == 1 and calls == []
            single = {**greeting, 'turn_id': str(uuid4()), 'message': 'What is 25 + 17?'}
            reply = await client.post('/api/chat', json={**single, 'stream': False})
            assert reply.status_code == 200 and '42' in reply.json()['message']['content']
            chain = {**greeting, 'turn_id': str(uuid4()),
                     'message': 'Calculate 25 + 17, then multiply the result by 3.'}
            events = parse_events(await client.post('/api/chat', json=chain))
            assert [event['type'] for event in events] == [
                'accepted', 'agent_status', 'agent_status', 'tool_start', 'tool_result',
                'tool_start', 'tool_result', 'delta', 'done',
            ]
            assert '**Result: 126**' in events[-2]['content']
            assert [args['expression'] for _, args, _ in calls] == ['25 + 17', '25 + 17', '(42) * (3)']
            assert len(requests) == 1
            follow_up = {**greeting, 'turn_id': str(uuid4()), 'message': 'Can you explain the last answer?'}
            assert parse_events(await client.post('/api/chat', json=follow_up))[-1]['type'] == 'done'
            assert any('**Result: 126**' in item.content for item in requests[-1].messages)
            history = (await client.get(history_path(chain))).json()['messages']
            assert [event['invocation_id'] for event in history[-3]['tool_events']] == [call[2] for call in calls[-2:]]
            plain_arithmetic = {**single, 'turn_id': str(uuid4())}
            assert parse_events(await client.post('/api/chat', json={**plain_arithmetic, 'tools_enabled': False}))[-1]['type'] == 'done'
            assert len(requests) == 3 and len(calls) == 3
            assert (await client.post('/api/chat', json=plain_arithmetic)).status_code == 409
            assert (await client.get(history_path(chain))).json()['messages'][-2]['turn']['tools_enabled'] is False
            with closing(sqlite3.connect(workbench.state.settings.tasks_dir / 'tool-audit.sqlite3')) as conn:
                rows = conn.execute('SELECT step_id, metadata_json FROM audit_events WHERE tool=?', ('calculator',)).fetchall()
            assert {step for step, _ in rows} == {call[2] for call in calls}
            assert all(json.loads(metadata)['arguments'] == {} and '126' not in metadata for _, metadata in rows)

    run_offline(exercise())


def test_model_proposes_scoped_structured_plan(workbench, capsys, monkeypatch):
    async def proposed_plan(request):
        assert str(request.url) == 'http://127.0.0.1:11434/api/chat'
        payload = json.loads(request.content)
        steps = payload['format']['properties']['steps']
        assert steps['maxItems'] == 3 and not payload['stream'] and 'tools' not in payload
        item = steps['items']['properties']
        assert item['name']['const'] == 'calculator' and item['arguments']['additionalProperties'] is False
        assert item['arguments']['required'] == ['expression']
        message = {'role': 'assistant', 'content': '{"steps":[{"name":"calculator","arguments":{"expression":"81 / 3"}}]}'}
        if payload['messages'][-1]['content'] == 'Divide 81 by 3.':
            message['tool_calls'] = [{'function': {'name': 'calculator', 'arguments': []}}]
        return httpx.Response(200, json={'model': payload['model'], 'done': True, 'message': message})

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            provider = workbench.state.model_router.get_provider('ollama')
            await provider._client.aclose()
            monkeypatch.setattr(provider, '_client', httpx.AsyncClient(
                base_url='http://127.0.0.1:11434', transport=httpx.MockTransport(proposed_plan), trust_env=False,
            ))
            _, _, calls = record_calls(workbench, monkeypatch)
            turn = await new_turn(client, message='How much is 81 divided by 3?')
            events = parse_events(await client.post('/api/chat', json=turn))
            assert events[-1]['type'] == 'done' and '**Result: 27.0**' in events[-2]['content']
            malformed = {**turn, 'turn_id': str(uuid4()), 'message': 'Divide 81 by 3.'}
            assert parse_events(await client.post('/api/chat', json=malformed))[-1]['error_code'] == 'MODEL_INCOMPLETE'
            assert [call[:2] for call in calls] == [('calculator', {'expression': '81 / 3'})]

    run_offline(exercise())


@pytest.mark.parametrize(('output', 'code'), [
    ('broken JSON', 'LOCAL_TOOL_INVALID'),
    ('{"steps":[],"steps":[]}', 'LOCAL_TOOL_INVALID'),
    ('{"steps":[]}', 'LOCAL_TOOL_UNSUPPORTED'),
    ('{"steps":[{"name":"file_read","arguments":{}}]}', 'LOCAL_TOOL_UNAUTHORIZED'),
    ('{"steps":[{"name":"unknown","arguments":{}}]}', 'LOCAL_TOOL_UNAUTHORIZED'),
    ('{"steps":[{"name":"calculator","arguments":{"expression":42}}]}', 'LOCAL_TOOL_INVALID'),
    ('{"steps":[{"name":"calculator","arguments":{"expression":"2+2","extra":true}}]}', 'LOCAL_TOOL_INVALID'),
    ('{"steps":[{"name":"calculator","arguments":{"expression":"2+2"}},'
     '{"name":"calculator","arguments":{"expression":"abs(4)"}}]}', 'LOCAL_TOOL_INVALID'),
    ('{"steps":[{"name":"calculator","arguments":{"expression":"@step_2.result / 2"}}]}', 'LOCAL_TOOL_INVALID'),
    (json.dumps({'steps': [{'name': 'calculator', 'arguments': {'expression': f'{i}+1'}} for i in range(4)]}),
     'LOCAL_TOOL_INVALID'),
])
def test_invalid_model_plans_never_dispatch(workbench, capsys, monkeypatch, output, code):
    async def proposed_plan(request):
        return ChatResponse(output, request.model, 'ollama')

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            monkeypatch.setattr(workbench.state.model_router.get_provider('ollama'), 'chat', proposed_plan)
            _, _, calls = record_calls(workbench, monkeypatch)
            turn = await new_turn(client, message='Divide 81 by 3.')
            events = parse_events(await client.post('/api/chat', json=turn))
            assert events[-1]['error_code'] == code
            assert not any(event['type'] in {'tool_start', 'delta', 'done'} for event in events)
            assert calls == []

    run_offline(exercise())


def test_local_denials_preserve_production_roles_and_protected_surfaces(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            registry, execute, calls = record_calls(workbench, monkeypatch)
            turn = await new_turn(client, message='Run a shell command and read a file.')
            assert (await client.post('/api/chat', json={**turn, 'stream': False})).status_code == 403
            assert calls == []
            unauthorized = await execute('calculator', {'expression': '25+17'}, session_id=turn['session_id'],
                                         user_role='viewer', user_id=workbench.state.local_viewer_id)
            assert not unauthorized.success
            production = ToolRegistry()
            production.register(ToolDefinition('calculator', 'Arithmetic', CalculatorInput, execute_calculator))
            assert not (await production.execute('calculator', {'expression': '25+17'}, user_role='viewer')).success
            number = {**turn, 'turn_id': str(uuid4()), 'message': 'What is 25 + 17?'}
            registry.get('calculator').requires_approval = True
            assert parse_events(await client.post('/api/chat', json=number))[-1]['error_code'] == 'LOCAL_TOOL_APPROVAL_REQUIRED'
            registry.disable('calculator')
            assert parse_events(await client.post('/api/chat', json={**number, 'attempt': 2}))[-1]['error_code'] == 'LOCAL_TOOL_UNAVAILABLE'
            registry.unregister('calculator')
            assert parse_events(await client.post('/api/chat', json={**number, 'attempt': 3}))[-1]['error_code'] == 'LOCAL_TOOL_UNAVAILABLE'
            assert (await client.post('/api/chat', json={**number, 'tools_enabled': True})).status_code == 403
            for path in ('/api/tools', '/api/tasks', '/api/artifacts'):
                assert (await client.get(path)).status_code == 403
            assert (await client.get('/api/documents')).status_code == 503
            for ext in ('pdf', 'docx', 'xlsx'):
                assert (await client.post('/api/chat', files={'file': (f'test.{ext}', b'sample')})).status_code == 415
            assert calls == []

    run_offline(exercise())


def test_rejects_false_success_and_missing_model(workbench, capsys, monkeypatch):
    async def false_success(name, arguments, **kwargs):
        return ToolResult(name, True, {'expression': arguments['expression'], 'result': 99})

    async def unavailable(target, *, local_only):
        return health.ModelObservation('available', 'inventory_observed', (), ())

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            monkeypatch.setattr(health, 'observe_ollama', unavailable)
            monkeypatch.setattr(workbench.state.engine._tool_registry, 'execute', false_success)
            turn = await new_turn(client, message='What is 25 + 17?')
            events = parse_events(await client.post('/api/chat', json=turn))
            assert events[-2]['type'] == 'tool_result' and events[-2]['success'] is False
            assert events[-1]['error_code'] == 'LOCAL_TOOL_FAILED' and '99' not in events[-2]['content']
            assert len((await client.get(history_path(turn))).json()['messages']) == 1
            model_turn = {**turn, 'turn_id': str(uuid4()), 'message': 'Divide 81 by 3.'}
            assert parse_events(await client.post('/api/chat', json=model_turn))[-1]['error_code'] == 'MODEL_UNAVAILABLE'

    run_offline(exercise())


@pytest.mark.parametrize('stop_reason', ['cancelled', 'interrupted'])
def test_explicit_retry_reuses_verified_step_after_disconnect_and_restart(
    durable_environment, capsys, monkeypatch, stop_reason,
):
    _, make_app = durable_environment
    pending, cancelled = asyncio.Event(), asyncio.Event()

    async def exercise():
        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            registry, dispatch, _ = record_calls(app, monkeypatch)
            first_calls = []

            async def pause_second(name, arguments, **kwargs):
                first_calls.append(arguments['expression'])
                if len(first_calls) == 1:
                    return await dispatch(name, arguments, **kwargs)
                pending.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            monkeypatch.setattr(registry, 'execute', pause_second)
            turn = await new_turn(client, message='Calculate 25 + 17, then multiply the result by 3.')
            posting = asyncio.create_task(client.post('/api/chat', json=turn))
            await asyncio.wait_for(pending.wait(), 2)
            posting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await posting
            assert (await client.post('/api/chat', json=turn)).status_code == 409
            if stop_reason == 'cancelled':
                stopped = (await client.post(cancel_path(turn))).json()
                assert stopped['state'] == 'cancelled' and 'calculator_progress' not in stopped
            else:
                await app.state.local_chat_runs.close()
            assert cancelled.is_set() and first_calls == ['25 + 17', '(42) * (3)']
            assert 'calculator_progress' not in (await client.get(history_path(turn))).text
        restarted = make_app('durable')
        async with restarted.router.lifespan_context(restarted), api_client(restarted) as client:
            await login(client, capsys)
            _, _, retried = record_calls(restarted, monkeypatch)
            assert parse_events(await client.post('/api/chat', json=turn))[-1]['error_code'] == f'TURN_{stop_reason.upper()}'
            assert retried == []
            retry = {**turn, 'attempt': 2}
            events = parse_events(await client.post('/api/chat', json=retry))
            assert events[-1]['type'] == 'done' and '**Result: 126**' in events[-2]['content']
            assert [arguments['expression'] for _, arguments, _ in retried] == ['(42) * (3)']
            assert parse_events(await client.post('/api/chat', json=retry))[0]['type'] == 'done'
            assert len(retried) == 1
        final = make_app('durable')
        async with final.router.lifespan_context(final), api_client(final) as client:
            await login(client, capsys)
            messages = (await client.get(history_path(turn))).json()['messages']
            assert messages[0]['turn']['state'] == 'completed' and '**Result: 126**' in messages[1]['content']
            assert len(messages[1]['tool_events']) == 2

    run_offline(exercise())
