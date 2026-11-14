"""Offline artifact checks; production publication remains blocked."""
import io
from unittest.mock import MagicMock
import pytest
from backend.auth.request_context import effective_owner
from backend.tools.registry import ToolDefinition, ToolRegistry
from backend.tools.docx_create import DocxCreateInput
from backend.tools.xlsx_report import XlsxReportInput
from backend.tools.file_write import FileWriteInput
from backend.tools.verified_report import _verify, generate_verified_report
from test_a21_conversation_offline import immediate
from test_a71_artifacts_offline import report


@pytest.mark.parametrize('name,schema,args', [
    ('docx_create', DocxCreateInput, {'filename':'x.docx','title':'Report','content':'Synthetic'}),
    ('xlsx_report', XlsxReportInput, {'filename':'x.xlsx','title':'Report','headers':['A'], 'rows':[['x']]}),
    ('file_write', FileWriteInput, {'filename':'x.txt','content':'Synthetic report'}),
])
def test_registry_never_dispatches_unverified_report_or_consumes_approval(name, schema, args):
    calls=[]
    async def generator(_):
        calls.append(True)
        return {'filename':'missing', 'sha256_hash':'0'*64}
    registry=ToolRegistry(); store=MagicMock(); store.get_artifact.return_value=None
    registry.set_task_store(store)
    approval=MagicMock(); approval.verify_approval_for_execution.return_value=True
    registry.set_approval_manager(approval)
    registry.register(ToolDefinition(name=name,description='synthetic',input_schema=schema,
                                    execute_fn=generator,read_only=False,requires_approval=True))
    token=effective_owner.set('alice')
    try:
        result=immediate(registry.execute(name,args,user_id='alice',user_role='admin',
                    task_id='task',step_id='step',approval_id='approval'))
        assert not result.success and 'unavailable' in result.error
        assert calls == []
        approval.consume_approval_for_execution.assert_not_called()
        store.save_artifact.assert_not_called()
    finally: effective_owner.reset(token)


def test_pdf_readback_requires_body_not_just_title_and_sources():
    import fitz
    document=fitz.open()
    try:
        page=document.new_page()
        for i,line in enumerate(['Synthetic report','Findings','s1: source.txt']):
            page.insert_text((45,50+i*14),line,fontsize=9)
        data=document.tobytes()
    finally: document.close()
    with pytest.raises(ValueError,match='PDF expected content missing'):
        _verify(report('pdf'),data,{'s1':'source.txt'})


def test_xlsx_readback_rejects_formula_title(tmp_path):
    from openpyxl import load_workbook
    value=report('xlsx').model_copy(update={'title':'=1+1'})
    result=immediate(generate_verified_report(value,{'s1':'source.txt'},staging_parent=tmp_path))
    book=load_workbook(io.BytesIO(result.content))
    try:
        book.active['A1']='=1+1'
        output=io.BytesIO(); book.save(output)
    finally: book.close()
    with pytest.raises(ValueError,match='workbook title|formula'):
        _verify(value,output.getvalue(),{'s1':'source.txt'})
