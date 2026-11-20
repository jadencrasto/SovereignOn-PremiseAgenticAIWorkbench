"""Adapt approved local findings to the existing DOCX, XLSX and PDF renderers."""

import hashlib
import re
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.local_history import _safe_path
from backend.multimodal.text_file import TextFileError
from backend.tools.docx_create import DocxCreateInput, create_docx_create
from backend.tools.local_drafts import DraftConflict, LocalDraft, MAX_EXPORT, _json
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
    'user_edited': 'User-edited wording, unverified',
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
    rows, references = [], {}
    for finding in draft.findings:
        label = source_label(finding)
        if label:
            number = references.setdefault(label, len(references) + 1)
            citation = f'[{number}] {label}' if draft.format == 'xlsx' else f'[{number}]'
            if finding.kind == 'user_edited':
                citation = 'Original evidence reference ' + citation
        else:
            citation = ''
        field = finding.field if finding.kind == 'source_value' else None
        rows.append([finding.topic, finding.text, (field or {}).get('unit') or '',
                     ', '.join((field or {}).get('equipment', ())), ', '.join((field or {}).get('conditions', ())),
                     f'{LABELS[finding.kind]} / {finding.coverage if finding.kind != "user_edited" else "unverified"}',
                     citation])
    headers = ['Finding', 'Reported value or wording', 'Unit', 'Equipment', 'Condition', 'Evidence status', 'Reference']
    # Exact values stay as strings, preserving signs, decimal precision and units.
    if draft.format == 'xlsx':
        return XlsxReportInput(filename='report.xlsx', title=draft.title, headers=headers, rows=rows,
                               summary_notes=f'Local export revision {draft.revision}. {draft.summary}')
    sections = [{'heading': 'Executive summary',
                 'body': f'Local export revision {draft.revision}. Summary wording is unverified. {draft.summary}'}]
    if references:
        sections.append({'heading': 'Source references', 'body': '\n'.join(f'[{n}] {source}' for source, n in references.items())})
    return DocxCreateInput(filename='report.docx', title=draft.title, sections=sections,
                           tables=[{'headers': headers, 'rows': rows}])


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
