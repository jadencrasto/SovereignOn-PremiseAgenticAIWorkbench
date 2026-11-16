"""Offline checks using in-memory clients; no application lifespan or sockets."""
from contextlib import aclosing, asynccontextmanager
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


def reply(content, *, done=False, model='fake:latest', **fields):
    return {'model': model, 'done': done, 'message': {'role': 'assistant', 'content': content}, **fields}


def test_provider_response_and_unavailable(monkeypatch):
    good = provider(monkeypatch, FakeResponse(reply('Hello', done=True)))
    assert immediate(good.chat(request())).content == 'Hello'
    missing = provider(monkeypatch, FakeResponse(status=404))
    with pytest.raises(RuntimeError, match='Selected model'):
        immediate(missing.chat(request()))
    timeout = provider(monkeypatch, error=httpx.ReadTimeout('fake'))
    with pytest.raises(RuntimeError, match='timed out'):
        immediate(timeout.chat(request()))
    bad = provider(monkeypatch, FakeResponse({'model': 'fake', 'done': True, 'message': []}))
    with pytest.raises(RuntimeError, match='Invalid'):
        immediate(bad.chat(request()))


def test_ollama_vision_serialization_and_model_mismatch(monkeypatch):
    image_request = ChatRequest(messages=[
        Message(role='system', content='Describe visible content.'),
        Message(role='user', content='What color?', images=['AA==']),
    ], model='vision:7b')
    good = provider(monkeypatch, FakeResponse(reply('Blue.', done=True, model='vision:7b', done_reason='stop')))
    assert immediate(good.chat(image_request)).content == 'Blue.'
    path = good._client.post.await_args.args[0]
    payload = good._client.post.await_args.kwargs['json']
    assert path == '/api/chat' and payload['model'] == 'vision:7b' and payload['stream'] is False
    assert 'images' not in payload['messages'][0]
    assert payload['messages'][1] == {'role': 'user', 'content': 'What color?', 'images': ['AA==']}
    wrong_model = provider(monkeypatch, FakeResponse(reply('Blue.', done=True, model='text:7b')))
    with pytest.raises(RuntimeError, match='Invalid model response'):
        immediate(wrong_model.chat(image_request))


def test_provider_stream_completion_and_interruption(monkeypatch):
    async def collect(obj): return [c async for c in obj.chat_stream(request())]
    lines = [json.dumps(reply('Hi')), json.dumps(reply('!', done=True))]
    chunks = immediate(collect(provider(monkeypatch, FakeResponse(lines=lines))))
    assert [(c.delta, c.done) for c in chunks] == [('Hi', False), ('!', True)]
    with pytest.raises(RuntimeError, match='interrupted'):
        immediate(collect(provider(monkeypatch, FakeResponse(lines=lines[:1]))))
    with pytest.raises(RuntimeError, match='Invalid'):
        immediate(collect(provider(monkeypatch, FakeResponse(lines=['not json']))))


def test_ollama_chat_request_and_fragmented_ndjson():
    from test_a22_async_offline import run_offline

    class FragmentedReply(httpx.AsyncByteStream):
        async def __aiter__(self):
            first = json.dumps(reply('Ready.')).encode()
            yield first[:17]
            yield first[17:] + b'\n\n'
            # Exercise EOF flushing with a terminal record that lacks a final newline.
            yield json.dumps(reply('', done=True, done_reason='stop')).encode()

    calls = []

    def handle(http_request):
        assert http_request.method == 'POST'
        assert http_request.url.path == '/api/chat'
        payload = json.loads(http_request.content)
        assert payload['model'] == 'fake'
        assert payload['messages'] == [{'role': 'user', 'content': 'Hello'}]
        calls.append(payload['stream'])
        if payload['stream']:
            return httpx.Response(200, stream=FragmentedReply(), headers={'Content-Type': 'application/x-ndjson'})
        return httpx.Response(200, json=reply('Ready.', done=True, done_reason='stop'))

    async def exercise():
        async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', transport=httpx.MockTransport(handle)) as client:
            adapter = OllamaProvider.__new__(OllamaProvider)
            adapter._client = client
            adapter._timeout = 1
            assert (await adapter.chat(request())).content == 'Ready.'
            chunks = [chunk async for chunk in adapter.chat_stream(request())]
            assert [(chunk.delta, chunk.done) for chunk in chunks] == [('Ready.', False), ('', True)]

    run_offline(exercise())
    assert calls == [False, True]


def test_truncated_terminal_reason_is_not_a_completed_answer(monkeypatch):
    truncated = reply('partial', done=True, done_reason='length')
    with pytest.raises(RuntimeError, match='Incomplete'):
        immediate(provider(monkeypatch, FakeResponse(truncated)).chat(request()))
    async def collect(obj): return [chunk async for chunk in obj.chat_stream(request())]
    with pytest.raises(RuntimeError, match='Incomplete'):
        immediate(collect(provider(monkeypatch, FakeResponse(lines=[json.dumps(truncated)]))))


@pytest.mark.parametrize('bad_model', [None, '', 'different:latest', 'fake:other'])
def test_chat_rejects_missing_or_mismatched_response_model(monkeypatch, bad_model):
    data = reply('Wrong model reply', model=bad_model, done=True)
    if bad_model is None:
        del data['model']
    with pytest.raises(RuntimeError, match='Invalid model response'):
        immediate(provider(monkeypatch, FakeResponse(data)).chat(request()))


@pytest.mark.parametrize('message', [{'content': 'Invalid reply'},
                                    {'role': 'user', 'content': 'Invalid reply'},
                                    {'role': 'tool', 'content': 'Invalid reply'}],
                         ids=['missing-role', 'user-role', 'tool-role'])
def test_provider_rejects_non_assistant_messages(monkeypatch, message):
    terminal = reply('', done=True, message=message)
    with pytest.raises(RuntimeError, match='Invalid model response'):
        immediate(provider(monkeypatch, FakeResponse(terminal)).chat(request()))

    async def next_chunk(obj):
        async with aclosing(obj.chat_stream(request())) as stream:
            return await anext(stream)

    nonterminal = {**terminal, 'done': False}
    with pytest.raises(RuntimeError, match='Invalid model response'):
        immediate(next_chunk(provider(monkeypatch, FakeResponse(lines=[json.dumps(nonterminal)]))))


@pytest.mark.parametrize('terminal', [reply('', done=True, done_reason='length'),
                                      reply('untrusted text', done=True, model='wrong:latest'),
                                      reply('', done=True, message={'role': 'user', 'content': 'untrusted text'})])
def test_invalid_provider_completion_never_emits_done(tmp_path, monkeypatch, terminal):
    from test_a21_conversation_offline import make_app, setup_engine
    from test_a22_async_offline import run_offline
    engine, _ = setup_engine(tmp_path)
    lines = [json.dumps(reply('Partial response before cutoff')), json.dumps(terminal)]
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
            assert 'untrusted text' not in response.text
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
                yield json.dumps(reply('partial'))
                yield json.dumps(reply('end', done=True))
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
