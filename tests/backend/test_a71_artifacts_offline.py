"""Synthetic artifact tests. Direct tool calls do not authorize production use."""
import hashlib
from unittest.mock import MagicMock
import pytest
from pydantic import ValidationError
from docx import Document
from openpyxl import load_workbook
from backend.tools.docx_create import create_docx_create
from backend.tools.xlsx_report import create_xlsx_report
from backend.tools.report_contract import ReportInput, ReportSection, ReportTable, tool_arguments
from backend.tools.artifact_verifier import create_artifact_verifier, ArtifactVerifierInput
from backend.api.artifacts import _is_artifact_accessible
from test_a21_conversation_offline import immediate


def report(fmt):
    return ReportInput(title='Synthetic report',output_format=fmt,sections=[ReportSection(heading='Findings',text='Observed flow 20',kind='source_supported',citations=['s1'])],tables=[ReportTable(headers=['Name','Value'],rows=[['Pump',20],['Formula','=1+1']])],dependencies=['s1'])


def test_actual_docx_generation_and_structural_validation(tmp_path):
    args=tool_arguments(report('docx'),{'s1':'source.txt'})
    result=immediate(create_docx_create(tmp_path)(args))
    target=tmp_path/result['filename']; raw=target.read_bytes()
    assert result['artifact_id']+'.docx'==result['filename']
    assert result['sha256_hash']==hashlib.sha256(raw).hexdigest()
    doc=Document(target); text='\n'.join(p.text for p in doc.paragraphs)
    assert 'Synthetic report' in text and 'Observed flow 20' in text and 's1: source.txt' in text
    assert len(doc.tables)==1 and len(doc.tables[0].rows)==3 and len(doc.tables[0].columns)==2
    assert doc.tables[0].cell(1,1).text=='20'
    verify=immediate(create_artifact_verifier(tmp_path)(ArtifactVerifierInput(relative_path=result['filename'],expected_content=['Observed flow 20'])))
    assert verify['verified'] and verify['sha256_hash']==result['sha256_hash']
    target.write_bytes(b'tampered')
    assert hashlib.sha256(target.read_bytes()).hexdigest()!=result['sha256_hash']
    with pytest.raises(Exception): immediate(create_artifact_verifier(tmp_path)(ArtifactVerifierInput(relative_path=result['filename'])))


def test_actual_xlsx_values_and_formula_injection(tmp_path):
    args=tool_arguments(report('xlsx'),{'s1':'source.txt'})
    args.headers=['=1+2','Value']; args.title='=HYPERLINK("x")'
    result=immediate(create_xlsx_report(tmp_path)(args))
    target=tmp_path/result['filename']; assert result['sha256_hash']==hashlib.sha256(target.read_bytes()).hexdigest()
    book=load_workbook(target,data_only=False)
    try:
        sheet=book.active
        assert sheet['A1'].value=='=HYPERLINK("x")' and sheet['A1'].data_type=='s'
        assert sheet['A4'].value=='=1+2' and sheet['A4'].data_type=='s'
        assert sheet['B5'].value==20 and sheet['B5'].data_type=='n'
        assert sheet['B6'].value=='=1+1' and sheet['B6'].data_type=='s'
        assert 'source.txt' in sheet['A2'].value
    finally: book.close()


def test_contract_invalid_citations_formats_and_structure():
    with pytest.raises(ValueError): tool_arguments(report('docx'),{'other':'source.txt'})
    with pytest.raises(ValueError): tool_arguments(report('docx'),{'s1':'../source.txt'})
    with pytest.raises(ValidationError): report('html')
    with pytest.raises(ValidationError): ReportTable(headers=['A','B'],rows=[['one']])
    with pytest.raises(ValidationError): ReportSection(heading='x',text='y',kind='source_supported')
    with pytest.raises(ValidationError): ReportInput(title='x',output_format='xlsx',sections=[],tables=[])


def test_tool_boundary_uses_opaque_name_and_access_stays_blocked(tmp_path):
    args=tool_arguments(report('docx'),{'s1':'source.txt'})
    result=immediate(create_docx_create(tmp_path)(args))
    assert result['filename']!='report.docx' and (tmp_path/result['filename']).is_file()
    assert not _is_artifact_accessible(result['filename'],MagicMock(),MagicMock())


def test_isolated_pdf_readback_and_content(tmp_path):
    from backend.tools.pdf_report import create_pdf_report
    from pypdf import PdfReader
    result=create_pdf_report(tmp_path,report('pdf'),{'s1':'source.txt'})
    path=tmp_path/result['filename']
    assert result['sha256_hash']==hashlib.sha256(path.read_bytes()).hexdigest()
    reader=PdfReader(path);text=' '.join(page.extract_text() for page in reader.pages)
    assert all(x in text for x in ('Synthetic report','Observed flow 20','source.txt','Pump | 20','Formula | =1+1'))
    with pytest.raises(ValueError):
        create_pdf_report(tmp_path,report('pdf').model_copy(update={'title':'non-ascii é'}),{'s1':'source.txt'})

def test_registry_still_requires_owner_and_approval(tmp_path):
    from backend.tools.registry import ToolRegistry, ToolDefinition
    from backend.tools.docx_create import DocxCreateInput
    from backend.auth.request_context import effective_owner
    registry=ToolRegistry();registry.set_task_store(MagicMock())
    registry.register(ToolDefinition(name='docx_create',description='synthetic',input_schema=DocxCreateInput,execute_fn=create_docx_create(tmp_path),read_only=False,requires_approval=True))
    token=effective_owner.set('alice')
    try:
        result=immediate(registry.execute('docx_create',{'filename':'x.docx','title':'x'},user_id='alice',user_role='admin'))
        assert not result.success and 'approval' in result.error.lower()
        result=immediate(registry.execute('docx_create',{'filename':'x.docx','title':'x'},user_id='bob',user_role='admin'))
        assert not result.success
        assert list(tmp_path.iterdir())==[]
    finally: effective_owner.reset(token)


def test_pdf_interrupted_write_never_reports_success(tmp_path,monkeypatch):
    from backend.tools import pdf_report
    class FailedStream:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def write(self,_): raise OSError('simulated interruption')
    monkeypatch.setattr(pdf_report,'create_artifact_file',lambda *args: ('a'*32,'a'*32+'.pdf',FailedStream()))
    with pytest.raises(OSError): pdf_report.create_pdf_report(tmp_path,report('pdf'),{'s1':'source.txt'})
