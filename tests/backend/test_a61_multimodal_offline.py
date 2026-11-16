"""Offline multimodal foundations; no services or network."""
import asyncio
import base64
import io
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image
from openpyxl import Workbook
from backend.multimodal.image_processor import ImageProcessor, ImageValidationError
from backend.multimodal.service import MultimodalService
from backend.multimodal.spreadsheet import parse_spreadsheet, numeric_summary
from backend.models.base import ChatResponse
from backend.models.router import ModelRouter
from backend.rag.ingest import DocumentParser
from backend.security.provenance_foundation import document_content_allowed
from test_a22_async_offline import run_offline


def image_bytes(fmt='PNG', size=(8, 6)):
    out=io.BytesIO(); Image.new('RGB',size,'blue').save(out,format=fmt); return out.getvalue()


def image_chat_app(tmp_path, provider=None, engine=None, *, checked=True):
    from backend.health import routes
    from backend.health.capabilities import ModelObservation
    from backend.security.request_limits import MultipartBodyLimitMiddleware
    from test_a21_conversation_offline import make_app, setup_engine

    if engine is None:
        engine, _ = setup_engine(tmp_path)
    engine._max_tool_iterations = 2
    router = engine._router
    router.default_model_id = 'ollama/text'
    router.configured_model_id = lambda capability: {'chat': 'ollama/text', 'vision': 'ollama/vision'}.get(capability)
    router._config = {'models': {
        'ollama/text': {'capabilities': ['chat']},
        'ollama/vision': {'capabilities': ['vision', 'chat']},
        'ollama/other-vision': {'capabilities': ['vision']},
    }}
    router.resolve_model = lambda model: (model or 'ollama/text').split('/', 1)
    if provider is not None:
        router.get_provider_for_model = lambda model: (provider, model.removeprefix('ollama/'))
    app = make_app(engine)
    app.include_router(routes.router)
    app.add_middleware(MultipartBodyLimitMiddleware, max_file_bytes=50 * 1024 * 1024)
    app.state.upload_dir = tmp_path
    app.state.settings.ollama_base_url = 'http://127.0.0.1:11434'
    app.state.settings.ollama_default_model = 'text'
    app.state.settings.embedding_model = None
    app.state.startup_complete = True
    app.state.enforce_model_readiness = True
    inventory = ModelObservation('available', 'inventory_observed',
                                 ('text:latest', 'vision:latest', 'other-vision:latest', 'unconfigured:latest'),
                                 (('vision:latest', 'a' * 64), ('other-vision:latest', 'b' * 64)))
    app.state.model_observation = (routes._tags_url(app.state), time.monotonic(), inventory)
    if checked:
        app.state.inference_observation = (routes._identity(app.state, 'ollama/text'), time.monotonic(), True, None)
        app.state.vision_observation = (
            routes._identity(app.state, 'ollama/vision', 'vision'), time.monotonic(), True, 'a' * 64)
    return app


def image_form(**data):
    return {'data': {'session_id': 'session-a', 'message': 'Describe this image', **data},
            'files': {'image': ('sample.png', image_bytes(), 'image/png')}}


@pytest.mark.parametrize('fmt,name', [('PNG','a.png'),('JPEG','a.jpg'),('WEBP','a.webp')])
def test_real_image_decoding_and_identity(tmp_path,fmt,name):
    proc=ImageProcessor(tmp_path); data=image_bytes(fmt)
    image=proc.process(data,name,save_to_disk=False)
    assert (image.width,image.height)==(8,6) and image.original_filename==name
    assert not image.storage_path.exists()
    assert image.attachment_id != proc.process(data,name,save_to_disk=False).attachment_id


def test_bad_images_and_dimension_limits(tmp_path):
    proc=ImageProcessor(tmp_path,max_size_bytes=100000,max_dimension=5)
    with pytest.raises(ImageValidationError): proc.process(image_bytes(),'a.png',save_to_disk=False)
    with pytest.raises(ImageValidationError): proc.process(image_bytes('WEBP'),'a.webp',save_to_disk=False)
    with pytest.raises(ImageValidationError): ImageProcessor(tmp_path).process(image_bytes()[:-10],'a.png',save_to_disk=False)
    with pytest.raises(ImageValidationError): ImageProcessor(tmp_path).process(image_bytes(),'a.jpg',save_to_disk=False)
    with pytest.raises(ImageValidationError): ImageProcessor(tmp_path,max_size_bytes=1).process(image_bytes(),'a.png',save_to_disk=False)


def test_vision_request_and_provider_failure(tmp_path):
    data=ImageProcessor(tmp_path).process(image_bytes(),'a.png',save_to_disk=False)
    provider=MagicMock();provider.chat=AsyncMock(return_value=ChatResponse('Model interpretation','fake','fake'))
    async def run():
        result=await MultimodalService(provider,'vision').analyze_image(data.base64_data,'Describe')
        request=provider.chat.await_args.args[0]
        assert result=='Model interpretation' and request.model=='vision'
        assert request.messages[1].images==[data.base64_data] and request.messages[1].content=='Describe'
        provider.chat=AsyncMock(return_value=ChatResponse('','fake','fake'))
        with pytest.raises(RuntimeError): await MultimodalService(provider,'vision').analyze_image(data.base64_data,'Describe')
    run_offline(run())


def test_vision_cancellation_propagates():
    closed=[]
    class Provider:
        async def chat(self, request):
            try: await asyncio.sleep(1)
            finally: closed.append(True)
    async def run():
        task=asyncio.create_task(MultimodalService(Provider(),'vision').analyze_image('synthetic','Question'))
        await asyncio.sleep(.01);task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert closed==[True]
    run_offline(run())


def test_router_rejects_text_only_vision_configuration():
    router=ModelRouter.__new__(ModelRouter)
    router._config={'capability_routing':{'vision':'fake/text'},'models':{'fake/text':{'capabilities':['chat']}}}
    router.get_provider_for_model=MagicMock()
    with pytest.raises(ValueError,match='does not support'): router.resolve_vision_model()
    router.get_provider_for_model.assert_not_called()
    router._config['models']['fake/text']['capabilities'].append('vision')
    router.resolve_vision_model();router.get_provider_for_model.assert_called_once_with('fake/text')


def workbook_bytes():
    w=Workbook();s=w.active;s.title='Measurements';s.append(['Name','Value']);s.append(['A',10]);s.append(['B',20]);s.append(['C',None]);s.append(['D','=1+1'])
    b=io.BytesIO();w.save(b);w.close();return b.getvalue()


def test_spreadsheet_summary_and_formulas_unevaluated():
    table=parse_spreadsheet('measurements.xlsx',workbook_bytes())
    assert table.filename=='measurements.xlsx' and table.sheets[0].name=='Measurements'
    assert table.sheets[0].rows[4][1]=='=1+1'
    assert numeric_summary(table.sheets[0],1)=={'count':2,'missing':1,'nonnumeric':1,'mean':15,'max':20}


def test_csv_missing_and_text_values():
    table=parse_spreadsheet('sample.csv',b'name,value\r\na,10\r\nb,\r\n')
    assert table.sheets[0].rows[2][1] is None
    assert numeric_summary(table.sheets[0],1)=={'count':0,'missing':1,'nonnumeric':1,'mean':None,'max':None}


@pytest.mark.parametrize('name,data',[('x.xlsx',b'bad'),('x.xlsm',b'PK\x03\x04'),('x.csv',b'\xff'),('x.csv',b''),('../x.csv',b'a')])
def test_malformed_spreadsheet_rejected(name,data):
    with pytest.raises(ValueError): parse_spreadsheet(name,data)


def test_docx_comparison_keeps_source_identity():
    from docx import Document
    def sample(value):
        doc=Document();doc.add_paragraph(value);out=io.BytesIO();doc.save(out);return out.getvalue()
    first=DocumentParser.parse('a.docx',sample('Pump flow 10'))
    second=DocumentParser.parse('b.docx',sample('Pump flow 20'))
    assert first.document_id != second.document_id
    assert ('10' in first.text and '20' in second.text and first.filename=='a.docx' and second.filename=='b.docx')
    assert not document_content_allowed()

def test_real_chat_route_image_integration_and_ownership(tmp_path):
    from backend.models.base import ChatChunk
    calls=[]
    class FakeProvider:
        provider_name='synthetic'
        async def chat(self,request):
            calls.append(request)
            return ChatResponse('A blue object is visible.','vision','synthetic')
        async def chat_stream(self,request):
            calls.append(request)
            yield ChatChunk('The image appears blue.',True)
    app = image_chat_app(tmp_path, FakeProvider())
    app.state.engine._retrieve_context = AsyncMock(side_effect=AssertionError('Image chat must not retrieve documents'))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            kwargs = image_form(vision_model='ollama/vision', tools_enabled='true')
            denied=await client.post('/api/chat/multimodal',headers={'Authorization':'Bearer b'},**kwargs)
            assert denied.status_code==404 and not calls
            response=await client.post('/api/chat/multimodal',headers={'Authorization':'Bearer a'},**kwargs)
            assert response.status_code==200 and '"type":"done"' in response.text and 'appears blue' in response.text
            assert [request.model for request in calls] == ['vision', 'text']
            assert base64.b64decode(calls[0].messages[-1].images[0], validate=True) == image_bytes()
            assert all(not message.images for message in calls[1].messages)
            assert any(message.role == 'user' and 'UNTRUSTED' in message.content for message in calls[1].messages)
            history = await client.get('/api/chat/sessions/session-a', headers={'Authorization': 'Bearer a'})
            messages = history.json()['messages']
            attachment, = messages[0]['attachments']
            assert attachment['filename'] == 'sample.png'
            assert set(attachment) == {'attachment_id', 'filename', 'mime_type', 'size_bytes'}
            assert messages[1]['withheld']
            denied_history = await client.get('/api/chat/sessions/session-a', headers={'Authorization': 'Bearer b'})
            assert denied_history.status_code == 404
            assert not list((tmp_path/'images').glob('*'))
    run_offline(run())

def test_real_image_route_provider_failure_is_error_not_completion(tmp_path):
    class FailingProvider:
        async def chat(self,request): raise RuntimeError('synthetic failure')
    app = image_chat_app(tmp_path, FailingProvider())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/api/chat/multimodal',headers={'Authorization':'Bearer a'},data={'session_id':'session-a','message':'Describe','tools_enabled':'false'},files={'image':('x.png',image_bytes(),'image/png')})
            assert '"type":"error"' in response.text and '"type":"done"' not in response.text
            assert 'synthetic failure' not in response.text
    run_offline(run())


@pytest.mark.parametrize('markup', [
    '<tool_call>{malformed reasoning}</tool_call>',
    '<tool_call>{"name":"unit_conversion","arguments":{"value":5,"from_unit":"km","to_unit":"m"}}</tool_call>',
])
def test_real_image_route_rejects_malformed_and_valid_tool_calls(tmp_path, markup):
    from backend.models.base import ChatChunk
    from test_a33_production_tools_offline import setup_conversion

    engine = setup_conversion(tmp_path, [])
    engine._max_tool_iterations = 2
    execute = AsyncMock()
    engine._tool_registry.get('unit_conversion').execute_fn = execute

    class Provider:
        provider_name = 'synthetic'

        async def chat(self, request):
            return ChatResponse('The image contains a blue square.', 'vision', 'synthetic')

        async def chat_stream(self, request):
            yield ChatChunk(markup, True)

    app = image_chat_app(tmp_path, Provider(), engine)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            response = await client.post('/api/chat/multimodal',
                                         data={'session_id': 's', 'message': 'Describe image', 'tools_enabled': 'true'},
                                         files={'image': ('sample.png', image_bytes(), 'image/png')})
            assert response.status_code == 200
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert events[-1]['type'] == 'error'
            assert events[-1]['error_code'] == 'INVALID_TOOL_REQUEST'
            assert not any(event['type'] in {'delta', 'done'} for event in events)
            assert 'malformed reasoning' not in response.text
        execute.assert_not_awaited()

    run_offline(run())


def test_vision_check_is_authenticated_and_selects_only_configured_vision(tmp_path, monkeypatch):
    from backend.health import routes

    probe = AsyncMock(return_value=True)
    monkeypatch.setattr(routes, 'probe_inference', probe)
    app = image_chat_app(tmp_path, checked=False)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            path = '/api/health/capabilities/vision-check'
            assert (await client.post(path, json={'model': 'ollama/other-vision'})).status_code == 401
            client.headers['Authorization'] = 'Bearer a'
            inventory = (await client.get('/api/health/capabilities')).json()
            assert inventory['vision_models'] == ['ollama/vision:latest', 'ollama/other-vision:latest']
            assert inventory['capabilities']['multimodal']['state'] == 'unknown'
            for model in ('ollama/text', 'ollama/unconfigured'):
                denied = await client.post(path, json={'model': model})
                assert denied.status_code == 409
                assert denied.json()['selected_vision_model']['reason'] == 'unsupported_vision_model'
            probe.assert_not_awaited()
            report = (await client.post(path, json={'model': 'ollama/other-vision'})).json()
            probe.assert_awaited_once_with('http://127.0.0.1:11434/api/tags', 'other-vision:latest', vision=True)
            assert report['selected_vision_model']['state'] == 'available'
            assert report['selected_chat_model']['state'] == 'unknown'
            assert routes.model_readiness(app.state, 'ollama/vision', 'vision')['state'] == 'unknown'

    run_offline(run())


@pytest.mark.parametrize('fault, code', [
    ('missing', 'VISION_NOT_READY'), ('expired', 'VISION_NOT_READY'), ('other-model', 'VISION_NOT_READY'),
    ('wrong-type', 'VISION_NOT_READY'), ('digest-changed', 'VISION_NOT_READY'),
    ('digest-missing', 'VISION_NOT_READY'), ('endpoint', 'VISION_NOT_READY'), ('text-expired', 'MODEL_NOT_READY'),
])
def test_image_request_rejects_unusable_readiness_before_provider_dispatch(tmp_path, fault, code):
    from dataclasses import replace
    from backend.health import routes

    app = image_chat_app(tmp_path)
    state = app.state
    state.engine._router.get_provider_for_model = MagicMock(side_effect=AssertionError('Provider must not load'))
    form_model = {'other-model': 'ollama/other-vision', 'wrong-type': 'ollama/text'}.get(fault, 'ollama/vision')
    identity, observed, success, digest = state.vision_observation
    if fault == 'missing':
        state.vision_observation = None
    elif fault == 'expired':
        state.vision_observation = (identity, observed - routes.INFERENCE_TTL, success, digest)
    elif fault in ('digest-changed', 'digest-missing'):
        target, at, inventory = state.model_observation
        state.model_observation = (target, at, replace(
            inventory, digests=(('vision:latest', 'c' * 64),) if fault == 'digest-changed' else ()))
    elif fault == 'endpoint':
        state.settings.ollama_base_url = 'http://127.0.0.1:11435'
    elif fault == 'text-expired':
        state.inference_observation = None

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            response = await client.post('/api/chat/multimodal', **image_form(vision_model=form_model))
            assert response.status_code == 503 and response.headers['X-Error-Code'] == code
            history = await client.get('/api/chat/sessions/session-a')
            assert history.json()['messages'] == []
        state.engine._router.get_provider_for_model.assert_not_called()

    run_offline(run())


@pytest.mark.parametrize('kind, status', [
    ('unsupported', 415), ('wrong-extension', 422), ('empty', 422),
    ('corrupt', 422), ('mismatched-mime', 415), ('oversized-file', 413), ('oversized-body', 413),
    ('duplicate', 422),
])
def test_image_ingress_rejects_invalid_upload_before_provider_dispatch(tmp_path, monkeypatch, kind, status):
    app = image_chat_app(tmp_path)
    app.state.engine._router.get_provider_for_model = MagicMock(side_effect=AssertionError('Provider must not load'))
    png = image_bytes()
    uploads = {
        'unsupported': ('sample.gif', png, 'image/gif'),
        'wrong-extension': ('sample.jpg', png, 'image/jpeg'),
        'empty': ('sample.png', b'', 'image/png'),
        'corrupt': ('sample.png', png[:-20], 'image/png'),
        'mismatched-mime': ('sample.png', png, 'image/jpeg'),
    }
    upload = uploads.get(kind, ('sample.png', png, 'image/png'))
    if kind == 'oversized-file':
        # Exercise the file-level boundary without spooling to disk in the timer-only loop.
        monkeypatch.setattr('backend.multimodal.image_processor.DEFAULT_MAX_SIZE_BYTES', 64)
        upload = ('sample.png', b'x' * 65, 'image/png')
    files = [('image', upload)] * (2 if kind == 'duplicate' else 1)
    headers = {'Authorization': 'Bearer a'}
    if kind == 'oversized-body':
        headers['Content-Length'] = str(11 * 1024 * 1024 + 1)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers=headers) as client:
            response = await client.post('/api/chat/multimodal', data=image_form()['data'], files=files)
            assert response.status_code == status
        app.state.engine._router.get_provider_for_model.assert_not_called()

    run_offline(run())


def test_image_request_rechecks_reasoning_readiness_after_vision(tmp_path):
    class Provider:
        provider_name = 'synthetic'

        async def chat(self, request):
            app.state.inference_observation = None
            return ChatResponse('Blue', 'vision', 'synthetic')

        async def chat_stream(self, request):
            raise AssertionError('Expired reasoning model must not receive image context')
            yield  # asynchronous generator protocol

    app = image_chat_app(tmp_path, Provider())

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            response = await client.post('/api/chat/multimodal', **image_form())
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert events[-1]['error_code'] == 'MODEL_NOT_READY'
            assert not any(event['type'] in {'done', 'delta'} for event in events)

    run_offline(run())


def test_synthetic_pdf_summary_uses_extracted_passage_and_citation():
    import fitz
    from backend.rag.ingest import TextChunker
    from backend.rag.grounding import answer_with_evidence
    pdf=fitz.open();page=pdf.new_page();page.insert_text((72,72),'Synthetic pump flow is 20 units.');data=pdf.tobytes();pdf.close()
    doc=DocumentParser.parse('pump.pdf',data);chunk=TextChunker().chunk(doc)[0]
    rows=[{'document_id':doc.document_id,'filename':doc.filename,'chunk_id':chunk.chunk_id,'chunk_index':chunk.chunk_index,'text':chunk.text,'distance':0.1,'source_revision':1,'index_version':'test','embedding_model':'fake'}]
    async def fake_model(question,context):
        assert 'Synthetic pump flow is 20 units' in context
        return 'The stated flow is 20 units '+context.split(']')[0]+']'
    result=run_offline(answer_with_evidence('Summarize',rows,allowed=lambda d,r:d==doc.document_id,index_version='test',embedding_model='fake',top_k=1,generate=fake_model,verify_evidence=lambda item: True))
    assert result.citations[0][1].filename=='pump.pdf' and result.dependencies==((doc.document_id,1),)


def test_multimodal_lifecycle_keeps_each_image_and_its_context_request_scoped(tmp_path):
    from backend.models.base import ChatChunk

    vision_requests = []
    text_requests = []

    class Provider:
        provider_name = 'synthetic'

        async def chat(self, request):
            vision_requests.append(request)
            return ChatResponse('A colored square.', 'vision', 'synthetic')

        async def chat_stream(self, request):
            text_requests.append(request)
            yield ChatChunk('Synthetic answer.', True)

    app = image_chat_app(tmp_path, Provider())
    first = 'Describe this image with the first-question marker.'
    second = 'Describe this image with the second-question marker.'
    path = '/api/chat/sessions/session-a'

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer a'}) as client:
            response = await client.post('/api/chat/multimodal', **image_form(message=first))
            assert '"type":"done"' in response.text
            for prompt in ('What was in the earlier image?', 'What color was it?'):
                blocked = await client.post('/api/chat', json={'session_id': 'session-a', 'message': prompt})
                events = [json.loads(line[6:]) for line in blocked.text.splitlines() if line.startswith('data: ')]
                assert [(event['type'], event['error_code']) for event in events] == [('error', 'IMAGE_REATTACH_REQUIRED')]
                assert 'Reattach the image' in events[0]['content']
            blocked = await client.post('/api/chat', json={
                'session_id': 'session-a', 'message': 'Read the previous screenshot.', 'stream': False,
            })
            assert blocked.status_code == 409
            blocked = await client.post('/api/chat/multimodal', **image_form(message='Compare with the earlier image.'))
            assert blocked.status_code == 409 and blocked.headers['X-Error-Code'] == 'IMAGE_REATTACH_REQUIRED'
            assert len(vision_requests) == len(text_requests) == 1

            second_bytes = image_bytes(size=(12, 12))
            response = await client.post('/api/chat/multimodal', data=image_form(message=second)['data'],
                                         files={'image': ('second.png', second_bytes, 'image/png')})
            assert '"type":"done"' in response.text
            assert len(vision_requests) == len(text_requests) == 2
            assert vision_requests[1].messages[-1].images == [base64.b64encode(second_bytes).decode('ascii')]
            assert first not in [message.content for message in text_requests[1].messages]
            assert text_requests[1].messages[-1].content == second

            for session_id in ('session-a', 's'):
                response = await client.post('/api/chat', json={
                    'session_id': session_id, 'message': 'Explain pumps.', 'tools_enabled': False,
                })
                assert '"type":"done"' in response.text
                request = text_requests[-1]
                assert all(not message.images for message in request.messages)
                assert all(message.content not in {first, second, 'Synthetic answer.'} for message in request.messages)
            assert len(vision_requests) == 2

            history = (await client.get(path)).json()['messages']
            assert [message['attachments'][0]['filename'] for message in history if 'attachments' in message] == [
                'sample.png', 'second.png',
            ]
            assert all(message['withheld'] for message in history[:4] if message['role'] == 'assistant')
            other = {'Authorization': 'Bearer b'}
            assert (await client.get(path, headers=other)).status_code == 404
            assert (await client.delete(path, headers=other)).status_code == 404
            assert (await client.delete(path)).status_code == 200
            assert (await client.get(path)).status_code == 404

    run_offline(run())


def test_request_scoped_model_history_excludes_legacy_image_bytes_in_both_reasoning_paths(tmp_path, monkeypatch):
    from backend.models.base import Message
    from test_a21_conversation_offline import setup_engine

    engine, _ = setup_engine(tmp_path)
    current = Message('user', 'Current prompt', id='current', extra_fields={'attachments_v1': [{}]})
    history = [Message('user', 'Legacy image prompt', images=['legacy-bytes']),
               Message('user', 'Prior image prompt', extra_fields={'attachments_v1': [{}]}, id='old'),
               Message('user', 'Ordinary text'), current]
    monkeypatch.setattr(engine._memory, 'get_history', lambda _: history)
    for context in (
        engine._build_messages('s', 'Current prompt', [], current_image_turn='current'),
        engine._build_task_reasoning_messages('s', 'Ordinary text', [], []),
    ):
        assert all(not message.images for message in context)
        assert not {'Legacy image prompt', 'Prior image prompt'}.intersection(message.content for message in context)
    assert engine._build_messages('s', 'Current prompt', [], current_image_turn='current')[-1] is current
    assert history[0].images == ['legacy-bytes']
