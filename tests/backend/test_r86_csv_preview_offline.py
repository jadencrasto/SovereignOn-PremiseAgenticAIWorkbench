"""Bounded CSV preview and analysis through in-process routes with no model service."""
import asyncio
import json
import time
from contextlib import aclosing
from types import SimpleNamespace

import httpx
import pytest

from backend.health.routes import INFERENCE_TTL
from backend.models.base import ChatChunk
from backend.multimodal.text_file import MAX_TEXT_FORM_BYTES
from test_a22_async_offline import run_offline
from test_r83_text_file_offline import AUTH, BASE_URL, Provider, app_with_text_profile, ask_form, events


PREVIEW = '/api/chat/csv-preview'
ASK = '/api/chat/csv-file'
SOURCE = ('Item,Value,Notes,Active\r\n'
          'caf\u00e9,12,"red, then ""blue""\nnext",true\r\n'
          'private-row,17,,false\r\n')


def upload(data=SOURCE.encode('utf-8-sig'), name='sample.csv', media='text/csv'):
    return {'file': (name, data, media)}


def client(app, headers=AUTH):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, headers=headers)


def test_csv_preview_preserves_quoted_unicode_cells_whitespace_and_formulas(tmp_path):
    app = app_with_text_profile(tmp_path)
    app.state.inference_observation = None
    content = SOURCE + 'other,-1,=2+3,true\n+,1,@name,true\n-2,2,+3,false\n'

    async def exercise():
        async with client(app) as http:
            result = await http.post(PREVIEW, files=upload(content.encode('utf-8-sig'), media='text/csv; charset=UTF-8'))
            assert result.status_code == 200
            assert result.headers['Cache-Control'] == 'no-store'
            assert result.json() == {
                'filename': 'sample.csv', 'size_bytes': len(content.encode('utf-8-sig')),
                'headers': ['Item', 'Value', 'Notes', 'Active'],
                'column_types': ['Text', 'Number', 'Text', 'Boolean'], 'row_count': 5, 'truncated': False,
                'rows': [['caf\u00e9', '12', 'red, then "blue"\nnext', 'true'], ['private-row', '17', '', 'false'],
                         ['other', '-1', '=2+3', 'true'], ['+', '1', '@name', 'true'], ['-2', '2', '+3', 'false']],
            }
            large_preview = upload(('Name\n' + '\n'.join(f'row-{i}' for i in range(1, 15))).encode())
            first = (await http.post(PREVIEW, files=large_preview)).json()
            assert first['truncated'] and first['row_count'] == 14 and len(first['rows']) == 12
            last = (await http.post(PREVIEW, files=large_preview,
                                    data={'row_start': '13', 'row_end': '14'})).json()
            assert last['rows'] == [['row-13'], ['row-14']] and not last['truncated']

    run_offline(exercise())


@pytest.mark.parametrize('data,name,media,status,code', [
    (b'', 'x.csv', 'text/csv', 422, 'TEXT_FILE_INVALID'),
    (b'a\n', 'x.csv', 'text/csv', 422, 'CSV_EMPTY'),
    (b',second\n1,2', 'x.csv', 'text/csv', 422, 'CSV_HEADER_INVALID'),
    (b'name, NAME\n1,2', 'x.csv', 'text/csv', 422, 'CSV_HEADER_INVALID'),
    (b' ' * 128 + b'name\n1', 'x.csv', 'text/csv', 422, 'CSV_HEADER_INVALID'),
    (b'"name\n"\n1', 'x.csv', 'text/csv', 422, 'CSV_HEADER_INVALID'),
    (b'a,b,c,d,e,f,g,h,i\n1,2,3,4,5,6,7,8,9', 'x.csv', 'text/csv', 422, 'CSV_HEADER_INVALID'),
    (b'a,b\n1', 'x.csv', 'text/csv', 422, 'CSV_ROW_WIDTH'),
    (b'a\n"unterminated', 'x.csv', 'text/csv', 422, 'CSV_INVALID'),
    (b'a\n"valid"invalid', 'x.csv', 'text/csv', 422, 'CSV_INVALID'),
    (b'a\n\xff', 'x.csv', 'text/csv', 422, 'TEXT_FILE_INVALID'),
    (b'a\n\x00binary', 'x.csv', 'text/csv', 422, 'TEXT_FILE_INVALID'),
    (b'a\n' + b'1\n' * 33, 'x.csv', 'text/csv', 422, 'CSV_LIMIT'),
    (b'a\n' + b'x' * 129, 'x.csv', 'text/csv', 422, 'CSV_LIMIT'),
    (b'a\n' + b'x' * 1199, 'x.csv', 'text/csv', 413, 'TEXT_FILE_TOO_LARGE'),
    (b'a\n' + b'x' * 2047, 'x.csv', 'text/csv', 413, 'TEXT_FILE_TOO_LARGE'),
    (b'a\n1', 'x.txt', 'text/csv', 415, 'TEXT_FILE_TYPE'),
    (b'a\n1', '../x.csv', 'text/csv', 422, 'TEXT_FILE_INVALID'),
    (b'a\n1', 'x.csv', 'application/pdf', 415, 'CSV_FILE_TYPE'),
    (b'a\n1', 'x.csv', 'text/csv; charset=latin1', 415, 'TEXT_FILE_TYPE'),
])
def test_csv_rejects_invalid_file_before_inference(tmp_path, data, name, media, status, code):
    provider = Provider()
    app = app_with_text_profile(tmp_path, provider)

    async def exercise():
        async with client(app) as http:
            response = await http.post(ASK, files=upload(data, name, media),
                                       data=ask_form(row_start='1', row_end='1'))
            assert response.status_code == status
            assert response.headers['X-Error-Code'] == code
        assert not provider.requests

    run_offline(exercise())


def test_csv_ranges_and_multipart_limits(tmp_path):
    provider = Provider()
    app = app_with_text_profile(tmp_path, provider)
    data = upload(b'Header\n' + b'a\n' * 14)

    async def exercise():
        async with client(app) as http:
            for bounds in ({'row_start': '1'}, {'row_start': '0', 'row_end': '1'},
                           {'row_start': '2', 'row_end': '1'}, {'row_start': '1', 'row_end': '13'},
                           {'row_start': '13', 'row_end': '15'}):
                for path, form in ((PREVIEW, bounds), (ASK, ask_form(**bounds))):
                    invalid = await http.post(path, files=data, data=form)
                    assert invalid.status_code == 422 and invalid.headers['X-Error-Code'] == 'CSV_RANGE_INVALID'
            assert (await http.post(ASK, files=data, data=ask_form())).headers['X-Error-Code'] == 'CSV_RANGE_INVALID'
            for path in (PREVIEW, ASK):
                assert (await http.post(path, content=b'x' * (MAX_TEXT_FORM_BYTES + 1))).status_code == 413
            dup = list(data.items()) + [('row_start', (None, '1')), ('row_start', (None, '2'))]
            assert (await http.post(PREVIEW, files=dup)).status_code == 400
        assert not provider.requests

    run_offline(exercise())


def test_csv_asks_only_about_selected_rows_and_history_contains_only_descriptor(tmp_path):
    provider = Provider()
    app = app_with_text_profile(tmp_path, provider)
    engine = app.state.engine
    engine._retrieve_context = lambda *args, **kwargs: pytest.fail('retrieval called')
    engine._dispatch_tool = lambda *args, **kwargs: pytest.fail('tool dispatch called')
    body = ask_form(message='What is selected?', row_start='1', row_end='1')

    async def exercise():
        async with client(app, headers={}) as anonymous, client(app, {'Authorization': 'Bearer b'}) as other:
            for path, form in ((ASK, body), (PREVIEW, {'session_id': 'session-a'})):
                assert (await anonymous.post(path, files=upload(), data=form)).status_code == 401
                assert (await other.post(path, files=upload(), data=form)).status_code == 404
        async with client(app) as http:
            response = await http.post(ASK, files=upload(), data=body)
            assert [item['type'] for item in events(response)] == ['agent_status', 'delta', 'done']
            request = provider.requests[0]
            assert request.model == 'text' and request.max_tokens == 384
            assert [message.role for message in request.messages] == ['system', 'user']
            assert json.loads(request.messages[1].content) == {'question': 'What is selected?', 'untrusted_table': {
                'headers': ['Item', 'Value', 'Notes', 'Active'],
                'rows': [['caf\u00e9', '12', 'red, then "blue"\nnext', 'true']], 'selected_row_count': 1}}
            assert not getattr(request, 'tools', None)
            for question in ('Can you summarize the earlier table?', 'How many rows are there?'):
                result = await http.post('/api/chat', json={'session_id': 'session-a', 'message': question})
                assert [item['error_code'] for item in events(result)] == ['TEXT_FILE_REATTACH_REQUIRED']
            comparison = await http.post(ASK, files=upload(), data={**body, 'message': 'Compare with the previous CSV.'})
            assert comparison.status_code == 409
            owned = (await http.get('/api/chat/sessions/session-a')).json()['messages']
            assert len(owned) == 1 and owned[0]['content'] == 'Question about an attached CSV file.'
            descriptor = owned[0]['attachments'][0]
            assert set(descriptor) == {'attachment_id', 'filename', 'mime_type', 'size_bytes'}
            assert descriptor['filename'] == 'sample.csv' and descriptor['mime_type'] == 'text/csv'
            assert not (await http.get('/api/chat/sessions/s')).json()['messages']
            assert len(provider.requests) == 1

    run_offline(exercise())
    persisted = (tmp_path / 'memory.json').read_text(encoding='utf-8')
    for private in ('private-row', 'next', 'What is selected?', 'The switch is amber.'):
        assert private not in persisted
    assert {path.name for path in tmp_path.iterdir()} <= {'memory.json', 'memory.json.lock', 'registry.db',
                                                        'registry.db-wal', 'registry.db-shm'}


def test_csv_analysis_honors_context_readiness_profile_and_provider_identity(tmp_path):
    provider = Provider()
    app = app_with_text_profile(tmp_path, provider)
    body = ask_form(row_start='1', row_end='1')

    async def exercise():
        async with client(app) as http:
            app.state.settings.local_chat_only = False
            assert (await http.post(ASK, files=upload(), data=body)).status_code == 403
            app.state.settings.local_chat_only = True
            identity, checked, success, digest = app.state.inference_observation
            app.state.inference_observation = (identity, time.monotonic() - INFERENCE_TTL - 1, success, digest)
            assert (await http.post(ASK, files=upload(), data=body)).status_code == 503
            app.state.inference_observation = (identity, checked, success, digest)
            oversized = upload(('Header\n' + '\n'.join('\\' * 125 for _ in range(9))).encode())
            result = await http.post(ASK, files=oversized, data={**body, 'row_end': '9', 'message': '\\' * 1024})
            assert events(result)[-1]['error_code'] == 'CSV_CONTEXT_LIMIT'
            provider._base_url = 'http://127.0.0.1:65535'
            result = await http.post(ASK, files=upload(), data=body)
            assert events(result)[-1]['error_code'] == 'MODEL_NOT_READY'
            provider._base_url = 'http://127.0.0.1:11434'
            app.state.model_router.get_provider_for_model = lambda _: (provider, 'wrong-model')
            result = await http.post(ASK, files=upload(), data=body)
            assert events(result)[-1]['error_code'] == 'MODEL_NOT_READY'
        assert not provider.requests

    run_offline(exercise())


def test_csv_model_tool_markup_is_rejected_without_completion_or_history(tmp_path):
    class InvalidProvider(Provider):
        async def chat_stream(self, request):
            yield ChatChunk('<tool_call', False)
            yield ChatChunk('>unsafe</tool_call>', True)

    app = app_with_text_profile(tmp_path, InvalidProvider())

    async def exercise():
        async with client(app) as http:
            response = await http.post(ASK, files=upload(), data=ask_form(row_start='1', row_end='1'))
            result = events(response)
            assert [item['type'] for item in result] == ['agent_status', 'error']
            assert result[-1]['error_code'] == 'INVALID_TOOL_REQUEST'
            assert not (await http.get('/api/chat/sessions/session-a')).json()['messages']

    run_offline(exercise())


def test_csv_cancel_closes_stream_without_persisting_turn(tmp_path):
    from backend.api.chat import _stream_text_file
    from backend.auth.request_context import dispatch_identity_resolver, effective_owner

    async def exercise():
        started, closed = asyncio.Event(), asyncio.Event()

        class PausingProvider(Provider):
            async def chat_stream(self, request):
                try:
                    started.set()
                    await asyncio.Event().wait()
                    yield ChatChunk('Incomplete', False)
                finally:
                    closed.set()

        app = app_with_text_profile(tmp_path, PausingProvider())
        engine = app.state.engine
        owner = effective_owner.set('a')
        resolver = dispatch_identity_resolver.set(lambda: app.state.auth_store.get_user_by_id('a'))
        try:
            async with aclosing(_stream_text_file(engine, 'session-a', SimpleNamespace(app=app), 'a',
                'ollama/text', 'Question', {'headers': ['x'], 'rows': [['y']], 'selected_row_count': 1},
                {'mime_type': 'text/csv'}, guard_initial_identity=engine._memory.stream_identity('session-a'))) as stream:
                assert 'agent_status' in await anext(stream)
                pending = asyncio.create_task(anext(stream))
                await started.wait()
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            assert closed.is_set() and all(m.role == 'system' for m in engine._memory.get_history('session-a'))
        finally:
            dispatch_identity_resolver.reset(resolver)
            effective_owner.reset(owner)

    run_offline(exercise())
