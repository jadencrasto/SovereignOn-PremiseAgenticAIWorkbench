"""Check the complete local attachment path with small, synthetic files."""

import pytest

from test_a22_async_offline import run_offline
from test_r83_text_file_offline import Provider, app_with_text_profile, events
from test_r86_csv_preview_offline import client
from test_r837_multifile_offline import assert_failed
from test_r839_json_offline import EQUIPMENT, answer, request


QUESTION = 'Use the vibration limit in limits.txt to identify equipment exceeding the threshold in equipment.json.'
EQUIPMENT_PATH = (
    'equipment.json',
    b'{"rack~/section":[{"id":"Pump A","readings":{"vibration_mm_s":3.2}},'
    b'{"id":"Pump B","readings":{"vibration_mm_s":7.1e0}},'
    b'{"id":"Pump C","readings":{"vibration_mm_s":"6"}},{"id":"Pump D"}]}',
    'application/json',
)
LIMITS = ('limits.txt', b'Synthetic limits\nMaximum vibration: 5 mm/s\nMaximum pressure: 10 bar\n', 'text/plain')
CSV = ('equipment.csv', b'Equipment,Vibration (mm/s)\nDifferent pump,900\n', 'text/csv')


@pytest.mark.parametrize(('uploads', 'source', 'limit_source'), [
    ((LIMITS, EQUIPMENT_PATH, CSV), '2', '1'),
    ((EQUIPMENT_PATH, CSV, LIMITS), '1', '3'),
])
def test_named_limit_binds_the_exact_field_paths_with_three_formats(tmp_path, uploads, source, limit_source):
    provider = Provider()
    app = app_with_text_profile(tmp_path, provider)
    app.state.engine._dispatch_tool = lambda *args, **kwargs: pytest.fail('generic tools are outside file analysis')

    async def exercise():
        async with client(app) as http:
            response = await request(http, QUESTION, *uploads, source=source)
            text, done = answer(response)
            assert 'Line 2: Maximum vibration: 5 mm/s' in text
            assert '1 of 2 valid readings violate the limit (50.00%)' in text
            assert 'Maximum deviation: 2.1 mm/s (JSON record indices 1)' in text
            assert 'JSON record index 1 at /rack~0~1section/1' in text
            assert '/rack~0~1section/1/id = "Pump B"' in text
            assert '/rack~0~1section/1/readings/vibration_mm_s = 7.1e0' in text
            assert '/rack~0~1section/2/readings/vibration_mm_s = "6" - invalid numeric measurement' in text
            assert '/rack~0~1section/3/readings/vibration_mm_s = (field absent) - missing measurement' in text
            assert '900' not in text and 'Maximum pressure' not in text and 'Data row ' not in text
            assert [(descriptor['attachment_id'], descriptor['mime_type']) for descriptor in done['file_sources']] == [
                (f'source-{limit_source}', 'text/plain'), (f'source-{source}', 'application/json'),
            ]
            history = (await http.get('/api/chat/sessions/session-a')).json()['messages']
            assert [entry['content'] for entry in history] == ['Question about an attached JSON file.']
            for question in (QUESTION, 'Which records exceeded the limit?'):
                later = await http.post('/api/chat', json={'session_id': 'session-a', 'message': question})
                assert events(later)[-1]['error_code'] == 'TEXT_FILE_REATTACH_REQUIRED'
            assert not provider.requests

    run_offline(exercise())
    stored = (tmp_path / 'memory.json').read_text(encoding='utf-8')
    assert all(value not in stored for value in ('Pump B', '7.1e0', '/rack', QUESTION, 'source-1'))


@pytest.mark.parametrize(('limit_body', 'json_body', 'step', 'reason'), [
    (b'Maximum pressure: 10 bar', EQUIPMENT_PATH[1], 1, 'one explicit upper limit'),
    (b'Minimum vibration: 5 mm/s', EQUIPMENT_PATH[1], 1, 'one explicit upper limit'),
    (b'Maximum vibration: 5 mm/s\nMaximum vibration: 7 mm/s', EQUIPMENT_PATH[1], 1, 'conflicts'),
    (LIMITS[1], b'[{"id":"Pump B","Vibration (bar)":8}]', 2, 'cannot be compared'),
])
def test_unusable_named_limit_never_produces_an_answer_or_receipt(tmp_path, limit_body, json_body, step, reason):
    app = app_with_text_profile(tmp_path)

    async def exercise():
        async with client(app) as http:
            limits = (LIMITS[0], limit_body, LIMITS[2])
            data = (EQUIPMENT_PATH[0], json_body, EQUIPMENT_PATH[2])
            stream = events(await request(http, QUESTION, limits, data))
            assert [event['type'] for event in stream if event['type'] in ('delta', 'done', 'error')] == ['error']
            failure = next(event for event in stream if event.get('status') == 'failed')
            assert failure['step_id'] == f'file_step_{step}'
            assert stream[-1]['error_code'] == 'TEXT_WORKFLOW_STEP_FAILED' and reason in stream[-1]['content']
            assert ('Last verified step:' in stream[-1]['content']) == (step == 2)
            assert not (await http.get('/api/chat/sessions/session-a')).json()['messages']

    run_offline(exercise())


def test_named_limit_rejects_a_conflicting_selection_and_duplicate_limits_filenames(tmp_path):
    app = app_with_text_profile(tmp_path)

    async def exercise():
        async with client(app) as http:
            assert_failed(await request(http, QUESTION, LIMITS, EQUIPMENT_PATH, CSV, source='3'), 422, 'TEXT_SOURCE_CONFLICT')
            assert_failed(await request(http, QUESTION, LIMITS, LIMITS, EQUIPMENT_PATH), 422, 'TEXT_SOURCE_AMBIGUOUS')

    run_offline(exercise())


def test_corrected_extrema_prompt_runs_through_json_workflow(tmp_path):
    app = app_with_text_profile(tmp_path)

    async def exercise():
        async with client(app) as http:
            response = await request(http, 'Find the difference between the highest and lowest temperatures.', EQUIPMENT)
            text, _ = answer(response)
            assert 'highest reading is 17 above the lowest' in text
            assert '/equipment/0/measurements/temperature_c = 65' in text
            assert '/equipment/1/measurements/temperature_c = 82' in text

    run_offline(exercise())
