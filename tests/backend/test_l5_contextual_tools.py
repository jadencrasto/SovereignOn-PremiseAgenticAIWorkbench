"""Offline local tool acceptance with real ownership, execution and persistence."""

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.auth.models import User
from backend.auth.security import hash_password
from backend.models.base import ChatChunk
from backend.tools.registry import ToolRegistry, ToolResult
from backend.tools.unit_conversion import LocalUnitConversionInput, convert_units, register_unit_conversion
from test_a22_async_offline import run_offline
from test_l2_local_turns import api_client, cancel_path, history_path, login, new_turn, parse_events, workbench
from test_l3_durable_history import durable_environment
from test_l4_local_calculator import record_calls


def next_turn(turn, message):
    return {**turn, 'turn_id': str(uuid4()), 'message': message}


async def send(client, turn):
    return parse_events(await client.post('/api/chat', json=turn))


def test_contextual_arithmetic_and_duplicate_turn(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            _, _, calls = record_calls(workbench, monkeypatch)
            initial = await new_turn(client, message='What is 25 + 17?')
            multiply = next_turn(initial, 'Multiply that by 3.')
            subtract = next_turn(initial, 'Now subtract 6.')
            for turn, value in [
                (initial, '42'), (multiply, '126'), (subtract, '120'),
                (next_turn(initial, 'Divide it by -2.5.'), '-48.0'),
                (next_turn(initial, 'Add 0.5 to the result.'), '-47.5'),
                (next_turn(initial, 'Subtract -2.5 from it.'), '-45.0'),
                (next_turn(initial, 'What is 2.5 × 4?'), '10.0'),
                (next_turn(initial, '5 − 12'), '-7'),
            ]:
                events = await send(client, turn)
                assert events[-1]['type'] == 'done' and events[-2]['content'] == f'**Result: {value}**'
            assert [args['expression'] for _, args, _ in calls][:4] == [
                '25 + 17', '(42) * (3)', '(126) - (6)', '(120) / (-2.5)',
            ]
            assert [event['type'] for event in await send(client, multiply)] == ['done']
            assert len(calls) == 8
            fail = await send(client, next_turn(initial, 'Divide it by 0.'))
            assert fail[-1]['error_code'] == 'LOCAL_TOOL_ZERO_DIVISION'
            assert not any(event['type'] == 'delta' for event in fail)
            followup = await send(client, next_turn(initial, 'Multiply that by 2'))
            assert followup[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT' and len(calls) == 9
            oversized = await send(client, next_turn(initial, f"What is {'9' * 210} + 1?"))
            assert oversized[-1]['error_code'] == 'LOCAL_TOOL_INVALID' and len(calls) == 9
            contents = (await client.get(history_path(initial))).text
            assert 'numeric_result_v1' not in contents and 'calculator_progress' not in contents

    run_offline(exercise())


def test_conversions_execute_and_reject_unclear_units_without_dispatch(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            _, _, calls = record_calls(workbench, monkeypatch)
            initial = await new_turn(client, message='Convert 5 km to m.')
            for turn, answer in [
                (initial, '5000 m'),
                (next_turn(initial, 'Convert that to cm.'), '500000 cm'),
                (next_turn(initial, 'What is 100°C in °F?'), '212 °F'),
                (next_turn(initial, 'How many pounds are in 2 kg?'), '4.4092452437 lb'),
                (next_turn(initial, '1 US cup to US fl oz'), '8 US fl oz'),
            ]:
                events = await send(client, turn)
                assert events[2]['tool'] == 'unit_conversion'
                assert events[-1]['type'] == 'done' and events[-2]['content'] == f'**Result: {answer}**'
            mismatch = await send(client, next_turn(initial, 'Convert it from US gal to L'))
            assert mismatch[-1]['error_code'] == 'LOCAL_TOOL_UNIT'
            for message in ('Convert 1 m to kg', 'Convert 100 USD to INR', 'Convert 2 gallons to L',
                            'Convert 2 imperial cups to mL', 'Convert 1 fl oz to mL',
                            'Convert 1 kg to stone', 'Convert 3 °C to cm'):
                denied = await send(client, next_turn(initial, message))
                assert denied[-1]['error_code'] == 'LOCAL_TOOL_UNIT'
                assert not any(event['type'] == 'tool_start' for event in denied)
            assert len(calls) == 5 and all(name == 'unit_conversion' for name, _, _ in calls)
            reply = (await client.get(history_path(initial))).json()['messages'][1]
            assert reply['tool_events'][0]['tool'] == 'unit_conversion'

    run_offline(exercise())


@pytest.mark.parametrize(('value', 'origin', 'target', 'expected'), [
    (1, 'in', 'mm', 25.4), (1, 'ft', 'in', 12), (1, 'mi', 'm', 1609.344),
    (1000, 'mg', 'g', 1), (16, 'oz', 'lb', 1), (3600, 's', 'h', 1), (1, 'min', 's', 60),
    (32, '°F', '°C', 0), (-40, '°F', '°C', -40), (0, 'K', '°F', -459.67),
    (273.15, 'K', '°C', 0), (-459.67, '°F', 'K', 0), (100, '°F', '°F', 100),
    (1, 'US gal', 'L', 3.785411784), (1, 'US fl oz', 'mL', 29.5735295625),
])
def test_local_conversion_reference_values(value, origin, target, expected):
    assert convert_units(value, origin, target, local=True) == expected


@pytest.mark.parametrize('bad_arguments', [
    {'value': '1'}, {'value': float('nan')}, {'value': float('inf')}, {'value': 1e12 + 1},
    {'from_unit': 'USD'}, {'extra': []},
])
def test_local_conversion_input_schema_is_strict(bad_arguments):
    with pytest.raises(ValidationError):
        LocalUnitConversionInput.model_validate({'value': 1, 'from_unit': 'kg', 'to_unit': 'lb', **bad_arguments})


def test_ambiguous_stale_prose_and_foreign_context_never_dispatch(workbench, capsys, monkeypatch):
    async def plain(request):
        yield ChatChunk('There are 42 items and 120 units.', True)

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as owner:
            await login(owner, capsys)
            monkeypatch.setattr(workbench.state.model_router.get_provider('ollama'), 'chat_stream', plain)
            _, _, calls = record_calls(workbench, monkeypatch)
            initial = await new_turn(owner, message='25 + 17')
            assert (await send(owner, initial))[-1]['type'] == 'done'
            other_conversation = await new_turn(owner, message='Multiply that by 2.')
            assert (await send(owner, other_conversation))[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            workbench.state.auth_store.create_user(User(
                id='other-test-viewer', username='other', password_hash=hash_password('fixture-pass'),
                role='viewer', clearance='L1', created_at=datetime.now(timezone.utc).isoformat(),
            ))
            async with api_client(workbench) as other:
                assert (await other.post('/api/auth/login', json={'username': 'other', 'password': 'fixture-pass'})).status_code == 200
                response = await other.post('/api/chat', json=next_turn(initial, 'Multiply that by 2.'))
                assert response.status_code == 404
                outsider = await new_turn(other, message='5 km to m')
                assert (await send(other, outsider))[-1]['error_code'] == 'LOCAL_TOOL_UNAUTHORIZED'
            assert (await send(owner, next_turn(initial, '5 + 6')))[-1]['type'] == 'done'
            ambiguous = await send(owner, next_turn(initial, 'Multiply the last two results by 3.'))
            assert ambiguous[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            ordinal = await send(owner, next_turn(initial, 'Multiply the first or second by 3.'))
            assert ordinal[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            prose = await send(owner, next_turn(initial, 'Describe that answer.'))
            assert prose[-1]['type'] == 'done'
            stale = await send(owner, next_turn(initial, 'Subtract 3 from the result.'))
            assert stale[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            assert len(calls) == 2

    run_offline(exercise())


def test_cancelled_calculation_cannot_supply_an_operand(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            initial = await new_turn(client, message='What is 25 + 17?')
            assert (await send(client, initial))[-1]['type'] == 'done'
            started = asyncio.Event()

            async def wait_for_stop(*args, **kwargs):
                started.set()
                await asyncio.Event().wait()

            registry = workbench.state.engine._tool_registry
            monkeypatch.setattr(registry, 'execute', wait_for_stop)
            cancelled = next_turn(initial, 'Multiply that by 2.')
            task = asyncio.create_task(send(client, cancelled))
            await asyncio.wait_for(started.wait(), 2)
            assert (await client.post(cancel_path(cancelled))).json()['state'] == 'cancelled'
            assert (await task)[-1]['error_code'] == 'TURN_CANCELLED'
            assert (await send(client, next_turn(initial, 'Subtract 6.')))[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'

    run_offline(exercise())


@pytest.mark.parametrize('tamper', [False, True])
def test_verified_operands_recover_from_durable_history(durable_environment, capsys, monkeypatch, tamper):
    root, make_app = durable_environment

    async def exercise():
        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            initial = await new_turn(client, message='25 + 17')
            assert (await send(client, initial))[-1]['type'] == 'done'
        if tamper:
            with closing(sqlite3.connect(root / 'history.sqlite3')) as db, db:
                serialized, = db.execute('SELECT snapshot FROM local_history_transcripts').fetchone()
                transcript = json.loads(serialized)
                transcript['messages'][-1]['numeric_result_v1']['value'] = 99
                db.execute('UPDATE local_history_transcripts SET snapshot=?', (json.dumps(transcript),))
            with pytest.raises(ValueError, match='Invalid completed local reply'):
                damaged = make_app('durable')
                async with damaged.router.lifespan_context(damaged):
                    pytest.fail('Tampered numeric metadata reached startup')
            return
        resumed = make_app('durable')
        async with resumed.router.lifespan_context(resumed), api_client(resumed) as client:
            await login(client, capsys)
            _, _, calls = record_calls(resumed, monkeypatch)
            assert [e['type'] for e in await send(client, initial)] == ['done'] and calls == []
            result = await send(client, next_turn(initial, 'Multiply it by 3.'))
            assert result[-2]['content'] == '**Result: 126**' and len(calls) == 1

    run_offline(exercise())


def test_forged_conversion_result_and_registry_permissions(workbench, capsys, monkeypatch):
    async def forged(name, args, **kwargs):
        return ToolResult(name, True, {**args, 'result': 6000.0})

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            turn = await new_turn(client, message='5 km to m')
            args = {'value': 5, 'from_unit': 'km', 'to_unit': 'm'}
            registry = workbench.state.engine._tool_registry
            assert not (await registry.execute('unit_conversion', args, session_id=turn['session_id'],
                                               user_role='viewer', user_id=workbench.state.local_viewer_id)).success
            production = ToolRegistry()
            register_unit_conversion(production)
            assert not (await production.execute('unit_conversion', args, user_role='viewer')).success
            with pytest.raises(ValidationError):
                production.get('unit_conversion').input_schema.model_validate({**args, 'to_unit': 'mi'})
            monkeypatch.setattr(registry, 'execute', forged)
            events = await send(client, turn)
            assert events[-1]['error_code'] == 'LOCAL_TOOL_FAILED'
            assert events[-2]['type'] == 'tool_result' and events[-2]['success'] is False
            assert not any('6000' in event['content'] for event in events)
            assert (await client.get('/api/documents')).status_code == 503
            assert (await client.get('/api/tools')).status_code == 403
            for ext in ('pdf', 'docx', 'xlsx'):
                assert (await client.post('/api/chat', files={'file': (f'test.{ext}', b'sample')})).status_code == 415

    run_offline(exercise())
