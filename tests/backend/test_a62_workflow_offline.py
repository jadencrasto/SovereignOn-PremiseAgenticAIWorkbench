"""Offline composition tests; callbacks are synthetic, not production authorization."""
import asyncio
from unittest.mock import MagicMock
import pytest
from backend.multimodal.workflow import process_inputs, MAX_TOTAL_BYTES
from backend.multimodal.image_processor import ImageProcessor
from backend.models.base import ChatResponse
from test_a22_async_offline import run_offline
from test_a61_multimodal_offline import image_bytes, workbook_bytes


def make_docx(text):
    from docx import Document
    import io
    d=Document();d.add_paragraph(text);out=io.BytesIO();d.save(out);return out.getvalue()


def run(files, tmp_path, **overrides):
    options=dict(owner_id='alice',conversation_id='conv1',allowed=lambda o,c:(o,c)==('alice','conv1'),image_processor=ImageProcessor(tmp_path))
    options.update(overrides)
    return run_offline(process_inputs('Compare the inputs',files,**options))


def test_two_documents_keep_source_id_and_content(tmp_path):
    result=run([('a.docx',make_docx('Flow is 10')),('b.docx',make_docx('Flow is 20'))],tmp_path)
    assert result.status=='completed' and len(set(result.dependencies))==2
    assert result.inputs[0].value['text']=='Flow is 10' and result.inputs[1].value['text']=='Flow is 20'
    assert [x.record.filename for x in result.inputs]==['a.docx','b.docx']
    assert all((x.record.owner_id,x.record.conversation_id)==('alice','conv1') for x in result.inputs)


def test_document_spreadsheet_calculation_and_formula(tmp_path):
    result=run([('report.txt',b'Measured flow 15.'),('data.xlsx',workbook_bytes())],tmp_path)
    assert result.status=='completed' and len(result.dependencies)==2
    summary=result.inputs[1].value['sheets'][0]['summary']
    assert summary=={'count':2,'missing':1,'nonnumeric':1,'mean':15,'max':20}
    assert result.inputs[0].value['text']=='Measured flow 15.'


def test_vision_uses_router_and_records_interpretation(tmp_path):
    class Vision:
        async def chat(self, request):
            assert request.messages[-1].images and request.messages[-1].content=='Compare the inputs'
            return ChatResponse('A blue object appears visible.','vision','fake')
    router=MagicMock();router.resolve_vision_model.return_value=(Vision(),'vision')
    result=run([('x.png',image_bytes())],tmp_path,router=router)
    assert result.status=='completed' and result.inputs[0].value['classification']=='model_generated'
    assert result.dependencies==(result.inputs[0].record.input_id,)
    router.resolve_vision_model.assert_called_once()


def test_unsupported_input_failure_withholds_successful_partial_value(tmp_path):
    result=run([('report.txt',b'valid'),('unsupported.bin',b'data')],tmp_path)
    assert result.status=='failed' and result.inputs[1].record.status=='failed'
    assert result.inputs[1].record.error and all(x.value is None for x in result.inputs)
    assert result.dependencies==()


def test_owner_revocation_and_untrusted_input_shape(tmp_path):
    with pytest.raises(PermissionError): run([('x.txt',b'hello')],tmp_path,owner_id='mallory')
    count=[0]
    def allowed(o,c): count[0]+=1; return count[0]<3
    with pytest.raises(PermissionError): run([('x.txt',b'hello')],tmp_path,allowed=allowed)
    with pytest.raises(ValueError): run([('x.txt',b'hello','forged-id')],tmp_path)


def test_aggregate_limit_and_empty_input(tmp_path):
    with pytest.raises(ValueError): run([('x.txt',b'a'*(MAX_TOTAL_BYTES+1))],tmp_path)
    with pytest.raises(ValueError): run([],tmp_path)
    result=run([('empty.txt',b'')],tmp_path)
    assert result.status=='failed' and result.inputs[0].record.status=='failed'


def test_provider_failure_and_cancellation(tmp_path):
    class Failed:
        async def chat(self, request): raise RuntimeError('private provider error')
    router=MagicMock();router.resolve_vision_model.return_value=(Failed(),'vision')
    result=run([('x.png',image_bytes())],tmp_path,router=router)
    assert result.status=='failed' and result.inputs[0].record.error=='processing_unavailable'
    closed=[]
    class Slow:
        async def chat(self, request):
            try: await asyncio.sleep(1)
            finally: closed.append(True)
    router.resolve_vision_model.return_value=(Slow(),'vision')
    async def exercise():
        task=asyncio.create_task(process_inputs('Describe',[('x.png',image_bytes())],owner_id='a',conversation_id='c',allowed=lambda o,c:True,image_processor=ImageProcessor(tmp_path),router=router))
        await asyncio.sleep(.01);task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    run_offline(exercise()); assert closed==[True]


def test_text_only_never_calls_vision(tmp_path):
    router=MagicMock()
    result=run([('x.txt',b'synthetic text')],tmp_path,router=router)
    assert result.status=='completed';router.resolve_vision_model.assert_not_called()
