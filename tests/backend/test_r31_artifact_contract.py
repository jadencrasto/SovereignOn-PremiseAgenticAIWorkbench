"""Offline artifact-verification and inventory regressions; no production artifact writes."""
import hashlib
import io
import re
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from openpyxl import load_workbook
from backend.api.tools import list_tools
from backend.tools.registry import ToolDefinition, ToolRegistry
from backend.tools.docx_create import DocxCreateInput
from backend.tools.xlsx_report import XlsxReportInput
from backend.tools.file_write import FileWriteInput
from backend.tools.artifact_verifier import ArtifactVerifierInput
from backend.tools.verified_report import _verify, generate_verified_report
from test_a21_conversation_offline import immediate
from test_a71_artifacts_offline import report


def add_hidden_formula(data):
    book = load_workbook(io.BytesIO(data))
    try:
        book.active['Z100'] = '=1+1'
        output = io.BytesIO(); book.save(output)
    finally: book.close()
    # A lying declared dimension must not hide the actual sheet cell.
    source = zipfile.ZipFile(io.BytesIO(output.getvalue()))
    rewritten = io.BytesIO()
    with source, zipfile.ZipFile(rewritten, 'w') as dest:
        for entry in source.infolist():
            payload = source.read(entry)
            if entry.filename == 'xl/worksheets/sheet1.xml':
                payload = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1:D6"', payload)
            dest.writestr(entry, payload)
    return rewritten.getvalue()


def test_unexpected_formula_rejected_even_with_forged_dimensions(tmp_path):
    value = report('xlsx')
    verified = immediate(generate_verified_report(value, {'s1':'source.txt'}, staging_parent=tmp_path))
    malicious = add_hidden_formula(verified.content)
    with pytest.raises(ValueError, match='formula|cell'):
        _verify(value, malicious, {'s1':'source.txt'})


def test_generator_digest_cannot_make_extra_formula_verified(tmp_path, monkeypatch):
    from backend.tools import verified_report as module
    value = report('xlsx')
    content = immediate(generate_verified_report(value, {'s1':'source.txt'}, staging_parent=tmp_path)).content
    malicious = add_hidden_formula(content)
    def factory(stage):
        async def execute(args):
            artifact_id = 'a' * 32; filename = artifact_id + '.xlsx'
            (stage/filename).write_bytes(malicious)
            return {'artifact_id':artifact_id, 'filename':filename, 'sha256_hash':hashlib.sha256(malicious).hexdigest()}
        return execute
    monkeypatch.setattr(module, 'create_xlsx_report', factory)
    with pytest.raises(ValueError, match='formula|cell'):
        immediate(generate_verified_report(value, {'s1':'source.txt'}, staging_parent=tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('fmt', ['docx','xlsx','pdf'])
def test_corrupt_or_wrong_format_cannot_verify(tmp_path, fmt):
    value = report(fmt)
    with pytest.raises(Exception): _verify(value, b'not a document', {'s1':'source.txt'})
    other = 'xlsx' if fmt != 'xlsx' else 'docx'
    bytes_wrong = immediate(generate_verified_report(report(other), {'s1':'source.txt'}, staging_parent=tmp_path)).content
    with pytest.raises(Exception): _verify(value, bytes_wrong, {'s1':'source.txt'})


def test_tools_api_distinguishes_configured_from_blocked():
    async def handler(args): return {}
    registry = ToolRegistry()
    for name, schema in [('docx_create',DocxCreateInput), ('xlsx_report',XlsxReportInput),
                         ('file_write',FileWriteInput), ('artifact_verifier',ArtifactVerifierInput)]:
        registry.register(ToolDefinition(name=name, description='synthetic', input_schema=schema, execute_fn=handler))
    response = immediate(list_tools(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(tool_registry=registry))), MagicMock()))
    assert {item.name for item in response.tools} == {'docx_create','xlsx_report','file_write','artifact_verifier'}
    assert all(item.enabled and item.availability == 'blocked' and item.availability_reason for item in response.tools)


def test_decoy_worksheet_relationship_is_rejected(tmp_path):
    value = report('xlsx')
    content = immediate(generate_verified_report(value, {'s1':'source.txt'}, staging_parent=tmp_path)).content
    archive = zipfile.ZipFile(io.BytesIO(content)); output = io.BytesIO()
    with archive, zipfile.ZipFile(output, 'w') as result:
        for entry in archive.infolist():
            payload = archive.read(entry)
            if entry.filename == 'xl/_rels/workbook.xml.rels':
                payload = payload.replace(b'/xl/worksheets/sheet1.xml', b'/xl/elsewhere/custom.xml')
            result.writestr(entry, payload)
        result.writestr('xl/elsewhere/custom.xml', '<worksheet/>')
    with pytest.raises(ValueError, match='relationship'):
        _verify(value, output.getvalue(), {'s1':'source.txt'})


def test_isolated_report_cannot_be_constructed_as_published():
    from backend.tools.verified_report import VerifiedReport
    with pytest.raises(TypeError):
        VerifiedReport('a'*32, 'pdf', 'b'*64, b'x', (), state='published')
