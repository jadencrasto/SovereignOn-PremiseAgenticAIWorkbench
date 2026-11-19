"""Independent synthetic development cases for requested-fact grounding."""

import json

import pytest

from backend.rag.local_knowledge import LocalKnowledge, question_parts
from backend.rag.local_relevance import measurements, requested_fact
from test_a22_async_offline import run_offline
from test_l2_local_turns import api_client, login, new_turn
from test_l3_durable_history import durable_environment
from test_l5_contextual_tools import next_turn, send
from test_l9_local_knowledge import fixture_selection, last_message, upload

OWNER = 'fixture-owner'
EQUIPMENT = """# Fictional transfer unit P-442
## Normal operation
Discharge pressure is 3 bar.
## Maintenance
Check the coupling every 6 weeks.
Coupling tightening torque is 28 N·m.
Bearing tightening torque is 46 N·m.
Rated shaft speed is 810 rpm.
"""
THERMAL = """# Fictional HX-770
## Maximum temperature
Inlet temperature is 96 °C.
Outlet temperature is 85 °C.
## Calibration
Verify the outlet probe every 30 days.
"""
VESSEL = """# Fictional V-620
Design vessel capacity is 480 L.
Drain flow rate is 5 L/min.
Design fill duration is 8 minutes.
"""
CASES = [
    ('coupling-frequency', 'What is the P-442 coupling inspection frequency?', 'Check the coupling every 6 weeks.'),
    ('bearing-frequency', 'What is the P-442 bearing inspection interval?', None),
    ('coupling-torque', 'What is the P-442 coupling tightening torque?', 'Coupling tightening torque is 28 N·m.'),
    ('speed', 'What is the rated shaft speed for P-442?', 'Rated shaft speed is 810 rpm.'),
    ('normal-vs-alarm', 'What is the P-442 discharge alarm pressure?', None),
    ('different-entity', 'What is the P-443 coupling tightening torque?', None),
    ('inlet-vs-outlet', 'What is the HX-770 maximum outlet temperature?', 'Outlet temperature is 85 °C.'),
    ('probe-frequency', 'What is the HX-770 outlet probe inspection interval?', 'Verify the outlet probe every 30 days.'),
    ('capacity', 'What is the design vessel capacity for V-620?', 'Design vessel capacity is 480 L.'),
    ('flow', 'What is the V-620 drain flow rate?', 'Drain flow rate is 5 L/min.'),
    ('unknown-slot', 'What is the P-442 coupling inspection certification code?', None),
]


def register(corpus, label, text):
    return corpus.register(OWNER, label, text.encode('utf-8'), 'text/markdown')


def adjudicate(corpus, question, scope=None):
    scope = scope or corpus.bind_scope(OWNER, None)
    parts = question_parts(question)
    contexts = corpus.contexts(OWNER, parts, scope)
    answer, receipt = corpus.answer(OWNER, scope, [
        (part, refs, json.dumps({'passages': list(refs)})) for part, refs in zip(parts, contexts, strict=True)
    ])
    public = corpus.public_receipt(OWNER, {
        **receipt, 'turn_id': '00000000-0000-0000-0000-000000000000', 'attempt': 1, 'request_digest': '0' * 64,
    })
    return answer, public


@pytest.mark.parametrize('question,expected', [(q, e) for _, q, e in CASES], ids=[i for i, _, _ in CASES])
def test_requested_attribute_development_cases(question, expected):
    corpus = LocalKnowledge(OWNER)
    for label, source in [('transfer.md', EQUIPMENT), ('thermal.md', THERMAL), ('vessel.md', VESSEL)]:
        register(corpus, label, source)
    _, receipt = adjudicate(corpus, question)
    assert [citation['excerpt'] for citation in receipt['citations']] == ([expected] if expected else [])
    assert receipt['coverage'][0]['state'] == ('supported' if expected else 'absent')


def test_multipart_retains_only_supported_fact_and_true_conflict():
    corpus = LocalKnowledge(OWNER)
    register(corpus, 'transfer.md', EQUIPMENT)
    register(corpus, 'record.md', '# Fictional transfer unit P-442\n## Maintenance\nCoupling tightening torque is 30 N·m.')
    answer, receipt = adjudicate(
        corpus, 'What is the P-442 coupling tightening torque, and what is the P-442 bearing inspection interval?')
    assert [part['state'] for part in receipt['coverage']] == ['conflict', 'absent']
    assert [item['excerpt'] for item in receipt['citations']] == [
        'Coupling tightening torque is 28 N·m.', 'Coupling tightening torque is 30 N·m.',
    ]
    assert 'bearing inspection interval' in answer


def test_same_torque_with_different_components_is_not_a_conflict():
    corpus = LocalKnowledge(OWNER)
    register(corpus, 'transfer.md', EQUIPMENT)
    _, receipt = adjudicate(corpus, 'What is the P-442 tightening torque?')
    assert receipt['coverage'][0]['state'] == 'supported'
    assert len(receipt['citations']) == 2


def test_answer_cannot_select_a_topical_candidate_from_another_fact():
    corpus = LocalKnowledge(OWNER)
    register(corpus, 'transfer.md', EQUIPMENT)
    scope = corpus.bind_scope(OWNER, None)
    refs, = corpus.contexts(OWNER, ['What is the P-442 coupling inspection interval?'], scope)
    assert refs
    _, receipt = corpus.answer(OWNER, scope, [
        ('What is the P-442 coupling inspection torque?', refs, json.dumps({'passages': list(refs)})),
    ])
    assert receipt['citations'] == [] and receipt['coverage'][0]['state'] in ('absent', 'incomplete')


def test_requested_fact_extracts_a_bounded_attribute_and_exact_component():
    text = 'What is the right-seal tightening moment for P-796 at 25 N·m?'
    supplied = measurements(text)
    fact = requested_fact(text, supplied)
    assert (fact.attribute, fact.dimension, fact.components) == ('torque', 'torque', {'right seal'})
    assert len(supplied) == 1 and not supplied[0].convertible


@pytest.mark.parametrize('question,expected_line', [
    ('What is the FT-574 primary seal tightening moment?', 7),
    ('What is the FT-574 seal inspection frequency?', None),
    ('FT-574 primary seal metallurgy', None),
    ('What is the FT-574 outlet pressure gauge calibration date?', None),
    ('What is the FT-574 outlet alarm pressure?', None),
    ('What is the FT-574 normal inlet pressure limit?', None),
    ('Where is the FT-574 inlet normal pressure of 3 bar?', 4),
    ('Where is the FT-574 inlet normal pressure of 6 bar?', None),
])
def test_fact_evidence_stays_in_its_clause_column_or_component(question, expected_line):
    corpus = LocalKnowledge(OWNER)
    register(corpus, 'meter.md', """# Fictional FT-574
| Device | Normal inlet pressure (bar) | Outlet pressure (bar) | Other note |
| --- | --- | --- | --- |
| FT-574 | 3 | 6 | Alarm test documented |
Every seal inspection takes 20 minutes.
Secondary seal torque is 17 N·m.
Primary seal torque is 25 Nm.
Primary seal torque is not 30 N·m.
""")
    _, receipt = adjudicate(corpus, question)
    assert [item['line'] for item in receipt['citations']] == ([expected_line] if expected_line else [])


def test_attribute_abstention_survives_duplicate_submission_and_durable_restart(
        durable_environment, capsys, monkeypatch):
    _, make_app = durable_environment

    async def exercise():
        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            calls = fixture_selection(app, monkeypatch)
            source = await upload(client, 'actuator.md', b'# V-510\nCheck the valve stem every 2 weeks.',
                                  media_type='text/markdown')
            assert source.status_code == 201
            turn = await new_turn(client, message='What is the V-510 valve stem tightening torque?')
            turn['knowledge_source_ids'] = [source.json()['source_id']]
            assert [event['type'] for event in await send(client, turn)] == ['accepted', 'done']
            assert [event['type'] for event in await send(client, turn)] == ['done']
            original = await last_message(client, turn)
            assert original['local_knowledge']['coverage'][0]['state'] == 'absent'
            assert not calls and original['local_knowledge']['citations'] == []

        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            calls = fixture_selection(app, monkeypatch)
            replay = await last_message(client, turn)
            assert replay['local_knowledge'] == original['local_knowledge']
            assert replay['content'] == original['content']
            assert [event['type'] for event in await send(client, turn)] == ['done']
            assert not calls
            revised = await upload(client, 'actuator.md', '# V-510\nValve stem tightening torque is 17 N·m.'.encode('utf-8'),
                                   previous=source.json(), media_type='text/markdown')
            assert revised.status_code == 200
            assert (await last_message(client, turn))['withheld']
            fresh = next_turn(turn, turn['message'])
            assert (await send(client, fresh))[-1]['type'] == 'done'
            grounded = await last_message(client, fresh)
            citation, = grounded['local_knowledge']['citations']
            assert citation['revision'] == 2 and citation['excerpt'] == 'Valve stem tightening torque is 17 N·m.'

    run_offline(exercise())
