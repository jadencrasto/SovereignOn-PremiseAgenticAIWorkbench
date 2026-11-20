"""Bounded local-development document projection. No routes or corpus writes."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
from importlib.metadata import version
from importlib.util import find_spec
from io import BytesIO
from threading import Event, Timer
from typing import Literal
from xml.parsers import expat
from zipfile import ZipFile

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.multimodal.document_evidence import ExtractionBudget, ExtractionLimit
from backend.multimodal.openxml_limits import validate_openxml
from backend.multimodal.pdf_limits import open_bounded_pdf
from backend.multimodal.text_file import TextFileError, text_filename
from backend.rag.local_relevance import MAX_TABLE_CELL, MAX_TABLE_COLUMNS, MAX_TABLE_LINE, MAX_TABLE_ROWS

MAX_DOCUMENT_BYTES = 512 * 1024
MAX_DOCUMENT_TEXT_BYTES = 6144
MAX_DOCUMENT_LINES = 220
MAX_PAGES = 12
MAX_PARAGRAPHS = 120
MAX_SHEETS = 8
MAX_SHEET_ROW = 64
MAX_OFFICE_ENTRIES = 256
MAX_OFFICE_EXPANDED = 4 * 1024 * 1024
PARSER_SECONDS = 8
_WORKER = Path(__file__).with_name('local_document_worker.py').resolve()
_FORMATS = {
    'pdf': ('pypdf', 'application/pdf'),
    'docx': ('docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
    'xlsx': ('openpyxl', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
}
_WORD = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_SHEET = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_WORD_FIELDS = {'instrText', 'delInstrText', 'fldChar', 'fldSimple', 'object', 'altChunk', 'control',
                'drawing', 'pict', 'subDoc', 'customXml', 'sdt', 'ins', 'del', 'moveFrom', 'moveTo'}
_PDF_FEATURES = {'/OpenAction', '/AA', '/Annots', '/AcroForm', '/XFA', '/JavaScript', '/JS', '/EF',
                 '/EmbeddedFiles', '/EmbeddedFile', '/Filespec', '/FileAttachment', '/RichMedia', '/Launch',
                 '/URI', '/URL', '/GoToR', '/GoToE', '/SubmitForm', '/ImportData', '/Rendition', '/Movie', '/Sound'}
WARNINGS = {
    'PDF_PAGE_OMITTED': 'One or more pages had no extractable text. OCR is unavailable.',
    'PDF_IMAGES_OMITTED': 'Images were excluded. OCR is unavailable.',
    'PDF_TABLES_UNVERIFIED': 'Some PDF tables did not have verified geometry.',
    'TABLE_OMITTED': 'Some tables have unsupported structure or exceed the local table limits.',
    'TEXT_OMITTED': 'Some paragraphs or cells could not be indexed without changing their structure.',
    'DOCX_NON_BODY_OMITTED': 'Headers, footers, comments or notes outside the document body were excluded.',
    'XLSX_FORMULAS_OMITTED': 'Formula cells and cached results were excluded.',
    'XLSX_ERRORS_OMITTED': 'Spreadsheet error cells were excluded.',
    'XLSX_SHEET_OMITTED': 'A worksheet did not have a supported rectangular table with unique headers.',
}


class DocumentLocator(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    line: int = Field(ge=1, le=MAX_DOCUMENT_LINES)
    page: int | None = Field(default=None, ge=1, le=MAX_PAGES)
    block: int | None = Field(default=None, ge=1, le=MAX_DOCUMENT_LINES)
    paragraph: int | None = Field(default=None, ge=1, le=MAX_PARAGRAPHS)
    table: int | None = Field(default=None, ge=1, le=64)
    row: int | None = Field(default=None, ge=1, le=MAX_SHEET_ROW)
    sheet: str | None = Field(default=None, min_length=1, max_length=31)
    width: int | None = Field(default=None, ge=2, le=MAX_TABLE_COLUMNS)

    def public(self, kind, column=None):
        result = self.model_dump(exclude={'line', 'width'}, exclude_none=True)
        result['format'] = kind
        if self.sheet:
            result['range'] = f'A{self.row}:{chr(64 + self.width)}{self.row}'
            if column is not None:
                result['cell'] = f'{chr(64 + column)}{self.row}'
        elif column is not None and self.table:
            result['column'] = column
        return result


class DocumentProjection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    format: Literal['pdf', 'docx', 'xlsx']
    content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    parser: str = Field(min_length=1, max_length=100)
    warnings: list[str] = Field(max_length=len(WARNINGS))
    text: str = Field(min_length=1, max_length=MAX_DOCUMENT_TEXT_BYTES)
    locators: list[DocumentLocator] = Field(min_length=1, max_length=MAX_DOCUMENT_LINES)

    @model_validator(mode='after')
    def verify_projection(self):
        lines = self.text.split('\n')
        if (len(self.text.encode('utf-8')) > MAX_DOCUMENT_TEXT_BYTES or len(lines) > MAX_DOCUMENT_LINES
                or any(code not in WARNINGS for code in self.warnings)
                or len(set(self.warnings)) != len(self.warnings)
                or len({item.line for item in self.locators}) != len(self.locators)):
            raise ValueError('Invalid local document projection.')
        for item in self.locators:
            if item.line > len(lines) or not lines[item.line - 1].strip():
                raise ValueError('Missing projected source line.')
            table = item.table is not None and item.row is not None and item.width is not None
            if self.format == 'pdf':
                valid = (item.page and not item.sheet and not item.paragraph
                         and (table and not item.block if item.table else item.block and not item.row and not item.width))
            elif self.format == 'docx':
                valid = (not item.page and not item.block and not item.sheet
                         and (table and not item.paragraph if item.table else item.paragraph and not item.row and not item.width))
            else:
                valid = item.sheet and item.row and item.width and not any(
                    (item.page, item.table, item.block, item.paragraph))
            if not valid:
                raise ValueError('Invalid format-specific source position.')
        return self

    def public(self):
        return {'format': self.format, 'content_sha256': self.content_sha256, 'parser': self.parser,
                'status': 'partial' if self.warnings else 'ready',
                'warnings': [WARNINGS[code] for code in self.warnings]}


def available_formats():
    return [kind for kind, (module, _) in _FORMATS.items() if find_spec(module) is not None]


def document_format(filename):
    _, separator, suffix = filename.rpartition('.')
    return suffix.lower() if separator and suffix.lower() in _FORMATS else None


def validate_document_upload(filename, media_type, kind):
    text_filename(filename, '.' + kind)
    if media_type.split(';', 1)[0].strip().lower() not in (_FORMATS[kind][1], 'application/octet-stream'):
        raise TextFileError('The file type does not match the selected document format.', status=415)
    if kind not in available_formats():
        raise TextFileError('The local parser for this format is unavailable.', status=503)


def run_local_parser(kind, raw):
    """Run the fixed local media worker with a killable wall-clock deadline."""
    env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP')
           if key in os.environ}
    try:
        with subprocess.Popen(
            [sys.executable, '-E', '-P', str(_WORKER), kind], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        ) as worker:
            expired = Event()

            def expire():
                if worker.poll() is None:
                    expired.set()
                    worker.kill()

            # Windows can block writing stdin before communicate's own timeout checks.
            deadline = Timer(PARSER_SECONDS, expire)
            deadline.start()
            try:
                output, _ = worker.communicate(raw)
            finally:
                deadline.cancel()
                deadline.join()
            if expired.is_set():
                raise TextFileError('Document extraction exceeded the local time limit.', status=422)
    except OSError:
        raise TextFileError('The local document parser could not start.', status=503) from None
    return worker.returncode, output


def import_document(kind, raw):
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise TextFileError('The document exceeds 512 KiB.', status=413)
    if not raw.startswith(b'%PDF-' if kind == 'pdf' else b'PK\x03\x04'):
        raise TextFileError('The file signature does not match its declared format.', status=415)
    code, output = run_local_parser(kind, raw)
    errors = {
        2: ('The document is malformed or contains unsupported or unsafe content.', 422),
        3: ('The document has no supported extractable content. Scanned PDFs require OCR.', 422),
        4: ('The document exceeds a local parsing limit.', 413),
        5: ('The local parser for this format is unavailable.', 503),
    }
    if code in errors:
        message, status = errors[code]
        raise TextFileError(message, status=status)
    try:
        if code or len(output) > 64 * 1024:
            raise ValueError
        projection = DocumentProjection.model_validate_json(output)
        if projection.format != kind or projection.content_sha256 != hashlib.sha256(raw).hexdigest():
            raise ValueError
        return projection
    except ValueError:
        raise TextFileError('The local parser did not return verified evidence.', status=503) from None


class NoDocumentEvidence(ValueError):
    pass


class _Projection:
    def __init__(self):
        self.lines = []
        self.locations = []
        self.warnings = set()

    def emit(self, line, **location):
        self.lines.append(line)
        if location:
            self.locations.append(DocumentLocator(line=len(self.lines), **location))
        if len(self.lines) > MAX_DOCUMENT_LINES:
            raise ExtractionLimit('Local document line limit exceeded.')

    def paragraph(self, text, heading=None, **location):
        self.emit('')
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if not _plain(line) or line.startswith(('#', '~~~', '[')) or set(line) <= {'-', '='}:
                self.warnings.add('TEXT_OMITTED')
                continue
            prefix = '#' * min(heading, 6) + ' ' if heading else ''
            self.emit(prefix + line, **location)
        self.emit('')

    def table(self, rows, first_row=1, **location):
        if not 2 <= len(rows) <= MAX_TABLE_ROWS + 1 or not 2 <= len(rows[0]) <= MAX_TABLE_COLUMNS:
            self.warnings.add('TABLE_OMITTED')
            return
        width = len(rows[0])
        prepared = []
        for row in rows:
            if len(row) != width:
                self.warnings.add('TABLE_OMITTED')
                return
            cells = []
            for value in row:
                value = '' if value is None else str(value).strip()
                if len(value) > MAX_TABLE_CELL or not _plain(value):
                    self.warnings.add('TEXT_OMITTED')
                    value = ''
                cells.append(value)
            prepared.append(cells)
        header = prepared[0]
        header_line = '| ' + ' | '.join(header) + ' |'
        if (not all(header) or len({' '.join(cell.casefold().split()) for cell in header}) != width
                or len(header_line) > MAX_TABLE_LINE):
            self.warnings.add('TABLE_OMITTED')
            return
        self.emit('')
        self.emit(header_line)
        self.emit('| ' + ' | '.join(['---'] * width) + ' |')
        for row, cells in enumerate(prepared[1:], first_row + 1):
            if not any(cells):
                continue
            line = '| ' + ' | '.join(cells) + ' |'
            if len(line) > MAX_TABLE_LINE:
                self.warnings.add('TABLE_OMITTED')
                continue
            self.emit(line, row=row, width=width, **location)
        self.emit('')


def _plain(text):
    return all(char.isprintable() or char == '\t' for char in text) and '|' not in text and chr(96) not in text


def _office_preflight(raw, kind, budget):
    main = 'word/document.xml' if kind == 'docx' else 'xl/workbook.xml'
    with ZipFile(BytesIO(raw)) as archive:
        members = archive.infolist()
        if len(members) > MAX_OFFICE_ENTRIES or sum(item.file_size for item in members) > MAX_OFFICE_EXPANDED:
            raise ExtractionLimit('Local Office expansion limit exceeded.')
        if any(not item.is_dir() and not item.filename.lower().endswith(('.xml', '.rels'))
               and not (item.filename == 'docProps/thumbnail.jpeg' and item.file_size <= 65536) for item in members):
            raise ValueError('Embedded media or objects are outside the local Office profile.')
        validate_openxml(raw, main, MAX_OFFICE_EXPANDED, budget, sheet_limit=MAX_SHEETS)
        for item in members:
            if item.filename.lower().endswith('.xml'):
                _inspect_office_part(archive, item, budget)
        return kind == 'docx' and any(item.filename.startswith(
            ('word/header', 'word/footer', 'word/footnotes', 'word/endnotes', 'word/comments')) for item in members)


def _inspect_office_part(archive, item, budget):
    parser = expat.ParserCreate(namespace_separator='}')
    paragraphs = 0

    def start(tag, attrs):
        nonlocal paragraphs
        if tag.startswith(_WORD):
            name = tag.removeprefix(_WORD)
            if name in _WORD_FIELDS:
                raise ValueError('Active or nonliteral Word content is outside the local profile.')
            if name == 'p' and item.filename == 'word/document.xml':
                paragraphs += 1
                if paragraphs > MAX_PARAGRAPHS:
                    raise ExtractionLimit('Local paragraph limit exceeded.')
        if tag == _SHEET + 'c':
            address = attrs.get('r', '')
            if (not address or len(address) > 3 or address[0] not in 'ABCDEFGHIJKLMNOP'
                    or not address[1:].isascii() or not address[1:].isdigit()
                    or not 1 <= int(address[1:]) <= MAX_SHEET_ROW or address[1] == '0'):
                raise ExtractionLimit('Cell is outside the local worksheet bounds.')
        if tag == _SHEET + 'row':
            row = attrs.get('r', '')
            if not row.isascii() or not row.isdigit() or len(row) > 2 or not 1 <= int(row) <= MAX_SHEET_ROW:
                raise ExtractionLimit('Row is outside the local worksheet bounds.')

    parser.StartElementHandler = start
    with archive.open(item) as stream:
        while chunk := stream.read(64 * 1024):
            budget.check()
            parser.Parse(chunk, False)
        parser.Parse(b'', True)


def _pdf(raw, projection, budget):
    from pypdf.generic import ArrayObject, DictionaryObject
    from backend.rag.ingest import DocumentParser

    reader = open_bounded_pdf(raw, budget)
    try:
        if len(reader.pages) > MAX_PAGES:
            raise ExtractionLimit('Local page limit exceeded.')
        todo = [reader.trailer, *reader.resolved_objects.values()]
        images = inspected = 0
        while todo:
            budget.check()
            value = todo.pop()
            inspected += 1
            if inspected > 20_000:
                raise ExtractionLimit('Local PDF structure limit exceeded.')
            if isinstance(value, DictionaryObject):
                if _PDF_FEATURES.intersection(value) or any(isinstance(child, str) and child in _PDF_FEATURES
                                                              for child in value.values()):
                    raise ValueError('Active PDF content is outside the local profile.')
                if value.get('/Subtype') == '/Image':
                    images += 1
                    if images > 8:
                        raise ExtractionLimit('Local PDF image limit exceeded.')
                todo.extend(value.values())
            elif isinstance(value, ArrayObject):
                todo.extend(value)
        _, metadata = DocumentParser._parse_pdf(raw, reader=reader, budget=budget)
    finally:
        reader.stream.close()
    blocks, tables, warnings = metadata['_evidence_parts']
    if images:
        projection.warnings.add('PDF_IMAGES_OMITTED')
    if any(warning.code in ('PDF_EMPTY_PAGE', 'PDF_OCR_UNAVAILABLE') for warning in warnings):
        projection.warnings.add('PDF_PAGE_OMITTED')
    if any(warning.code in ('PDF_TABLE_GEOMETRY_LIMIT', 'PDF_TABLE_GEOMETRY_UNVERIFIED', 'PDF_LAYOUT_UNAVAILABLE')
           for warning in warnings):
        projection.warnings.add('PDF_TABLES_UNVERIFIED')
    for page in range(1, metadata['page_count'] + 1):
        projection.emit(f'# Page {page}')
        for index, block in enumerate((block for block in blocks if block.location.page == page), 1):
            projection.paragraph(block.text, page=page, block=index)
        for table in tables:
            if table.location.page == page:
                projection.table(table.rows, page=page, table=table.location.table)
    return f'pypdf/{version("pypdf")}' + (f'; pdfplumber/{version("pdfplumber")}'
                                           if 'pdfplumber' in metadata['extraction_method'] else '')


def _docx(raw, projection):
    from backend.rag.ingest import DocumentParser

    evidence = DocumentParser.parse('source.docx', raw).evidence
    if evidence.warnings:
        projection.warnings.add('TABLE_OMITTED')
    for item in sorted((*evidence.blocks, *evidence.tables), key=lambda part: part.location.body):
        if item.location.paragraph is not None:
            projection.paragraph(item.text, heading=item.heading_level, paragraph=item.location.paragraph)
        else:
            projection.table(item.rows, table=item.location.table)
    return f'python-docx/{version("python-docx")}'


def _xlsx(raw, projection):
    from backend.multimodal.spreadsheet import parse_spreadsheet

    workbook = parse_spreadsheet('source.xlsx', raw)
    for sheet in workbook.sheets:
        grid = [list(row) for row in sheet.rows]
        for cell in sheet.cells:
            if cell.data_type in ('f', 'e'):
                grid[cell.location.row - 1][cell.location.column - 1] = None
                projection.warnings.add('XLSX_FORMULAS_OMITTED' if cell.data_type == 'f' else 'XLSX_ERRORS_OMITTED')
        first = next((index for index, row in enumerate(grid) if any(value is not None for value in row)), None)
        if first is None or not _plain(sheet.name) or not all(isinstance(cell, str) and cell.strip() for cell in grid[first]):
            projection.warnings.add('XLSX_SHEET_OMITTED')
            continue
        last = next(index for index in range(len(grid) - 1, first - 1, -1)
                    if any(cell is not None for cell in grid[index]))
        projection.emit(f'# Worksheet {sheet.name}')
        projection.table(grid[first:last + 1], first_row=first + 1, sheet=sheet.name)
    return f'openpyxl/{version("openpyxl")}'


def project_document(kind, raw):
    if kind not in available_formats():
        raise ImportError
    if not raw.startswith(b'%PDF-' if kind == 'pdf' else b'PK\x03\x04'):
        raise ValueError('Mismatched document signature.')
    budget = ExtractionBudget(5, 24_576)
    projection = _Projection()
    if kind == 'pdf':
        parser = _pdf(raw, projection, budget)
    else:
        if _office_preflight(raw, kind, budget):
            projection.warnings.add('DOCX_NON_BODY_OMITTED')
        parser = (_docx if kind == 'docx' else _xlsx)(raw, projection)
    budget.check()
    if not projection.locations:
        raise NoDocumentEvidence
    text = '\n'.join(projection.lines)
    if len(text.encode('utf-8')) > MAX_DOCUMENT_TEXT_BYTES:
        raise ExtractionLimit('Local extracted byte limit exceeded.')
    return DocumentProjection(format=kind, content_sha256=hashlib.sha256(raw).hexdigest(), parser=parser,
                              warnings=sorted(projection.warnings), text=text, locators=projection.locations)
