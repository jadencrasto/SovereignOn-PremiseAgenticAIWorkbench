"""Adapt approved local findings to the existing DOCX, XLSX and PDF renderers."""

import hashlib
import re
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.local_history import _safe_path
from backend.multimodal.text_file import TextFileError
from backend.tools.docx_create import DocxCreateInput, create_docx_create
from backend.tools.local_drafts import COLUMNS, HEADINGS, DraftConflict, DraftLayout, LocalDraft, MAX_EXPORT, _json
from backend.tools.pdf_report import render_pdf_report
from backend.tools.xlsx_report import XlsxReportInput, create_xlsx_report

MAX_EXPORT_INPUT = 512 * 1024
MIME = {
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'pdf': 'application/pdf',
}
LABELS = {
    'source_value': 'Source-attributed value', 'source_excerpt': 'Source excerpt',
    'visual_interpretation': 'Model-generated visual interpretation', 'missing': 'Insufficient evidence',
    'user_edited': 'User-edited wording, unverified', 'comparison': 'Comparison of cited values',
}


def position(locator):
    if locator['format'] == 'pdf':
        return ' '.join(f'{key} {locator[key]}' for key in ('page', 'block', 'table', 'row', 'column') if key in locator)
    if locator['format'] == 'docx':
        return ' '.join(f'{key} {locator[key]}' for key in ('paragraph', 'table', 'row', 'column') if key in locator)
    return f"{locator['sheet']}!{locator.get('cell', locator.get('range', ''))}"


def source_label(finding):
    if source := finding.source:
        locator = (finding.field or {}).get('document_locator') or source.get('document_locator')
        location = position(locator) if locator else (
            f"line {source['line']}, span {(finding.field or source)['start']}-{(finding.field or source)['end']}"
        )
        return f"{source['label']} - {location} - {source['source_id']} revision {source['revision']} SHA-256 {source['digest']}"
    if image := finding.image:
        return (f"{image['filename']} - image {image['attachment_id']} SHA-256 {image['sha256']} "
                f"- model {image['model']} - analysis {image['processing']}")
    return ''


def render_args(draft):
    layout = draft.layout or DraftLayout()
    rows, references, comparison, observations = [], {}, [], []
    for index, finding in enumerate(draft.findings):
        label = source_label(finding)
        topic = finding.topic
        if label:
            number = references.setdefault(label, len(references) + 1)
            citation = f'[{number}] {label}' if draft.format == 'xlsx' else f'[{number}]'
            if finding.kind == 'user_edited':
                citation = 'Original evidence reference ' + citation
            if layout.inline_references and draft.format != 'xlsx':
                topic = f'{topic}\n{citation} {label}'
        else:
            citation = ''
        field = finding.field if finding.kind == 'source_value' else None
        status = f'{LABELS[finding.kind]} / {finding.coverage if finding.kind != "user_edited" else "unverified"}'
        cells = {'finding': topic, 'value': finding.text, 'unit': (field or {}).get('unit') or '',
                 'equipment': ', '.join((field or {}).get('equipment', ())),
                 'condition': ', '.join((field or {}).get('conditions', ())), 'status': status, 'reference': citation}
        rows.append([cells[key] for key in layout.columns])
        if field and (not layout.comparison_attribute or layout.comparison_attribute in field['attribute']):
            comparison.append(index)
        if finding.image:
            observations.append((index, f'{status}\n{finding.text}\n{citation}'))
    headers = [layout.headers.get(key, COLUMNS[key]) for key in layout.columns]
    sections = []
    for key in layout.order:
        if key == 'summary':
            body = f'Local export revision {draft.revision}. Summary wording is unverified. {draft.summary}'
        elif key == 'references':
            if not references or draft.format == 'xlsx':
                continue
            body = '\n'.join(f'[{n}] {source}' for source, n in references.items())
        elif key == 'visual':
            if not observations or draft.format == 'xlsx':
                continue
            body = '\n\n'.join(text for _, text in observations)
        else:
            body = f'Editorial wording is unverified. {layout.notes.get(key, "")}'
        sections.append({'heading': layout.headings.get(key, HEADINGS[key]), 'body': body})
    # Exact values stay as strings, preserving signs, decimal precision and units.
    if draft.format == 'xlsx':
        summary = '\n'.join(f'{section["heading"]}\n{section["body"]}' for section in sections)
        return XlsxReportInput(filename='report.xlsx', title=draft.title, headers=headers, rows=rows,
                               summary_notes=summary or f'Local export revision {draft.revision}.',
                               comparison_rows=comparison if layout.comparison and comparison else None)
    if 'summary' not in layout.order:
        sections.insert(0, {'heading': '', 'body': f'Local export revision {draft.revision}.'})
    visual_indices = {index for index, _ in observations} if 'visual' in layout.order else set()
    tables = [{'headers': headers, 'rows': [row for index, row in enumerate(rows) if index not in visual_indices]}]
    if layout.comparison and comparison:
        selected = [rows[index] for index in comparison]
        tables.append({'headers': [f'Comparison - {headers[0]}', *headers[1:]], 'rows': selected})
    return DocxCreateInput(filename='report.docx', title=draft.title, sections=sections, tables=tables)


def generate_in_worker(kind, data):
    draft = LocalDraft.model_validate_json(data)
    if (draft.format != kind or draft.approval is None or draft.approval.revision != draft.revision
            or draft.exported is not None):
        raise ValueError('Invalid export input')
    args = render_args(draft)
    with TemporaryDirectory(prefix='sovereign-local-export-') as directory:
        root = Path(directory)
        if kind == 'pdf':
            result = render_pdf_report(root, args)
        else:
            factory = create_docx_create if kind == 'docx' else create_xlsx_report
            # These async tool wrappers perform synchronous serialization and
            # do not suspend. Drive them without opening asyncio's self socket.
            process = factory(root)(args)
            try:
                process.send(None)
            except StopIteration as done:
                result = done.value
            else:
                process.close()
                raise RuntimeError('Unexpected export suspension')
        filename = result['filename']
        if not re.fullmatch(rf'[0-9a-f]{{32}}\.{kind}', filename):
            raise ValueError('Invalid generated file identity')
        path = root / filename
        if not _safe_path(path) or path.stat().st_size > MAX_EXPORT:
            raise ValueError('Invalid generated file')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != result['sha256_hash']:
            raise ValueError('Generated file integrity check failed')
        return raw


def generate_export(draft):
    from backend.rag.local_documents import run_local_parser

    if draft.format == 'pdf' and any(find_spec(name) is None for name in ('pymupdf', 'pypdf')):
        raise DraftConflict('The optional local PDF renderer is unavailable.')
    if draft.format == 'pdf' and not all(value.isascii() for value in _report_strings(render_args(draft).model_dump())):
        raise DraftConflict('PDF export requires ASCII text. Use DOCX for the original characters.')
    encoded = _json(draft.model_dump())
    if len(encoded) > MAX_EXPORT_INPUT:
        raise DraftConflict('The draft exceeds the local export input limit.')
    try:
        code, raw = run_local_parser('artifact-' + draft.format, encoded)
    except TextFileError:
        raise DraftConflict('Local generation timed out or the renderer could not start.') from None
    signature = b'%PDF-' if draft.format == 'pdf' else b'PK\x03\x04'
    if code or len(raw) > MAX_EXPORT or not raw.startswith(signature):
        raise DraftConflict('The local renderer could not produce a complete file within its limits.')
    return raw


def _report_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for value in node:
            yield from _report_strings(value)
    elif isinstance(node, dict):
        for value in node.values():
            yield from _report_strings(value)
