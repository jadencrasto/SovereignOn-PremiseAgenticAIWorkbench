"""Timer-only event loop; fake clients, no sockets, services, or listeners."""
import asyncio
import selectors
import socket
import time
from threading import get_ident
from unittest.mock import patch
import pytest
from backend.models.base import ChatRequest, Message
from backend.models.ollama_provider import OllamaProvider

class TimerSelector(selectors.BaseSelector):
    def __init__(self): self._map = {}
    def register(self, *args, **kwargs): raise AssertionError('I/O registration forbidden')
    def unregister(self, *args, **kwargs): raise AssertionError('I/O registration forbidden')
    def select(self, timeout=None):
        if timeout: time.sleep(timeout)
        return []
    def get_map(self): return self._map
    def close(self): pass

class TimerLoop(asyncio.SelectorEventLoop):
    def __init__(self): super().__init__(TimerSelector())
    def _make_self_pipe(self): pass
    def _close_self_pipe(self): pass
    def _write_to_self(self): raise AssertionError('cross-thread wakeup forbidden')

    def _asyncgen_finalizer_hook(self, generator):
        # CPython's default hook wakes the self-pipe even for finalization on
        # the loop thread. Close these generators without socket I/O.
        if self._thread_id == get_ident():
            self._asyncgens.discard(generator)
            if not self.is_closed():
                self.call_soon(self.create_task, generator.aclose())
        else:
            super()._asyncgen_finalizer_hook(generator)

def run_offline(coro):
    with patch.object(socket, 'socket', side_effect=AssertionError('socket creation forbidden')):
        with asyncio.Runner(loop_factory=TimerLoop) as runner:
            return runner.run(coro)

def request(): return ChatRequest(messages=[Message(role='user', content='synthetic')], model='fake')

def fake_provider(client, timeout):
    provider = OllamaProvider.__new__(OllamaProvider)
    provider._timeout = timeout; provider._client = client
    return provider

def test_real_elapsed_timeout_cancels_request():
    class Client:
        closed = False
        async def post(self, *args, **kwargs):
            try: await asyncio.sleep(1)
            finally: self.closed = True
    async def run():
        client = Client(); provider = fake_provider(client, .04)
        start = time.monotonic()
        with pytest.raises(RuntimeError, match='timed out'):
            await provider.chat(request())
        elapsed = time.monotonic() - start
        assert .03 <= elapsed < .8 and client.closed
    run_offline(run())

def test_stream_cancellation_exits_provider_context():
    class Response:
        closed = False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): self.closed = True
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield '{"model":"fake","done":false,"message":{"role":"assistant","content":"partial"}}'
            await asyncio.sleep(1)
    class Client:
        def __init__(self): self.response = Response()
        def stream(self,*args,**kwargs): return self.response
    async def run():
        client = Client(); stream = fake_provider(client, 2).chat_stream(request())
        assert (await anext(stream)).delta == 'partial'
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert client.response.closed
    run_offline(run())

def test_stream_real_deadline_and_cleanup():
    class Response:
        closed = False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): self.closed = True
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield '{"model":"fake","done":false,"message":{"role":"assistant","content":"partial"}}'
            await asyncio.sleep(1)
    class Client:
        def __init__(self): self.response = Response()
        def stream(self,*args,**kwargs): return self.response
    async def run():
        client = Client(); start = time.monotonic(); collected=[]
        with pytest.raises(RuntimeError, match='timed out'):
            async for chunk in fake_provider(client, .04).chat_stream(request()): collected.append(chunk.delta)
        assert collected == ['partial'] and client.response.closed
        assert .03 <= time.monotonic()-start < .8
    run_offline(run())


def test_asgi_simulated_disconnect_closes_provider(tmp_path):
    import json
    from backend.auth.request_context import effective_owner
    from starlette.requests import ClientDisconnect
    from backend.models.base import ChatChunk
    from test_a21_conversation_offline import setup_engine, make_app
    engine, _ = setup_engine(tmp_path); closed=[]
    class Provider:
        provider_name='fake'
        async def chat_stream(self, request):
            try:
                yield ChatChunk('long enough for an incremental response', False)
                await asyncio.sleep(1)
                yield ChatChunk('end', True)
            finally: closed.append(True)
    engine._router.get_provider_for_model=lambda _: (Provider(),'fake')
    app=make_app(engine)
    body=json.dumps({'session_id':'s','message':'hello','stream':True,'tools_enabled':False}).encode()
    scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1','method':'POST','scheme':'http','path':'/api/chat','raw_path':b'/api/chat','query_string':b'', 'root_path':'','headers':[(b'authorization',b'Bearer a'),(b'content-type',b'application/json')], 'client':('test',1),'server':('test',80)}
    async def exercise():
        received=False
        attempted = []
        async def receive():
            nonlocal received
            if not received:
                received=True;return {'type':'http.request','body':body,'more_body':False}
            await asyncio.sleep(1)
            return {'type':'http.disconnect'}
        async def send(message):
            if message['type']=='http.response.body' and message.get('body'):
                attempted.append(json.loads(message['body'][6:]))
                raise OSError('simulated disconnect')
        try: await app(scope,receive,send)
        except ClientDisconnect: pass
        await asyncio.sleep(.01)
        assert closed == [True]
        token=effective_owner.set('a')
        try:
            replies = [m for m in engine._memory.get_history('s') if m.role == 'assistant']
            assert [event['type'] for event in attempted] == ['delta']
            assert len(replies) == 1
            assert replies[0].extra_fields['response_state_v1'] == 'interrupted'
            assert replies[0].content == attempted[0]['content']
        finally: effective_owner.reset(token)
    run_offline(exercise())
