"""Offline acceptance for authenticated status and older verified references."""

import asyncio
from datetime import datetime, timezone

from pydantic import BaseModel

from backend.auth.models import User
from backend.auth.security import hash_password
from backend.tools.registry import ToolDefinition
from test_a22_async_offline import run_offline
from test_l2_local_turns import api_client, cancel_path, history_path, login, new_turn, workbench
from test_l3_durable_history import durable_environment
from test_l4_local_calculator import record_calls
from test_l5_contextual_tools import next_turn, send


def test_capabilities_only_show_current_viewers_reviewed_tool_status(workbench, capsys):
    path = '/api/health/capabilities'

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            assert 'local_tools' not in (await client.get(path)).json()
            await login(client, capsys)
            response = await client.get(path)
            assert response.headers['cache-control'] == 'no-store'
            tools = response.json()['local_tools']
            available = {'available': True, 'reason': 'available', 'requires_approval': False}
            assert tools == {'enabled': True, 'tools': {'calculator': available, 'unit_conversion': available}}
            registry = workbench.state.engine._tool_registry
            registry.disable('calculator')
            registry.get('unit_conversion').requires_approval = True

            async def hidden_tool(args):
                raise AssertionError('The tool is outside the local allowlist')

            registry.register(ToolDefinition('hidden_tool', 'Internal tool', BaseModel, hidden_tool))
            status = (await client.post(f'{path}/ollama-check')).json()['local_tools']
            assert status == {'enabled': False, 'tools': {
                'calculator': {'available': False, 'reason': 'disabled', 'requires_approval': False},
                'unit_conversion': {'available': False, 'reason': 'approval_required', 'requires_approval': True},
            }}
            conversion = await new_turn(client, message='5 km to m')
            denied = await send(client, conversion)
            assert denied[-1]['error_code'] == 'LOCAL_TOOL_APPROVAL_REQUIRED'
            assert not any(event['type'] == 'tool_start' for event in denied)
            registry.get('unit_conversion').requires_approval = False
            registry.get('unit_conversion').requires_confirmation = True
            confirmation = (await client.get(path)).json()['local_tools']['tools']['unit_conversion']
            assert confirmation == {'available': False, 'reason': 'confirmation_required', 'requires_approval': False}
            registry.get('unit_conversion').requires_confirmation = False
            registry.get('unit_conversion').risk_level = 'high'
            assert (await client.get(path)).json()['local_tools']['tools']['unit_conversion']['reason'] == 'unavailable'
            workbench.state.auth_store.create_user(User(
                id='fixture-outsider', username='outsider', password_hash=hash_password('fixture-pass'),
                role='viewer', clearance='L1', created_at=datetime.now(timezone.utc).isoformat(),
            ))
            async with api_client(workbench) as outsider:
                assert (await outsider.post('/api/auth/login', json={
                    'username': 'outsider', 'password': 'fixture-pass',
                })).status_code == 200
                assert 'local_tools' not in (await outsider.get(path)).json()
            assert (await client.post('/api/auth/logout')).status_code == 200
            assert 'local_tools' not in (await client.get(path)).json()

    run_offline(exercise())


def test_named_and_relative_references_are_verified_and_replayable(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            _, _, calls = record_calls(workbench, monkeypatch)
            initial = await new_turn(client, message='25 + 17')
            for turn, value in [
                (initial, '42'), (next_turn(initial, 'Multiply the previous result by 2.'), '84'),
            ]:
                assert (await send(client, turn))[-2]['content'] == f'**Result: {value}**'
            for prompt in ('What was the answer before that?', 'Use the result from the first calculation.'):
                recall = next_turn(initial, prompt)
                events = await send(client, recall)
                assert [event['type'] for event in events] == ['accepted', 'delta', 'done']
                assert events[-2]['content'] == '**Verified result: 42**'
                assert [event['type'] for event in await send(client, recall)] == ['done']
            earlier = next_turn(initial, 'Multiply the result from the first calculation by 3.')
            assert (await send(client, earlier))[-2]['content'] == '**Result: 126**'
            assert len(calls) == 3
            foreign = await new_turn(client, message='Use the result from the first calculation.')
            assert (await send(client, foreign))[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            history = (await client.get(history_path(initial))).json()['messages']
            assert sum(entry['content'] == '**Verified result: 42**' for entry in history) == 2

    run_offline(exercise())


def test_quantity_clarification_and_older_conversion_disambiguation(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            _, _, calls = record_calls(workbench, monkeypatch)
            initial = await new_turn(client, message='5 km to m')
            assert (await send(client, initial))[-2]['content'] == '**Result: 5000 m**'
            for prompt in ('Subtract 5 from the conversion result.', 'Subtract 5 kg from the conversion result.'):
                invalid = await send(client, next_turn(initial, prompt))
                assert invalid[-1]['error_code'] == 'LOCAL_TOOL_REFERENCE_UNIT'
                assert not any(event['type'] == 'tool_start' for event in invalid)
            result = await send(client, next_turn(initial, 'Subtract 5 m from the conversion result.'))
            assert result[-2]['content'] == '**Result: 4995 m**'
            assert (await send(client, next_turn(initial, 'Convert it to km.')))[-2]['content'] == '**Result: 4.995 km**'
            ambiguous = await send(client, next_turn(initial, 'Subtract 5 m from the conversion result.'))
            assert ambiguous[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            selected = await send(client, next_turn(initial, 'Subtract 5 m from the first conversion.'))
            assert selected[-2]['content'] == '**Result: 4995 m**'
            assert len(calls) == 4

    run_offline(exercise())


def test_failed_and_cancelled_numeric_turns_do_not_shift_named_ordinals(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            first = await new_turn(client, message='1 / 0')
            assert (await send(client, first))[-1]['error_code'] == 'LOCAL_TOOL_ZERO_DIVISION'
            started = asyncio.Event()
            registry = workbench.state.engine._tool_registry
            execute = registry.execute

            async def pause(*args, **kwargs):
                started.set()
                await asyncio.Event().wait()

            second = next_turn(first, '1 + 2')
            monkeypatch.setattr(registry, 'execute', pause)
            active = asyncio.create_task(send(client, second))
            await asyncio.wait_for(started.wait(), 2)
            stopped = (await client.post(cancel_path(second))).json()
            assert stopped['state'] == 'cancelled' and 'numeric_tool' not in stopped
            assert (await active)[-1]['error_code'] == 'TURN_CANCELLED'
            monkeypatch.setattr(registry, 'execute', execute)
            third = next_turn(first, '25 + 17')
            assert (await send(client, third))[-2]['content'] == '**Result: 42**'
            for ordinal in ('first', 'second'):
                denied = await send(client, next_turn(first, f'Use the result from the {ordinal} calculation.'))
                assert denied[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            assert (await send(client, next_turn(first, 'Use the third calculation.')))[-2]['content'] == '**Verified result: 42**'
            assert (await send(client, second))[-1]['error_code'] == 'TURN_CANCELLED'

    run_offline(exercise())


def test_unavailable_older_reference_attempt_can_retry_after_durable_restart(durable_environment, capsys, monkeypatch):
    _, make_app = durable_environment

    async def exercise():
        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            initial = await new_turn(client, message='25 + 17')
            assert (await send(client, initial))[-2]['content'] == '**Result: 42**'
            followup = next_turn(initial, 'Multiply the first calculation by 2.')
            app.state.engine._tool_registry.disable('calculator')
            unavailable = await send(client, followup)
            assert unavailable[-1]['error_code'] == 'LOCAL_TOOL_UNAVAILABLE'
        reopened = make_app('durable')
        async with reopened.router.lifespan_context(reopened), api_client(reopened) as client:
            await login(client, capsys)
            _, _, calls = record_calls(reopened, monkeypatch)
            assert (await send(client, followup))[-1]['error_code'] == 'LOCAL_TOOL_UNAVAILABLE'
            assert calls == []
            retry = {**followup, 'attempt': 2}
            assert (await send(client, retry))[-2]['content'] == '**Result: 84**'
            assert [event['type'] for event in await send(client, retry)] == ['done']
            assert len(calls) == 1
            saved = (await client.get(history_path(initial))).json()['messages']
            assert saved[-2]['turn']['attempt'] == 2 and saved[-1]['content'] == '**Result: 84**'
            assert 'numeric_tool' not in saved[-2]['turn']

    run_offline(exercise())
