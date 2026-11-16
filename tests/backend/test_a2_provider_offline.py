"""Offline checks using in-memory clients; no application lifespan or sockets."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
import json
import pytest
import httpx
from backend.agent.engine import AgentEngine
from backend.models.base import ChatChunk, ChatRequest, Message
from backend.models.ollama_provider import OllamaProvider


def immediate(coro):
    try:
        try:
            coro.send(None)
        except StopIteration as result:
            return result.value
        raise AssertionError('unexpected asynchronous suspension')
    finally:
        coro.close()


@asynccontextmanager
async def no_clock(_):
    yield


class FakeResponse:
    def __init__(self, data=None, lines=(), status=200):
        self.data, self.lines, self.status = data, lines, status
    def raise_for_status(self):
        if self.status != 200:
            req = httpx.Request('POST', 'http://test.invalid/api/chat')
            raise httpx.HTTPStatusError('fake', request=req, response=httpx.Response(self.status, request=req))
    def json(self):
        return self.data
    async def aiter_lines(self):
        for line in self.lines:
            yield line
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass


def provider(monkeypatch, response=None, error=None):
    monkeypatch.setattr('backend.models.ollama_provider.asyncio.timeout', no_clock)
    obj = OllamaProvider.__new__(OllamaProvider)
    obj._timeout = 5
    obj._client = MagicMock()
    obj._client.post = AsyncMock(return_value=response, side_effect=error)
    obj._client.stream.return_value = response
    return obj


def request(): return ChatRequest(messages=[Message(role='user', content='Hello')], model='fake')


def test_provider_response_and_unavailable(monkeypatch):
    good = provider(monkeypatch, FakeResponse({'done': True, 'message': {'content': 'Hello'}}))
    assert immediate(good.chat(request())).content == 'Hello'
    missing = provider(monkeypatch, FakeResponse(status=404))
    with pytest.raises(RuntimeError, match='Selected model'):
        immediate(missing.chat(request()))
    timeout = provider(monkeypatch, error=httpx.ReadTimeout('fake'))
    with pytest.raises(RuntimeError, match='timed out'):
        immediate(timeout.chat(request()))
    bad = provider(monkeypatch, FakeResponse({'done': True, 'message': []}))
    with pytest.raises(RuntimeError, match='Invalid'):
        immediate(bad.chat(request()))


def test_provider_stream_completion_and_interruption(monkeypatch):
    async def collect(obj): return [c async for c in obj.chat_stream(request())]
    lines = [json.dumps({'done': False, 'message': {'content': 'Hi'}}), json.dumps({'done': True, 'message': {'content': '!'}})]
    chunks = immediate(collect(provider(monkeypatch, FakeResponse(lines=lines))))
    assert [(c.delta, c.done) for c in chunks] == [('Hi', False), ('!', True)]
    with pytest.raises(RuntimeError, match='interrupted'):
        immediate(collect(provider(monkeypatch, FakeResponse(lines=lines[:1]))))
    with pytest.raises(RuntimeError, match='Invalid'):
        immediate(collect(provider(monkeypatch, FakeResponse(lines=['not json']))))


def test_truncated_terminal_reason_is_not_a_completed_answer(monkeypatch):
    truncated = {'done': True, 'done_reason': 'length', 'message': {'content': 'partial'}}
    with pytest.raises(RuntimeError, match='Incomplete'):
        immediate(provider(monkeypatch, FakeResponse(truncated)).chat(request()))
    async def collect(obj): return [chunk async for chunk in obj.chat_stream(request())]
    with pytest.raises(RuntimeError, match='Incomplete'):
        immediate(collect(provider(monkeypatch, FakeResponse(lines=[json.dumps(truncated)]))))


def test_truncated_provider_stream_never_persists_or_emits_done(tmp_path, monkeypatch):
    from test_a21_conversation_offline import make_app, setup_engine
    from test_a22_async_offline import run_offline
    engine, _ = setup_engine(tmp_path)
    lines = [json.dumps({'done': False, 'message': {'content': 'Partial response before cutoff'}}),
             json.dumps({'done': True, 'done_reason': 'length', 'message': {'content': ''}})]
    fake_provider = provider(monkeypatch, FakeResponse(lines=lines))
    engine._router.get_provider_for_model = lambda _: (fake_provider, 'fake')
    app = make_app(engine)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            response = await client.post('/api/chat', json={'session_id': 's', 'message': 'Explain pumps',
                                                            'stream': True, 'tools_enabled': False})
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert events[-1]['type'] == 'error'
            assert all(event['type'] != 'done' for event in events)
            history = await client.get('/api/chat/sessions/s')
            assert history.status_code == 200
            assistant = [message for message in history.json()['messages'] if message['role'] == 'assistant']
            assert len(assistant) == 1
            assert assistant[0]['response_state'] == 'failed'
            assert assistant[0]['content'] == ''.join(event.get('content', '') for event in events if event['type'] == 'delta')
    run_offline(exercise())


def test_provider_closes_line_iterator_on_consumer_cancellation(monkeypatch):
    closed = []
    class Response(FakeResponse):
        async def aiter_lines(self):
            try:
                yield json.dumps({'done': False, 'message': {'content': 'partial'}})
                yield json.dumps({'done': True, 'message': {'content': 'end'}})
            finally:
                closed.append(True)
    async def exercise():
        stream = provider(monkeypatch, Response()).chat_stream(request())
        assert (await stream.__anext__()).delta == 'partial'
        await stream.aclose()
    immediate(exercise())
    assert closed == [True]


def test_real_engine_does_not_persist_incomplete_stream():
    engine = AgentEngine.__new__(AgentEngine)
    engine._ensure_session = MagicMock()
    engine._memory = MagicMock()
    engine._router = MagicMock()
    engine._agent_config = {}
    engine._retrieve_context = AsyncMock(return_value=[])
    engine._build_messages = MagicMock(return_value=[])
    fake = MagicMock(); fake.provider_name = 'fake'
    async def stream(_):
        yield ChatChunk(delta='partial', done=False)
    fake.chat_stream = stream
    engine._router.get_provider_for_model.return_value = (fake, 'fake')
    async def collect(): return [x async for x in engine.chat_stream('s', 'hello')]
    with pytest.raises(RuntimeError, match='interrupted'):
        immediate(collect())
    engine._memory.add_assistant_message.assert_not_called()


def test_engine_streams_and_rejects_split_tool_syntax():
    def make_engine(parts):
        engine = AgentEngine.__new__(AgentEngine)
        engine._ensure_session = MagicMock(); engine._memory = MagicMock(); engine._router = MagicMock()
        engine._agent_config = {}; engine._retrieve_context = AsyncMock(return_value=[])
        engine._build_messages = MagicMock(return_value=[])
        fake = MagicMock(); fake.provider_name = 'fake'
        async def stream(_):
            for part in parts: yield part
        fake.chat_stream = stream
        engine._router.get_provider_for_model.return_value = (fake, 'fake')
        return engine
    async def collect(engine): return [x async for x in engine.chat_stream('s', 'hello')]
    engine = make_engine([ChatChunk('Hello ', False), ChatChunk('world!', True)])
    assert immediate(collect(engine)) == ['H', 'ello world!', []]
    engine._memory.add_assistant_message.assert_called_once()
    engine = make_engine([ChatChunk('text <tool_', False), ChatChunk('call>{}</tool_call>', True)])
    with pytest.raises(RuntimeError, match='Invalid'):
        immediate(collect(engine))
    engine._memory.add_assistant_message.assert_not_called()
