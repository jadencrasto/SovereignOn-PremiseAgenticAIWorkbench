"""Temperature context through owned ASGI turns and registered numerical tools."""

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


@pytest.mark.parametrize(('prompt', 'answer', 'tools'), [
    ('Convert -273.15 °C to Fahrenheit', '-459.67 °F', ('unit_conversion',)),
    ('Convert -459.67 °F to K', '0 K', ('unit_conversion',)),
    ('Convert 10 °C temperature difference to Fahrenheit', '18 Δ°F (temperature difference)', ('unit_conversion',)),
    ('10 K temperature difference in °C', '10 Δ°C (temperature difference)', ('unit_conversion',)),
    ('Convert -1 Δ°F to Kelvin', '-0.555555555556 ΔK (temperature difference)', ('unit_conversion',)),
    ('30 °C − 20 °C', '10 Δ°C (temperature difference)', ('calculator',)),
    ('What is 30 °C minus 20 °C?', '10 Δ°C (temperature difference)', ('calculator',)),
    ('20 °C - 86 °F in Fahrenheit', '-18 Δ°F (temperature difference)', ('unit_conversion', 'calculator', 'unit_conversion')),
    ('20 °C + 10 °C temperature increase', '30 °C', ('calculator',)),
    ('68 °F + 18 Δ°F', '86 °F', ('calculator',)),
    ('68 °F + a decrease of 10 °C', '50 °F', ('unit_conversion', 'calculator')),
    ('5 K - 9 Δ°F in °C', '-273.15 °C', ('calculator', 'unit_conversion')),
    ('-273.148 °C - 0.002 Δ°C', '-273.15 °C', ('calculator',)),
    ('10 Δ°C + 18 Δ°F', '20 Δ°C (temperature difference)', ('unit_conversion', 'calculator')),
    ('-10 Δ°C × 2', '-20 Δ°C (temperature difference)', ('calculator',)),
    ('10 Δ°C ÷ 3 in Fahrenheit', '6 Δ°F (temperature difference)', ('calculator', 'unit_conversion')),
])
def test_registered_temperature_arithmetic(workbench, capsys, monkeypatch, prompt, answer, tools):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            _, _, calls = record_calls(workbench, monkeypatch)
            turn = await new_turn(client, message=prompt)
            events = await send(client, turn)
            assert events[-1]['type'] == 'done', events
            assert events[-2]['content'].splitlines()[0] == f'**Result: {answer}**'
            assert tuple(tool for tool, _, _ in calls) == tools
            history = (await client.get(history_path(turn))).json()['messages'][-1]
            assert not history['withheld']
            assert tuple(event['tool'] for event in history['tool_events']) == tools

    run_offline(exercise())


def test_illegal_temperature_semantics_fail_before_dispatch(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            _, _, calls = record_calls(workbench, monkeypatch)
            first = await new_turn(client, message='20 °C + 10 °C')
            assert (await send(client, first))[-1]['error_code'] == 'LOCAL_TOOL_TEMPERATURE'
            for prompt, code in (
                ('Calculate 20 °C plus 10 °C', 'LOCAL_TOOL_TEMPERATURE'),
                ('Calculate the sum of 20 °C and 10 °C', 'LOCAL_TOOL_INVALID'),
                ('20 °C × 2', 'LOCAL_TOOL_TEMPERATURE'),
                ('10 Δ°C - 2 Δ°C', 'LOCAL_TOOL_TEMPERATURE'),
                ('10 Δ°C + 20 °C', 'LOCAL_TOOL_TEMPERATURE'),
                ('10 Δ°C ÷ 0', 'LOCAL_TOOL_ZERO_DIVISION'),
                ('Convert 10 Δ°C to absolute Fahrenheit', 'LOCAL_TOOL_TEMPERATURE'),
                ('Convert 10 °C to Δ°F', 'LOCAL_TOOL_TEMPERATURE'),
                ('Convert -273.150000000000001 °C to Kelvin', 'LOCAL_TOOL_ABSOLUTE_ZERO'),
                ('Convert -460 °F to Fahrenheit', 'LOCAL_TOOL_ABSOLUTE_ZERO'),
                ('0 K + -1 ΔK', 'LOCAL_TOOL_ABSOLUTE_ZERO'),
                ('-273.15 °C - 0.000000000000000001 Δ°C', 'LOCAL_TOOL_ABSOLUTE_ZERO'),
                ('30 °C - -1 K', 'LOCAL_TOOL_ABSOLUTE_ZERO'),
                ('10 ΔK + 5 m', 'LOCAL_TOOL_DIMENSION'),
            ):
                events = await send(client, next_turn(first, prompt))
                assert events[-1]['error_code'] == code, (prompt, events)
                assert not any(event['type'] == 'tool_start' for event in events)
            assert calls == []

    run_offline(exercise())


def test_typed_references_with_same_conversation_and_source_turns(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            _, _, calls = record_calls(workbench, monkeypatch)
            first = await new_turn(client, message='Convert 5 km to m')
            assert (await send(client, first))[-1]['type'] == 'done'
            assert (await send(client, next_turn(first, '30 + 20')))[-1]['type'] == 'done'
            assert (await send(client, next_turn(
                first, 'Convert it from Celsius to Fahrenheit',
            )))[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            assert (await send(client, next_turn(
                first, 'Convert the previous temperature difference to Fahrenheit',
            )))[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            assert (await send(client, next_turn(first, 'Convert 30 °C to Celsius')))[-1]['type'] == 'done'
            assert (await send(client, next_turn(first, 'Convert -1 K to Celsius')))[-1]['error_code'] == 'LOCAL_TOOL_ABSOLUTE_ZERO'
            assert (await send(client, next_turn(first, 'Convert 20 °C to Fahrenheit')))[-1]['type'] == 'done'
            delta = next_turn(first, 'Subtract the second temperature reading from the first.')
            assert (await send(client, delta))[-2]['content'].startswith('**Result: 10 Δ°C (temperature difference)**')
            length = next_turn(first, 'Add 5 meters to the earlier distance conversion.')
            assert (await send(client, length))[-2]['content'] == '**Result: 5005 m**'
            converted = next_turn(first, 'Convert the previous temperature difference to Fahrenheit.')
            assert (await send(client, converted))[-2]['content'] == '**Result: 18 Δ°F (temperature difference)**'
            recall = next_turn(first, 'Use the result from my first calculation.')
            assert (await send(client, recall))[-2]['content'] == '**Verified result: 50**'
            messages = (await client.get(history_path(first))).json()['messages']
            difference = next(row for row in messages if row['role'] == 'assistant' and
                              row['content'].startswith('**Result: 10 Δ°C (temperature difference)**'))
            assert difference['numeric_source_turn'] == 5
            assert difference['numeric_secondary_source_turn'] == 7
            assert 'numeric_source_v1' not in json.dumps(messages)
            assert (await send(client, next_turn(first, 'Convert 1 km to m')))[-1]['type'] == 'done'
            ambiguous = next_turn(first, 'Add 5 meters to the earlier distance conversion.')
            assert (await send(client, ambiguous))[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            other = await new_turn(client, message='Subtract the second temperature reading from the first')
            assert (await send(client, other))[-1]['error_code'] == 'LOCAL_TOOL_CONTEXT'
            assert [name for name, _, _ in calls].count('calculator') == 3

    run_offline(exercise())


def test_durable_temperature_checkpoint_retries_only_pending_steps(durable_environment, capsys, monkeypatch):
    _, make_app = durable_environment

    async def exercise():
        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            first = await new_turn(client, message='Convert 30 °C to Celsius')
            await send(client, first)
            await send(client, next_turn(first, 'Convert 20 °C to Fahrenheit'))
            turn = next_turn(first, 'Subtract the second temperature reading from the first')
            registry = app.state.engine._tool_registry
            execute = registry.execute
            pending = asyncio.Event()
            completed = []

            async def pause_calculator(name, arguments, **kwargs):
                if name == 'calculator':
                    pending.set()
                    await asyncio.Event().wait()
                result = await execute(name, arguments, **kwargs)
                completed.append(name)
                return result

            monkeypatch.setattr(registry, 'execute', pause_calculator)
            request = asyncio.create_task(send(client, turn))
            await asyncio.wait_for(pending.wait(), 2)
            assert (await client.post(cancel_path(turn))).json()['state'] == 'cancelled'
            assert (await request)[-1]['error_code'] == 'TURN_CANCELLED'
            assert completed == ['unit_conversion']
        reopened = make_app('durable')
        async with reopened.router.lifespan_context(reopened), api_client(reopened) as client:
            await login(client, capsys)
            _, _, calls = record_calls(reopened, monkeypatch)
            assert (await send(client, turn))[-1]['error_code'] == 'TURN_CANCELLED'
            retry = {**turn, 'attempt': 2}
            assert (await send(client, retry))[-2]['content'].startswith('**Result: 10 Δ°C')
            assert [name for name, _, _ in calls] == ['calculator']
            assert [event['type'] for event in await send(client, retry)] == ['done']
            assert len(calls) == 1
        final = make_app('durable')
        async with final.router.lifespan_context(final), api_client(final) as client:
            await login(client, capsys)
            _, _, calls = record_calls(final, monkeypatch)
            prior = (await client.get(history_path(first))).json()['messages'][-1]
            assert prior['numeric_source_turn'] == 1 and prior['numeric_secondary_source_turn'] == 2
            assert calls == []
            reading = next_turn(first, 'Convert the previous temperature difference to Fahrenheit')
            assert (await send(client, reading))[-2]['content'] == '**Result: 18 Δ°F (temperature difference)**'
            assert [name for name, _, _ in calls] == ['unit_conversion']

    run_offline(exercise())


@pytest.mark.parametrize('tamper', ['quantity', 'secondary_source'])
def test_signed_temperature_type_and_sources_cannot_be_rewritten(durable_environment, capsys, tamper):
    root, make_app = durable_environment

    async def exercise():
        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            first = await new_turn(client, message='Convert 30 °C to Celsius')
            await send(client, first)
            await send(client, next_turn(first, 'Convert 20 °C to Celsius'))
            difference = next_turn(first, 'Subtract the second temperature reading from the first')
            assert (await send(client, difference))[-1]['type'] == 'done'
        with closing(sqlite3.connect(root / 'history.sqlite3')) as db, db:
            serialized, = db.execute('SELECT snapshot FROM local_history_transcripts').fetchone()
            transcript = json.loads(serialized)
            assistant = transcript['messages'][-1]
            if tamper == 'quantity':
                assistant['numeric_result_v1']['unit'] = '°C'
            else:
                assistant['numeric_source_v1']['secondary']['attempt'] = 2
            db.execute('UPDATE local_history_transcripts SET snapshot=?', (json.dumps(transcript, ensure_ascii=True),))
        reopened = make_app('durable')
        with pytest.raises(ValueError, match='Invalid completed local reply'):
            async with reopened.router.lifespan_context(reopened):
                pytest.fail('A changed signed temperature result reached startup')

    run_offline(exercise())
