"""Offline production-route coverage; no services or sockets are started."""
import json

import httpx
import pytest

from backend.models.base import ChatChunk, ChatResponse
from backend.tools.unit_conversion import register_unit_conversion
from test_a21_conversation_offline import make_app
from test_a22_async_offline import run_offline
from test_a31_planning_offline import setup_workflow


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def test_owned_streamed_workflow_and_history(tmp_path):
    plan = json.dumps([{'description': 'Convert units', 'tool_name': 'unit_conversion',
                        'arguments': {'value': 5.0, 'from_unit': 'km', 'to_unit': 'm'}}])
    engine = setup_workflow(tmp_path, plan)
    register_unit_conversion(engine._tool_registry)

    class Provider:
        provider_name = 'fake'
        async def chat(self, request):
            return ChatResponse(content=plan, model='fake', provider='fake')

        async def chat_stream(self, request):
            if any(m.role == 'user' and m.content == 'Convert 5 kilometres to metres.' for m in request.messages):
                yield ChatChunk('<tool_call>{"name":"unit_conversion","arguments":{"value":5,"from_unit":"km","to_unit":"m"}}</tool_call>', True)
            else:
                yield ChatChunk('I can help with questions.', False)
                yield ChatChunk('', True)

    engine._router.get_provider_for_model = lambda _: (Provider(), 'fake')
    engine._max_tool_iterations = 3
    app = make_app(engine)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            created = await client.post('/api/chat/sessions')
            assert created.status_code == 201
            sid = created.json()['session_id']

            async def turn(message):
                response = await client.post('/api/chat', json={'session_id': sid, 'message': message,
                    'stream': True, 'tools_enabled': True})
                assert response.status_code == 200
                return events(response)

            ordinary = await turn('Hello. What can you help me with?')
            assert any('I can help' in e.get('content', '') for e in ordinary)
            assert not any(e['type'].startswith('tool_') for e in ordinary)
            assert ordinary[-1]['type'] == 'done'
            for message, expected in [('What is 25 + 17?', 'Result: 42'),
                                      ('Multiply that result by 3.', 'Result: 126'),
                                      ('Add 5 to that result.', 'Result: 131'),
                                      ('Convert 5 kilometres to metres.', 'Result: 5000.0 m')]:
                stream = await turn(message)
                assert [e['summary'] for e in stream if e['type'] == 'tool_result' and e['success']] == [expected], stream
                assert any(e['type'] == 'task_completed' for e in stream)
                assert stream[-1]['type'] == 'done'
            assert (await client.patch(f'/api/chat/sessions/{sid}/title', json={'title': 'Calculations'})).status_code == 200
            assert (await client.get(f'/api/chat/sessions/{sid}')).status_code == 200
            assert (await client.get(f'/api/chat/sessions/{sid}', headers={'Authorization': 'Bearer b'})).status_code == 404
            assert (await client.delete(f'/api/chat/sessions/{sid}')).status_code == 200
            assert (await client.get(f'/api/chat/sessions/{sid}')).status_code == 404
    run_offline(exercise())


def test_planning_error_is_terminal_on_production_route(tmp_path):
    engine = setup_workflow(tmp_path, '[]')
    async def failed(*args, **kwargs):
        yield {'type': 'error', 'content': 'synthetic failure'}
        yield 'must not be emitted'
    engine.run_agent_task = failed
    app = make_app(engine)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            response = await client.post('/api/chat', json={'session_id': 's', 'message': 'What is 25 + 17?',
                                                           'stream': True, 'tools_enabled': True})
            assert [e['type'] for e in events(response)] == ['error']
            assert 'must not be emitted' not in response.text
    run_offline(exercise())


@pytest.mark.parametrize('terminal', ['task_failed', 'task_cancelled'])
def test_planning_failure_and_cancellation_are_terminal(tmp_path, terminal):
    engine = setup_workflow(tmp_path, '[]')
    closed = []

    async def interrupted(*args, **kwargs):
        try:
            yield 'partial response'
            yield {'type': terminal, 'task_id': 'synthetic-task'}
            yield 'must not be emitted'
        finally:
            closed.append(True)

    engine.run_agent_task = interrupted
    app = make_app(engine)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            response = await client.post('/api/chat', json={'session_id': 's', 'message': 'What is 25 + 17?',
                                                           'stream': True, 'tools_enabled': True})
            assert [e['type'] for e in events(response)] == ['delta', terminal]
            assert 'must not be emitted' not in response.text
            assert closed == [True]
    run_offline(exercise())
