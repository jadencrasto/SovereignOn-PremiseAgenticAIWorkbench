"""L7 acceptance with isolated ASGI, verified local tools, and durable history."""

import asyncio
import json
import sqlite3
from contextlib import closing

import pytest

from test_a22_async_offline import run_offline
from test_l2_local_turns import api_client, cancel_path, history_path, login, new_turn, workbench
from test_l3_durable_history import durable_environment
from test_l4_local_calculator import record_calls
from test_l5_contextual_tools import next_turn, send


def test_length_workflow_and_server_owned_source_attribution(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            _, _, calls = record_calls(workbench, monkeypatch)
            initial = await new_turn(client, message='Convert 5 km to meters.')
            assert (await send(client, initial))[-2]['content'] == '**Result: 5000 m**'
            added = next_turn(initial, 'Add 250 meters to that.')
            assert (await send(client, added))[-2]['content'] == '**Result: 5250 m**'
            converted = next_turn(initial, 'Convert the total back to kilometers.')
            assert (await send(client, converted))[-2]['content'] == '**Result: 5.25 km**'
            assert [name for name, _, _ in calls] == ['unit_conversion', 'calculator', 'unit_conversion']
            assert [event['type'] for event in await send(client, added)] == ['done']
            assert len(calls) == 3
            history = (await client.get(history_path(initial))).json()['messages']
            assert [entry.get('numeric_source_turn') for entry in history[1::2]] == [None, 1, 2]
            assert 'numeric_source_v1' not in json.dumps(history)

    run_offline(exercise())


@pytest.mark.parametrize(('prompt', 'answer', 'tools'), [
    ('2 kg + 500 g', '2.5 kg', ('unit_conversion', 'calculator')),
    ('3 hours − 30 minutes', '2.5 h', ('unit_conversion', 'calculator')),
    ('10 m × 3', '30 m', ('calculator',)),
    ('Multiply 10 meters by 3', '30 m', ('calculator',)),
    ('-2.5 m ÷ 5', '-0.5 m', ('calculator',)),
    ('5 km + 250 m in m', '5250 m', ('unit_conversion', 'calculator', 'unit_conversion')),
    ('0.1 m + 20 cm', '0.3 m', ('unit_conversion', 'calculator')),
    ('100 °C to °F', '212 °F', ('unit_conversion',)),
    ('32 °F to K', '273.15 K', ('unit_conversion',)),
    ('273.15 K to °C', '0 °C', ('unit_conversion',)),
])
def test_measurement_steps_are_deterministic(workbench, capsys, monkeypatch, prompt, answer, tools):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            _, _, calls = record_calls(workbench, monkeypatch)
            turn = await new_turn(client, message=prompt)
            events = await send(client, turn)
            assert events[-1]['type'] == 'done'
            assert events[-2]['content'].splitlines()[0] == f'**Result: {answer}**'
            assert tuple(name for name, _, _ in calls) == tools
            history = (await client.get(history_path(turn))).json()['messages'][-1]
            assert history['withheld'] is False
            assert tuple(event['tool'] for event in history['tool_events']) == tools

    run_offline(exercise())


def test_measurement_errors_clarify_before_execution(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            registry, _, calls = record_calls(workbench, monkeypatch)
            first = await new_turn(client, message='5 m + 5 m')
            assert (await send(client, first))[-2]['content'] == '**Result: 10 m**'
            for prompt, code in (
                ('Add 5 to that.', 'LOCAL_TOOL_REFERENCE_UNIT'),
                ('Convert it.', 'LOCAL_TOOL_TARGET_UNIT'),
                ('5 m + 2 kg', 'LOCAL_TOOL_DIMENSION'),
                ('Add 2 kilograms to 5 meters.', 'LOCAL_TOOL_DIMENSION'),
                ('10 m × 3 m', 'LOCAL_TOOL_DIMENSION'),
                ('5 m/s + 3 m/s', 'LOCAL_TOOL_INVALID'),
                ('5 m + 2 USD', 'LOCAL_TOOL_UNIT'),
                ('10 C to K', 'LOCAL_TOOL_UNIT'),
                ('100 °C + 5 °C', 'LOCAL_TOOL_TEMPERATURE'),
                ('10 m ÷ 0', 'LOCAL_TOOL_ZERO_DIVISION'),
                ('1000000000001 m + 2 m', 'LOCAL_TOOL_INVALID'),
            ):
                if prompt == 'Convert it.':
                    assert (await send(client, next_turn(first, '5 m + 5 m')))[-1]['type'] == 'done'
                invalid = await send(client, next_turn(first, prompt))
                assert invalid[-1]['error_code'] == code
                assert not any(event['type'] == 'tool_start' for event in invalid)
            registry.disable('unit_conversion')
            denied = await send(client, next_turn(first, '2 kg + 500 g'))
            assert denied[-1]['error_code'] == 'LOCAL_TOOL_UNAVAILABLE'
            assert len(calls) == 2
            assert (await client.get('/api/documents')).status_code == 503
            assert (await client.get('/api/tools')).status_code == 403
            assert (await client.post('/api/chat', files={'file': ('test.pdf', b'example')})).status_code == 415

    run_offline(exercise())


def test_named_recall_can_supply_an_operand_without_creating_a_calculation(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            _, _, calls = record_calls(workbench, monkeypatch)
            initial = await new_turn(client, message='25 + 17')
            second = next_turn(initial, '20 + 30')
            assert (await send(client, initial))[-1]['type'] == 'done'
            assert (await send(client, second))[-1]['type'] == 'done'
            recall = next_turn(initial, 'Use the result from the first calculation.')
            assert (await send(client, recall))[-2]['content'] == '**Verified result: 42**'
            assert len(calls) == 2
            reused = next_turn(initial, 'Multiply that by 2.')
            assert (await send(client, reused))[-2]['content'] == '**Result: 84**'
            named = next_turn(initial, 'Subtract 5 from the answer to my second calculation.')
            assert (await send(client, named))[-2]['content'] == '**Result: 45**'
            earlier = await send(client, next_turn(initial, 'Add 10 to the value we calculated before.'))
            assert earlier[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            separate = await new_turn(client, message='Multiply the result from the first calculation by 2.')
            assert (await send(client, separate))[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            unique = await new_turn(client, message='25 + 17')
            assert (await send(client, unique))[-1]['type'] == 'done'
            assert (await send(client, next_turn(unique, 'Add 10 to the value we calculated before.')))[-2]['content'] == '**Result: 52**'
            history = (await client.get(history_path(initial))).json()['messages']
            assert [row.get('numeric_source_turn') for row in history[1:10:2]] == [None, None, 1, 1, 2]

    run_offline(exercise())


@pytest.mark.parametrize('tamper', [False, True])
def test_mixed_plan_checkpoint_is_verified_across_restart(durable_environment, capsys, monkeypatch, tamper):
    root, make_app = durable_environment

    async def exercise():
        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            initial = await new_turn(client, message='Convert 5 km to meters.')
            assert (await send(client, initial))[-1]['type'] == 'done'
            turn = next_turn(initial, 'Add 0.25 km to that in km.')
            registry, execute, _ = record_calls(app, monkeypatch)
            pending = asyncio.Event()

            async def pause_calculator(name, args, **kwargs):
                if name == 'calculator':
                    pending.set()
                    await asyncio.Event().wait()
                return await execute(name, args, **kwargs)

            monkeypatch.setattr(registry, 'execute', pause_calculator)
            request = asyncio.create_task(send(client, turn))
            await asyncio.wait_for(pending.wait(), 2)
            assert (await client.post(cancel_path(turn))).json()['state'] == 'cancelled'
            assert (await request)[-1]['error_code'] == 'TURN_CANCELLED'
        if tamper:
            with closing(sqlite3.connect(root / 'history.sqlite3')) as db, db:
                serialized, = db.execute('SELECT snapshot FROM local_history_transcripts').fetchone()
                transcript = json.loads(serialized)
                transcript['messages'][-1]['local_turn_v1']['calculator_progress']['steps'][0]['result'] = 999.0
                db.execute('UPDATE local_history_transcripts SET snapshot=?', (json.dumps(transcript),))
            broken = make_app('durable')
            with pytest.raises(ValueError, match='Invalid local calculator proof'):
                async with broken.router.lifespan_context(broken):
                    pytest.fail('Tampered measurement checkpoint reached startup')
            return
        reopened = make_app('durable')
        async with reopened.router.lifespan_context(reopened), api_client(reopened) as client:
            await login(client, capsys)
            _, _, calls = record_calls(reopened, monkeypatch)
            assert (await send(client, turn))[-1]['error_code'] == 'TURN_CANCELLED'
            retry = {**turn, 'attempt': 2}
            assert (await send(client, retry))[-2]['content'].startswith('**Result: 5.25 km**')
            assert [name for name, _, _ in calls] == ['calculator', 'unit_conversion']
            assert [event['type'] for event in await send(client, retry)] == ['done']
        final = make_app('durable')
        async with final.router.lifespan_context(final), api_client(final) as client:
            await login(client, capsys)
            _, _, calls = record_calls(final, monkeypatch)
            assert (await send(client, next_turn(initial, 'Multiply the previous result by 2.')))[-2]['content'] == '**Result: 10.5 km**'
            assert [name for name, _, _ in calls] == ['calculator']

    run_offline(exercise())
