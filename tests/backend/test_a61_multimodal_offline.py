"""Offline multimodal foundations; no services or network."""
import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
    import httpx
    from backend.models.base import ChatChunk
    from test_a21_conversation_offline import setup_engine, make_app
    engine,_=setup_engine(tmp_path); engine._max_tool_iterations=2
    calls=[]
    class FakeProvider:
        provider_name='synthetic'
        async def chat(self,request):
            calls.append(request)
            return ChatResponse('A blue object is visible.','vision','synthetic')
        async def chat_stream(self,request):
            calls.append(request)
            yield ChatChunk('The image appears blue.',True)
    provider=FakeProvider()
    engine._router.resolve_vision_model=lambda:(provider,'vision')
    engine._router.resolve_chat_model=lambda:(provider,'text')
    engine._router.get_provider_for_model=lambda _: (provider,'text')
    app=make_app(engine);app.state.upload_dir=tmp_path
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            kwargs=dict(data={'session_id':'session-a','message':'Describe image','stream':'true','tools_enabled':'false'},files={'image':('sample.png',image_bytes(),'image/png')})
            denied=await client.post('/api/chat/multimodal',headers={'Authorization':'Bearer b'},**kwargs)
            assert denied.status_code==404 and not calls
            response=await client.post('/api/chat/multimodal',headers={'Authorization':'Bearer a'},**kwargs)
            assert response.status_code==200 and '"type":"done"' in response.text and 'appears blue' in response.text
            assert calls[0].messages[-1].images and len(calls[0].messages[-1].images)==1
            assert not list((tmp_path/'images').glob('*'))
    run_offline(run())

def test_real_image_route_provider_failure_is_error_not_completion(tmp_path):
    import httpx
    from test_a21_conversation_offline import setup_engine, make_app
    engine,_=setup_engine(tmp_path);engine._max_tool_iterations=2
    class FailingProvider:
        async def chat(self,request): raise RuntimeError('synthetic failure')
    engine._router.resolve_vision_model=lambda:(FailingProvider(),'vision')
    app=make_app(engine);app.state.upload_dir=tmp_path
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/api/chat/multimodal',headers={'Authorization':'Bearer a'},data={'session_id':'session-a','message':'Describe','tools_enabled':'false'},files={'image':('x.png',image_bytes(),'image/png')})
            assert '"type":"error"' in response.text and '"type":"done"' not in response.text
            assert 'synthetic failure' not in response.text
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
