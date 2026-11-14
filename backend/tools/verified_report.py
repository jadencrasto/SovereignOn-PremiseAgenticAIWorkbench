"""Isolated report verification. Not an authorization or publication mechanism.

Requires a caller-controlled private staging parent. Uncertain leftovers are
never recursively removed or treated as verified. No production route uses this.
"""
from __future__ import annotations
import hashlib
import io
import os
import re
import stat
import tempfile
import textwrap
import zipfile
from xml.etree import ElementTree
from dataclasses import dataclass, field
from pathlib import Path

from backend.tools.report_contract import ReportInput, tool_arguments
from backend.tools.docx_create import create_docx_create
from backend.tools.xlsx_report import create_xlsx_report
from backend.tools.pdf_report import create_pdf_report

MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_XLSX_XML_BYTES = 8 * 1024 * 1024


def _check_xlsx_worksheet_xml(data: bytes, report: ReportInput) -> None:
    """Bound and inspect actual sheet cells, independent of declared dimensions."""
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            sheets = [entry for entry in entries if re.fullmatch(r'xl/worksheets/[^/]+\.xml', entry.filename)]
            if (len(entries) > 512 or len(names) != len(set(names)) or len(sheets) != 1
                    or sheets[0].filename != 'xl/worksheets/sheet1.xml'
                    or sheets[0].file_size > MAX_XLSX_XML_BYTES
                    or sum(entry.file_size for entry in entries) > 20 * 1024 * 1024):
                raise ValueError('Invalid workbook structure')
            xml = archive.read(sheets[0])
            relations = archive.read('xl/_rels/workbook.xml.rels')
        if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
            raise ValueError('Invalid workbook XML')
        if b'<!DOCTYPE' in relations.upper() or b'<!ENTITY' in relations.upper():
            raise ValueError('Invalid workbook XML')
        rel_root = ElementTree.fromstring(relations)
        rel_ids = [item.get('Id') for item in rel_root]
        links = [item for item in rel_root if item.get('Type', '').endswith('/worksheet')]
        if (not all(isinstance(value, str) and value for value in rel_ids) or len(set(rel_ids)) != len(rel_ids)
                or len(links) != 1 or links[0].get('TargetMode') is not None
                or links[0].get('Target') not in ('/xl/worksheets/sheet1.xml', 'worksheets/sheet1.xml')):
            raise ValueError('Invalid workbook relationship')
        root = ElementTree.fromstring(xml)
        max_row = 4 + len(report.tables[0].rows)
        max_col = max(4, len(report.tables[0].headers))
        for element in root.iter():
            tag = element.tag.rsplit('}', 1)[-1] if isinstance(element.tag, str) else ''
            if tag == 'f':
                raise ValueError('Workbook formulas are not permitted')
            if tag == 'c':
                coordinate = element.get('r')
                if not coordinate or not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}', coordinate):
                    raise ValueError('Invalid workbook cell')
                column, row = coordinate_from_string(coordinate)
                if row > max_row or column_index_from_string(column) > max_col:
                    raise ValueError('Unexpected workbook cell')
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, OSError) as exc:
        raise ValueError('Invalid workbook structure') from exc


@dataclass(frozen=True)
class VerifiedReport:
    artifact_id: str
    output_format: str
    sha256: str
    content: bytes
    dependencies: tuple[str, ...]
    state: str = field(default='verified_unpublished', init=False)


def _verify(report: ReportInput, data: bytes, sources: dict[str, str]) -> None:
    if not data or len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError('Artifact size is invalid')
    refs = {r for section in report.sections for r in section.citations}
    required_sources = [f'{r}: {sources[r]}' for r in refs]
    if report.output_format == 'docx':
        from docx import Document
        doc = Document(io.BytesIO(data))
        paragraphs = '\n'.join(p.text for p in doc.paragraphs)
        required = [report.title] + [item for section in report.sections for item in (section.heading, section.text)] + required_sources
        if any(item not in paragraphs for item in required) or len(doc.tables) != len(report.tables):
            raise ValueError('DOCX expected content missing')
        for actual, expected in zip(doc.tables, report.tables):
            expected_rows = [expected.headers] + expected.rows
            if len(actual.rows) != len(expected_rows): raise ValueError('DOCX table dimensions mismatch')
            for row, expected_row in zip(actual.rows, expected_rows):
                if len(row.cells) != len(expected_row) or [cell.text for cell in row.cells] != [str(v) for v in expected_row]:
                    raise ValueError('DOCX table content mismatch')
    elif report.output_format == 'xlsx':
        from openpyxl import load_workbook
        _check_xlsx_worksheet_xml(data, report)
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
        try:
            if len(wb.worksheets) != 1: raise ValueError('Unexpected worksheets')
            ws = wb.worksheets[0]
            if ws.title != 'Compliance Report' or ws['A1'].value != report.title or ws['A1'].data_type == 'f': raise ValueError('Invalid workbook title')
            if ws['A2'].data_type == 'f': raise ValueError('Invalid workbook notes')
            notes = str(ws['A2'].value or '')
            required = [item for section in report.sections for item in (section.heading, section.text)] + required_sources
            if any(item not in notes for item in required): raise ValueError('Workbook expected content missing')
            table = report.tables[0]
            for offset, row in enumerate([table.headers] + table.rows, start=4):
                for col, value in enumerate(row, start=1):
                    cell=ws.cell(offset,col)
                    if cell.value != value or cell.data_type == 'f': raise ValueError('Workbook table mismatch')
        finally: wb.close()
    elif report.output_format == 'pdf':
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(data))
        if not 1 <= len(reader.pages) <= 20: raise ValueError('Invalid PDF page count')
        text='\n'.join(page.extract_text() or '' for page in reader.pages)
        # Verify the written bytes, including body and tables, independently
        # of the renderer's pre-write inspection.
        args = tool_arguments(report.model_copy(update={'output_format': 'docx'}), sources)
        blocks = [args.title]
        for section in args.sections or []:
            blocks.extend([section['heading'], section['body']])
        for table in args.tables or []:
            blocks.append(' | '.join(str(x) for x in table['headers']))
            blocks.extend(' | '.join('' if x is None else str(x) for x in row) for row in table['rows'])
        lines = [part for block in blocks for line in (block.splitlines() or [''])
                 for part in (textwrap.wrap(line, width=80, replace_whitespace=True, drop_whitespace=True) or [''])]
        if any(line and line not in text for line in lines): raise ValueError('PDF expected content missing')
    else:
        raise ValueError('Unsupported output format')


async def generate_verified_report(report: ReportInput, sources: dict[str, str], *, staging_parent: Path) -> VerifiedReport:
    """Return checked bytes only; the caller cannot infer authorization or publication."""
    if not isinstance(report, ReportInput): raise ValueError('Invalid report')
    report = ReportInput.model_validate(report.model_dump())
    # Validate source membership before creating any staging object.
    validation_report = report.model_copy(update={'output_format':'docx'}) if report.output_format == 'pdf' else report
    args = tool_arguments(validation_report, sources)
    parent=Path(staging_parent)
    if parent.is_symlink() or not parent.is_dir(): raise ValueError('Invalid staging parent')
    stage=Path(tempfile.mkdtemp(prefix='sovereign-report-', dir=parent))
    identity=None; target=None
    try:
        if report.output_format == 'docx': result=await create_docx_create(stage)(args)
        elif report.output_format == 'xlsx': result=await create_xlsx_report(stage)(args)
        else: result=create_pdf_report(stage, report, sources)
        filename=result.get('filename'); artifact_id=result.get('artifact_id')
        if not isinstance(artifact_id,str) or not re.fullmatch(r'[0-9a-f]{32}',artifact_id) or filename != artifact_id+'.'+report.output_format:
            raise ValueError('Invalid generated artifact identity')
        target=stage / filename
        metadata=target.lstat()
        if not stat.S_ISREG(metadata.st_mode) or target.is_symlink() or metadata.st_size > MAX_ARTIFACT_BYTES:
            raise ValueError('Invalid generated artifact')
        if not metadata.st_ino:
            raise ValueError("Artifact identity unavailable")
        identity=(metadata.st_dev,metadata.st_ino)
        data=target.read_bytes()
        if len(data) != metadata.st_size or hashlib.sha256(data).hexdigest()!=result.get('sha256_hash'):
            raise ValueError('Artifact digest mismatch')
        _verify(report,data,sources)
        after=target.lstat()
        if (after.st_dev,after.st_ino)!=(metadata.st_dev,metadata.st_ino) or target.read_bytes()!=data:
            raise ValueError('Artifact changed during verification')
        return VerifiedReport(artifact_id,report.output_format,hashlib.sha256(data).hexdigest(),data,tuple(report.dependencies))
    finally:
        # Only remove the exact regular file whose identity we observed. A
        # failed generator without a returned identity leaves an orphan for
        # manual inspection; it is never enumerated or recursively deleted.
        if target is not None and identity is not None:
            try:
                current=target.lstat()
                if stat.S_ISREG(current.st_mode) and not target.is_symlink() and (current.st_dev,current.st_ino)==identity:
                    target.unlink()
            except OSError:
                pass
        try: stage.rmdir()
        except OSError: pass
