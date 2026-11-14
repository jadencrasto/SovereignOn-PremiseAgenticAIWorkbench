"""Isolated PDF rendering from a validated report; not registered as a tool.

This requires locally installed PyMuPDF and does not authorize publication.
"""
from __future__ import annotations
import hashlib
import io
import os
import textwrap
from pathlib import Path
from backend.tools.report_contract import ReportInput, tool_arguments
from backend.tools.artifact_storage import create_artifact_file


def create_pdf_report(sandbox_dir: Path, report: ReportInput, sources: dict[str, str]) -> dict:
    if not isinstance(report, ReportInput) or report.output_format != 'pdf':
        raise ValueError('A PDF report is required')
    args = tool_arguments(report.model_copy(update={'output_format':'docx'}), sources)
    blocks = [args.title]
    for section in args.sections or []:
        blocks.extend([section['heading'], section['body']])
    for table in args.tables or []:
        blocks.append(' | '.join(str(x) for x in table['headers']))
        blocks.extend(' | '.join('' if x is None else str(x) for x in row) for row in table['rows'])
    if any(not text.isascii() or any(ord(c) < 32 and c not in '\n\r\t' for c in text) for text in blocks):
        raise ValueError('PDF renderer currently supports ASCII text only')
    lines=[]
    for block in blocks:
        for line in block.splitlines() or ['']:
            lines.extend(textwrap.wrap(line, width=80, replace_whitespace=True, drop_whitespace=True) or [''])
    if len(lines) > 1000: raise ValueError('PDF page limit exceeded')
    try:
        import fitz
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError('PDF renderer unavailable') from exc
    document=fitz.open()
    try:
        for start in range(0, len(lines), 50):
            page=document.new_page()
            for index,line in enumerate(lines[start:start+50]):
                if line: page.insert_text((45, 50+index*14), line, fontsize=9)
        data=document.tobytes()
    finally:
        document.close()
    if not data or len(data)>5*1024*1024: raise ValueError('PDF output exceeds limit')
    reader=PdfReader(io.BytesIO(data))
    extracted='\n'.join(page.extract_text() or '' for page in reader.pages)
    if any(line and line not in extracted for line in lines):
        raise RuntimeError('PDF content verification failed')
    artifact_id, filename, stream = create_artifact_file(sandbox_dir, 'report.pdf')
    with stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    path=Path(sandbox_dir).resolve()/filename
    written=path.read_bytes()
    if written != data: raise RuntimeError('PDF write verification failed')
    return {'artifact_id':artifact_id,'filename':filename,'display_name':'report.pdf',
            'size_bytes':len(data),'sha256_hash':hashlib.sha256(data).hexdigest(),
            'content_type':'application/pdf'}
