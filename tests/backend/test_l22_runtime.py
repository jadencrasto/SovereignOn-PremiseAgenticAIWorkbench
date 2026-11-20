"""Local runtime regressions through the Ollama adapter and owned ASGI turns."""

import asyncio
import json
from contextlib import aclosing
from types import SimpleNamespace

import httpx
import pytest

from backend.health import routes as health
from backend.models.base import ChatRequest, Message
from backend.models.ollama_provider import OllamaProvider
from test_l1_local_dev import local_settings
from test_l2_local_turns import MODEL, api_client, history_path, login, new_turn, parse_events, workbench
from test_l16_local_images import wire_provider


def test_concurrent_turns_share_the_current_inventory_probe(monkeypatch):
    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []
        state = SimpleNamespace(settings=local_settings(), local_inventory_lock=asyncio.Lock())

        async def observe(target, *, local_only):
            calls.append((target, local_only))
            entered.set()
            await release.wait()
            return health.ModelObservation('available', 'inventory_observed', (MODEL,))

        monkeypatch.setattr(health, 'observe_ollama', observe)
        running = asyncio.create_task(health.refresh_local_inventory(state))
        await entered.wait()
        waiter = asyncio.create_task(health.refresh_local_inventory(state))
        stopped = asyncio.create_task(health.refresh_local_inventory(state))
        await asyncio.sleep(0)
        stopped.cancel()
        release.set()
        await asyncio.gather(running, waiter)
        with pytest.raises(asyncio.CancelledError):
            await stopped
        assert calls == [('http://127.0.0.1:11434/api/tags', True)]
        await health.refresh_local_inventory(state)
        assert len(calls) == 2

    asyncio.run(exercise())


def ollama_line(content, *, done=False):
    return (json.dumps({'model': MODEL, 'done': done, 'message': {'role': 'assistant', 'content': content}}) + '\n').encode()


def test_provider_deadline_does_not_cancel_the_consumer_between_reads(monkeypatch):
    class ResponseStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield ollama_line('Hi')
            await asyncio.sleep(1)

        async def aclose(self):
            self.closed = True

    async def exercise():
        provider = OllamaProvider('http://127.0.0.1:11434', timeout=.05)
        await provider.aclose()
        body = ResponseStream()
        transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=body))
        async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', transport=transport) as client:
            monkeypatch.setattr(provider, '_client', client)
            request = ChatRequest(messages=[Message('user', 'Hello')], model=MODEL)
            async with aclosing(provider.chat_stream(request)) as stream:
                assert (await anext(stream)).delta == 'Hi'
                await asyncio.sleep(.08)
                with pytest.raises(RuntimeError, match='Model request timed out'):
                    await anext(stream)
            assert body.closed

    asyncio.run(exercise())


@pytest.mark.parametrize('raw_tool_markup', [False, True])
def test_short_first_token_streams_before_completion_with_split_markup_fenced(workbench, capsys, monkeypatch, raw_tool_markup):
    async def exercise():
        first_delta = asyncio.Event()
        finish_reply = asyncio.Event()
        payload = {}
        observed = []

        class ResponseStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield ollama_line('Hi <To' if raw_tool_markup else 'Hi')
                await finish_reply.wait()
                yield ollama_line('Ol_CaLl>' if raw_tool_markup else ' there')
                yield ollama_line('', done=True)

        async def respond(request):
            assert request.url.path == '/api/chat'
            payload.update(json.loads(request.content))
            return httpx.Response(200, stream=ResponseStream())

        async def app(scope, receive, send):
            async def observe(message):
                if b'"type": "delta"' in (message.get('body') or b''):
                    observed.extend(json.loads(line[6:])['content'] for line in message['body'].splitlines()
                                    if line.startswith(b'data: '))
                    first_delta.set()
                await send(message)

            await workbench(scope, receive, observe)

        async with workbench.router.lifespan_context(workbench), api_client(app) as client:
            await login(client, capsys)
            wire_provider(workbench, monkeypatch, respond)
            turn = await new_turn(client, message='Please greet me.')
            running = asyncio.create_task(client.post('/api/chat', json=turn))
            try:
                await asyncio.wait_for(first_delta.wait(), timeout=3)
                assert observed == (['Hi '] if raw_tool_markup else ['Hi'])
                assert not running.done()
            finally:
                finish_reply.set()
                response = await running
            assert payload['model'] == MODEL and payload['stream'] is True
            assert payload['messages'][-1] == {'role': 'user', 'content': turn['message']}
            events = parse_events(response)
            assert events[1]['content'] == 'awaiting_model'
            history = (await client.get(history_path(turn))).json()['messages']
            if raw_tool_markup:
                assert events[-1]['type'] == 'error' and events[-1]['error_code'] == 'MODEL_INCOMPLETE'
                assert len(history) == 1 and history[0]['turn']['state'] == 'failed'
                assert observed == ['Hi ']
            else:
                assert events[-1]['type'] == 'done' and events[-1]['model_used'] == f'ollama/{MODEL}'
                assert [message['content'] for message in history] == [turn['message'], 'Hi there']

    asyncio.run(exercise())
