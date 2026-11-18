"""
backend/rag/ingest.py
---------------------
Document parsing and chunking for the RAG pipeline.

Supported formats: PDF, TXT, MD, DOCX

Design:
  - Parsing produces a Document (full text + metadata).
  - Chunking splits a Document into Chunks with preserved metadata.
  - All chunk IDs are deterministic (doc_id + chunk index) so
    re-ingesting the same file produces the same IDs — safe for upsert.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from backend.multimodal.document_evidence import (
    DocumentEvidence, ExtractedBlock, ExtractedTable, ExtractionBudget, ExtractionLimit,
    ExtractionWarning, SourceLocation, header_unit, valid_source_filename,
)
from backend.multimodal.openxml_limits import validate_openxml
from backend.multimodal.pdf_limits import open_bounded_pdf

logger = logging.getLogger(__name__)


def _markdown_table(rows):
    lines = []
    for index, row in enumerate(rows):
        values = [(cell or '').replace('\n', ' ').replace('|', '\\|') for cell in row]
        lines.append('| ' + ' | '.join(values) + ' |')
        if index == 0:
            lines.append('| ' + ' | '.join(['---'] * len(row)) + ' |')
    return '\n'.join(lines)

# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------

@dataclass
class Document:
    """Parsed representation of an uploaded file."""
    document_id: str        # stable identifier derived from filename and complete content
    filename: str
    file_type: str          # pdf | txt | md | docx
    text: str               # full extracted text
    metadata: dict = field(default_factory=dict)
    evidence: DocumentEvidence | None = field(default=None, compare=False, repr=False)


@dataclass
class Chunk:
    """A text chunk ready for embedding and storage."""
    chunk_id: str           # "{document_id}_chunk_{index}"
    document_id: str
    filename: str
    file_type: str
    text: str
    chunk_index: int
    metadata: dict = field(default_factory=dict)   # page, source, etc.


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class DocumentParser:
    """
    Parses raw file bytes into a Document.

    Each parse_* method returns the extracted text and per-page metadata
    where available.
    """

    SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx"}
    MAX_INPUT_BYTES = 50 * 1024 * 1024
    MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
    MAX_OUTPUT_CHARS = 10 * 1024 * 1024
    EXTRACTION_SECONDS = 5

    @classmethod
    def parse(cls, filename: str, content: bytes) -> Document:
        """
        Main entry point.

        Args:
            filename : original uploaded filename (used for type detection)
            content  : raw file bytes

        Returns:
            Document with extracted text and metadata.

        Raises:
            ValueError : unsupported extension or empty content
        """
        if not valid_source_filename(filename):
            raise ValueError("Invalid document filename.")
        suffix = Path(filename).suffix.lower()
        if suffix not in cls.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type '{suffix}'. "
                f"Supported: {sorted(cls.SUPPORTED_EXTENSIONS)}"
            )
        if not content:
            raise ValueError("File content is empty.")

        if not isinstance(content, bytes) or len(content) > cls.MAX_INPUT_BYTES:
            raise ValueError("File exceeds the parser size limit or is not bytes.")
        if suffix == ".pdf" and not content.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF signature.")
        if suffix in (".txt", ".md") and (b"\x00" in content or content.startswith((b"%PDF-", b"PK\x03\x04"))):
            raise ValueError("File is not supported plain text.")

        doc_id = cls._make_doc_id(filename, content)

        if suffix == ".pdf":
            budget = ExtractionBudget(cls.EXTRACTION_SECONDS, cls.MAX_OUTPUT_CHARS)
            try:
                reader = open_bounded_pdf(content, budget)
                try:
                    text, metadata = cls._parse_pdf(content, reader=reader, budget=budget)
                finally:
                    reader.stream.close()
            except ExtractionLimit:
                raise
            except Exception:
                raise ValueError("PDF could not be parsed within the supported document profile.") from None
        elif suffix == ".docx":
            text, metadata = cls._parse_docx(content)
        else:
            # .txt and .md
            text, metadata = cls._parse_text(content)

        parts = metadata.pop("_evidence_parts", None)
        evidence = DocumentEvidence.from_parts(filename, suffix[1:], *parts) if parts is not None else None
        if len(text) > cls.MAX_OUTPUT_CHARS:
            raise ValueError("Extracted text exceeds the parser limit.")
        if not text.strip() and not (evidence and any(w.code == "PDF_OCR_UNAVAILABLE" for w in evidence.warnings)):
            raise ValueError("No text could be extracted from the document.")

        metadata["content_sha256"] = hashlib.sha256(content).hexdigest()
        return Document(
            document_id=doc_id,
            filename=filename,
            file_type=suffix.lstrip("."),
            text=text,
            metadata=metadata,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # Format-specific parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_pdf(content: bytes, *, reader=None, budget=None) -> tuple[str, dict]:
        """Preserve actual pages. Only geometry-backed grids are citation evidence."""
        from pypdf import PdfReader

        bounded = reader is not None
        if reader is None:
            reader = PdfReader(io.BytesIO(content))
        if budget is None:
            budget = ExtractionBudget(DocumentParser.EXTRACTION_SECONDS, DocumentParser.MAX_OUTPUT_CHARS)
        page_count = len(reader.pages)
        pages, blocks, tables, warnings = [], [], [], []
        methods = set()
        plumber = None
        try:
            try:
                import pdfplumber
                plumber = pdfplumber.open(io.BytesIO(content))
                if bounded and len(plumber.pages) != page_count:
                    plumber.close()
                    plumber = None
            except Exception:
                plumber = None

            for number in range(1, page_count + 1):
                budget.check()
                location = SourceLocation(page=number)
                page_text = ''
                page_tables = []
                has_images = False
                method = 'pypdf'
                if plumber is not None:
                    try:
                        page = plumber.pages[number - 1]
                        has_images = bool(page.images) if bounded else False
                        page_text, page_tables = DocumentParser._pdf_page(page, location, budget, warnings, bounded)
                        if page_text.strip() or page_tables:
                            method = 'pdfplumber'
                    except ExtractionLimit:
                        raise
                    except Exception:
                        warnings.append(ExtractionWarning('PDF_LAYOUT_UNAVAILABLE', location))
                if method == 'pypdf':
                    source_page = reader.pages[number - 1]
                    if bounded:
                        page_text = source_page.extract_text(visitor_text=lambda text, *_: budget.consume(text)) or ''
                    else:
                        page_text = source_page.extract_text() or ''
                    if bounded and not has_images:
                        resources = reader.pages[number - 1].get('/Resources')
                        xobjects = resources.get_object().get('/XObject') if resources is not None else None
                        if xobjects is not None:
                            has_images = any(ref.get_object().get('/Subtype') == '/Image'
                                             for ref in xobjects.get_object().values())
                budget.consume(page_text)
                entries = [text.strip() for text in re.split(r'\n[ \t]*\n', page_text) if text.strip()]
                if bounded and entries and not page_tables:
                    warnings.append(ExtractionWarning('PDF_NO_VERIFIED_TABLE_STRUCTURE', location))
                blocks.extend(ExtractedBlock(location, text) for text in entries)
                if len(tables) + len(page_tables) > 64:
                    raise ExtractionLimit('PDF exceeds the table limit.')
                tables.extend(page_tables)
                entries.extend(_markdown_table(table.rows) for table in page_tables)
                if entries:
                    page_output = f"[Page {number}]\n" + "\n\n".join(entries)
                    budget.check()
                    pages.append(page_output)
                    methods.add(method)
                else:
                    code = 'PDF_OCR_UNAVAILABLE' if has_images else 'PDF_EMPTY_PAGE'
                    warnings.append(ExtractionWarning(code, location))
        finally:
            if plumber is not None:
                plumber.close()
        budget.check()
        metadata = {
            'page_count': page_count, 'source_format': 'pdf',
            'extraction_method': '+'.join(sorted(methods)) or 'pypdf',
            '_evidence_parts': (blocks, tables, warnings),
        }
        return "\n\n".join(pages), metadata

    @staticmethod
    def _pdf_page(page, location, budget, warnings, bounded):
        if bounded and len(page.chars) > 30_000:
            raise ExtractionLimit('PDF page exceeds the text object limit.')
        tables_found = []
        if bounded and len(page.edges) > 120:
            warnings.append(ExtractionWarning('PDF_TABLE_GEOMETRY_LIMIT', location))
        else:
            tables_found = page.find_tables() or []
        budget.check()
        # Keep the private legacy test path compatible. The real bounded entry
        # point does not accept text alignment as verified table geometry.
        candidates = [table.extract() for table in tables_found]
        if not candidates and not bounded:
            candidates = page.extract_tables() or []
        budget.check()
        tables, boxes = [], []
        for index, candidate in enumerate(candidates, 1):
            if (not candidate or len(candidate) < 2 or not 2 <= len(candidate[0]) <= 40
                    or len(candidate) > 250 or any(len(row) != len(candidate[0]) for row in candidate)
                    or (bounded and any(cell is None for row in candidate for cell in row))):
                warnings.append(ExtractionWarning('PDF_TABLE_GEOMETRY_UNVERIFIED', location))
                continue
            rows = tuple(tuple(cell.strip() if cell is not None else None for cell in row) for row in candidate)
            for row in rows:
                for cell in row:
                    if cell is not None:
                        budget.consume(cell)
            budget.consume(_markdown_table(rows))
            tables.append(ExtractedTable(SourceLocation(page=location.page, table=index), rows,
                                         tuple(header_unit(cell) for cell in rows[0])))
            if index <= len(tables_found):
                boxes.append(tables_found[index - 1].bbox)
        if boxes:
            def outside_table(obj):
                return not any(
                    obj.get('x0', 0) >= x0 - 2 and obj.get('x1', 0) <= x1 + 2
                    and obj.get('top', 0) >= top - 2 and obj.get('bottom', 0) <= bottom + 2
                    for x0, top, x1, bottom in boxes
                )

            text = page.filter(outside_table).extract_text() or ''
        else:
            text = page.extract_text() or ''
            if bounded and not tables and re.search(r'\S+[ \t]{2,}\S+', text):
                warnings.append(ExtractionWarning('PDF_TABLE_GEOMETRY_UNVERIFIED', location))
        budget.check()
        return text, tables

    @staticmethod
    def _apply_table_heuristic(page_text: str) -> str:
        """
        Lightweight heuristic to detect tabular patterns in extracted PDF text.

        Looks for consecutive lines with 2+ whitespace-separated columns
        and formats them as Markdown tables with aligned rows and padded blank cells.

        Returns the page text with detected tables converted to Markdown format.
        If no tables are detected, returns the text unchanged.
        """
        lines = page_text.split("\n")
        result_parts = []
        table_buffer = []

        def _flush_table(buf):
            """Convert buffered table-like lines to Markdown table with preserved columns."""
            if len(buf) < 2:
                return "\n".join(buf)

            # Strategy 1: Positional column spans from header
            header_line = buf[0]
            header_matches = list(re.finditer(r"\S+(?:\s\S+)*?(?=\s{2,}|\t|$)", header_line.strip()))

            if len(header_matches) >= 2:
                col_starts = [m.start() for m in header_matches]
                headers = [m.group().strip().replace("|", "\\|") for m in header_matches]
                num_cols = len(headers)

                parsed_rows = [headers]
                use_positional = True
                for line in buf[1:]:
                    row_cells = []
                    for i in range(num_cols):
                        start = col_starts[i]
                        if i + 1 < num_cols:
                            next_start = col_starts[i + 1]
                            cell_text = line[start:next_start] if len(line) > start else ""
                        else:
                            cell_text = line[start:] if len(line) > start else ""
                        row_cells.append(cell_text.strip().replace("|", "\\|"))
                    if not any(row_cells):
                        use_positional = False
                        break
                    parsed_rows.append(row_cells)

                if use_positional and len(parsed_rows) >= 2:
                    md_lines = [
                        "| " + " | ".join(parsed_rows[0]) + " |",
                        "| " + " | ".join(["---"] * num_cols) + " |",
                    ]
                    for row in parsed_rows[1:]:
                        md_lines.append("| " + " | ".join(row) + " |")
                    return "\n".join(md_lines)

            # Strategy 2: Fallback to token splitting
            parsed_rows = []
            for line in buf:
                cells = [c.strip() for c in re.split(r"\s{2,}|\t", line.strip()) if c.strip()]
                if cells:
                    parsed_rows.append(cells)
            if len(parsed_rows) < 2:
                return "\n".join(buf)

            max_cols = max(len(r) for r in parsed_rows)
            if max_cols < 2:
                return "\n".join(buf)

            headers = parsed_rows[0] + [""] * (max_cols - len(parsed_rows[0]))
            headers = [c.replace("|", "\\|") for c in headers]
            md_lines = [
                "| " + " | ".join(headers) + " |",
                "| " + " | ".join(["---"] * max_cols) + " |",
            ]
            for row in parsed_rows[1:]:
                padded = row + [""] * (max_cols - len(row))
                cleaned = [c.replace("|", "\\|") for c in padded]
                md_lines.append("| " + " | ".join(cleaned) + " |")

            return "\n".join(md_lines)

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if table_buffer:
                    result_parts.append(_flush_table(table_buffer))
                    table_buffer = []
                result_parts.append("")
                continue

            # Heuristic: line has 2+ segments separated by 2+ spaces or tabs
            segments = [c.strip() for c in re.split(r"\s{2,}|\t", stripped) if c.strip()]
            if len(segments) >= 2:
                table_buffer.append(stripped)
            else:
                if table_buffer:
                    result_parts.append(_flush_table(table_buffer))
                    table_buffer = []
                result_parts.append(stripped)

        if table_buffer:
            result_parts.append(_flush_table(table_buffer))

        return "\n".join(result_parts)

    @classmethod
    def _parse_docx(cls, content: bytes) -> tuple[str, dict]:
        """Extract paragraphs and tables from a DOCX file in document body order."""
        try:
            from docx import Document as DocxDocument
            from docx.table import Table as DocxTable
            from docx.text.paragraph import Paragraph as DocxParagraph
            from docx.oxml.ns import qn
        except ImportError:
            raise RuntimeError("python-docx is required for DOCX parsing.") from None
        budget = ExtractionBudget(cls.EXTRACTION_SECONDS, cls.MAX_OUTPUT_CHARS)
        try:
            if not isinstance(content, bytes) or not content or len(content) > cls.MAX_INPUT_BYTES:
                raise ExtractionLimit('DOCX exceeds the parser size limit or is empty.')
            validate_openxml(content, 'word/document.xml', cls.MAX_DOCX_UNCOMPRESSED_BYTES, budget)
            doc = DocxDocument(io.BytesIO(content))
            parts, blocks, tables, warnings = [], [], [], []
            table_count = paragraph_index = 0
            for body_index, element in enumerate(doc.element.body, 1):
                budget.check()
                if element.tag == qn('w:p'):
                    paragraph_index += 1
                    para = DocxParagraph(element, doc)
                    text = para.text.strip()
                    if text:
                        budget.consume(text)
                        heading = re.fullmatch(r'Heading([1-9])', para.style.style_id)
                        blocks.append(ExtractedBlock(SourceLocation(paragraph=paragraph_index, body=body_index), text,
                                                     int(heading[1]) if heading else None))
                        parts.append(text)
                elif element.tag == qn('w:tbl'):
                    table_count += 1
                    if table_count > 64:
                        raise ExtractionLimit('DOCX exceeds the table limit.')
                    location = SourceLocation(table=table_count, body=body_index)
                    rows = cls._docx_grid(DocxTable(element, doc), location, budget, warnings)
                    if rows:
                        rendered = _markdown_table(rows)
                        budget.consume(rendered)
                        tables.append(ExtractedTable(location, rows, tuple(header_unit(cell) for cell in rows[0])))
                        parts.append(rendered)
            metadata = {
                'paragraph_count': len(blocks), 'table_count': table_count,
                'has_tables': table_count > 0, 'source_format': 'docx',
                '_evidence_parts': (blocks, tables, warnings),
            }
            for name in ('title', 'author'):
                value = getattr(doc.core_properties, name)
                if value and len(value) <= 255 and value.isprintable():
                    metadata[name] = value
            budget.check()
            return '\n\n'.join(parts), metadata
        except ExtractionLimit:
            raise
        except Exception:
            raise ValueError('DOCX could not be parsed within the supported document profile.') from None

    @staticmethod
    def _docx_grid(table, location, budget, warnings):
        from docx.table import _Cell

        width = len(table.columns)
        if width > 40 or len(table.rows) > 250:
            raise ExtractionLimit('DOCX table exceeds the row or column limit.')
        rows = []
        for row in table.rows:
            budget.check()
            if not 0 <= row.grid_cols_before <= width or not 0 <= row.grid_cols_after <= width:
                raise ValueError('Invalid DOCX table grid.')
            cells = [None] * row.grid_cols_before
            for tc in row._tr.tc_lst:
                span = tc.grid_span
                if not 1 <= span <= width - len(cells):
                    raise ValueError('Invalid DOCX table span.')
                text = None if tc.vMerge == 'continue' else _Cell(tc, table).text.strip()
                if text is not None:
                    budget.consume(text)
                cells.extend([text] + [None] * (span - 1))
                if tc.tbl_lst:
                    warning = ExtractionWarning('DOCX_NESTED_TABLE_OMITTED', location)
                    if warning not in warnings:
                        warnings.append(warning)
            cells.extend([None] * row.grid_cols_after)
            if len(cells) != width or not width:
                warnings.append(ExtractionWarning('DOCX_TABLE_GRID_UNVERIFIED', location))
                return ()
            rows.append(tuple(cells))
        return tuple(rows)

    @staticmethod
    def _parse_text(content: bytes) -> tuple[str, dict]:
        """Read plain text / markdown, gracefully handling encoding."""
        for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
            try:
                text = content.decode(encoding)
                return text, {"source_format": "text", "encoding": encoding}
            except UnicodeDecodeError:
                continue
        # Final fallback: replace errors
        text = content.decode("utf-8", errors="replace")
        logger.warning("Text file decoded with error replacement")
        return text, {"source_format": "text", "encoding": "utf-8-lossy"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_doc_id(filename: str, content: bytes) -> str:
        """Stable document ID from filename + content hash."""
        h = hashlib.sha256(filename.encode("utf-8") + b"\0" + content).hexdigest()[:32]
        return f"doc_{h}"


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------

class TextChunker:
    """
    Splits a Document into overlapping text chunks.

    Split normalized paragraphs into bounded character windows with fixed overlap.

    This paragraph-aware approach preserves semantic boundaries better
    than pure character splitting.
    """

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 150) -> None:
        if chunk_size <= 0 or chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, document: Document) -> List[Chunk]:
        """
        Split a Document into Chunks.

        Returns an empty list only if the document had no usable text.
        """
        raw_paragraphs = self._split_paragraphs(document.text)
        windows = self._build_windows(raw_paragraphs)

        chunks: List[Chunk] = []
        for idx, window_text in enumerate(windows):
            text = window_text.strip()
            if not text:
                continue
            chunk_id = f"{document.document_id}_chunk_{idx}"
            # Carry page metadata if available (PDF stores "[Page N]" markers)
            # Text markers are not trusted evidence of a source page.
            page = None
            chunk_meta = {**document.metadata, "chunk_index": idx}
            if page is not None:
                chunk_meta["page"] = page

            chunks.append(Chunk(
                chunk_id=chunk_id,
                document_id=document.document_id,
                filename=document.filename,
                file_type=document.file_type,
                text=text,
                chunk_index=idx,
                metadata=chunk_meta,
            ))

        logger.debug(
            "Chunked '%s': %d chunks (size=%d overlap=%d)",
            document.filename, len(chunks), self.chunk_size, self.chunk_overlap,
        )
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_paragraphs(text: str) -> List[str]:
        """Split on two or more newlines; keep non-empty paragraphs."""
        parts = re.split(r"\n{2,}", text)
        return [p.strip() for p in parts if p.strip()]

    def _build_windows(self, paragraphs: List[str]) -> List[str]:
        """
        Greedily accumulate paragraphs into windows ≤ chunk_size characters,
        then prepend an overlap tail from the previous window.
        """
        text = "\n\n".join(paragraphs)
        if not text:
            return []
        windows = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            windows.append(text[start:end])
            if end == len(text):
                break
            start = end - self.chunk_overlap
        return windows

    @staticmethod
    def _extract_page_hint(text: str) -> Optional[int]:
        """
        Extract the first [Page N] marker from text, if present.
        PDFs are parsed with these markers prepended per page.
        """
        m = re.search(r"\[Page (\d+)\]", text)
        if m:
            return int(m.group(1))
        return None
