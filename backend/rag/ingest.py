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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------

@dataclass
class Document:
    """Parsed representation of an uploaded file."""
    document_id: str        # stable UUID derived from filename hash
    filename: str
    file_type: str          # pdf | txt | md | docx
    text: str               # full extracted text
    metadata: dict = field(default_factory=dict)


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
        suffix = Path(filename).suffix.lower()
        if suffix not in cls.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type '{suffix}'. "
                f"Supported: {sorted(cls.SUPPORTED_EXTENSIONS)}"
            )
        if not content:
            raise ValueError("File content is empty.")

        doc_id = cls._make_doc_id(filename, content)

        if suffix == ".pdf":
            try:
                text, metadata = cls._parse_pdf(content)
            except Exception as exc:
                raise ValueError(f"Failed to parse PDF '{filename}': {exc}") from exc
        elif suffix == ".docx":
            text, metadata = cls._parse_docx(content)
        else:
            # .txt and .md
            text, metadata = cls._parse_text(content)

        if not text.strip():
            raise ValueError(f"No text could be extracted from '{filename}'.")

        return Document(
            document_id=doc_id,
            filename=filename,
            file_type=suffix.lstrip("."),
            text=text,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Format-specific parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_pdf(content: bytes) -> tuple[str, dict]:
        """
        Extract text from PDF with structured table preservation.

        3-tier extraction hierarchy:
          1. pdfplumber (if installed) — structured table extraction
          2. Lightweight line-based heuristic — detect tabular patterns
          3. pypdf extract_text() — final fallback (existing behavior)

        A failure in table detection NEVER causes the entire PDF ingestion to fail.
        """
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError("pypdf is required for PDF parsing: pip install pypdf")

        reader = PdfReader(io.BytesIO(content))
        page_count = len(reader.pages)
        pages = []

        # Tier 1: Try pdfplumber for structured table extraction
        pdfplumber_available = False
        try:
            import pdfplumber
            pdfplumber_available = True
        except ImportError:
            pass

        if pdfplumber_available:
            try:
                pdf_pl = pdfplumber.open(io.BytesIO(content))
                table_settings = {"snap_x_tolerance": 6, "join_x_tolerance": 6}

                for page_num, pl_page in enumerate(pdf_pl.pages, start=1):
                    page_parts = []
                    try:
                        found_tables = []
                        if hasattr(pl_page, "find_tables"):
                            try:
                                found_tables = pl_page.find_tables(table_settings) or []
                            except Exception:
                                found_tables = []

                        table_bboxes = [t.bbox for t in found_tables if hasattr(t, "bbox") and t.bbox]

                        raw_tables = []
                        for t in found_tables:
                            if hasattr(t, "extract"):
                                extr = t.extract()
                                if extr:
                                    raw_tables.append(extr)
                        if not raw_tables and hasattr(pl_page, "extract_tables"):
                            try:
                                raw_tables = pl_page.extract_tables() or []
                            except Exception:
                                raw_tables = []
                        if not raw_tables and hasattr(pl_page, "extract_tables"):
                            try:
                                raw_tables = pl_page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "text"}) or []
                            except Exception:
                                raw_tables = []

                        # Extract tables as structured Markdown tables
                        md_tables = []
                        for raw_table in raw_tables:
                            if not raw_table or len(raw_table) < 1:
                                continue

                            num_cols = max(len(r) for r in raw_table)
                            # Identify columns that have at least one non-empty cell across rows
                            col_has_content = [
                                any(c_idx < len(r) and r[c_idx] is not None and str(r[c_idx]).strip() != "" for r in raw_table)
                                for c_idx in range(num_cols)
                            ]

                            cleaned_rows = []
                            for row in raw_table:
                                row_padded = list(row) + [""] * (num_cols - len(row))
                                cleaned = [
                                    str(cell or "").strip().replace("\n", " ").replace("|", "\\|")
                                    for i, cell in enumerate(row_padded)
                                    if col_has_content[i]
                                ]
                                if any(cleaned):
                                    cleaned_rows.append(cleaned)

                            if cleaned_rows and len(cleaned_rows) >= 2:
                                headers = cleaned_rows[0]
                                md_lines = [
                                    "| " + " | ".join(headers) + " |",
                                    "| " + " | ".join(["---"] * len(headers)) + " |",
                                ]
                                for row_cells in cleaned_rows[1:]:
                                    md_lines.append("| " + " | ".join(row_cells) + " |")
                                md_tables.append("\n".join(md_lines))

                        # Extract text outside tables by filtering out table bounding boxes
                        if table_bboxes:
                            def not_within_tables(obj):
                                ox0 = obj.get("x0", 0)
                                ox1 = obj.get("x1", 0)
                                otop = obj.get("top", 0)
                                obottom = obj.get("bottom", 0)
                                for bx0, btop, bx1, bbottom in table_bboxes:
                                    if (ox0 >= bx0 - 2 and ox1 <= bx1 + 2 and otop >= btop - 2 and obottom <= bbottom + 2):
                                        return False
                                return True
                            try:
                                filtered_page = pl_page.filter(not_within_tables)
                                non_table_text = filtered_page.extract_text() or ""
                            except Exception:
                                non_table_text = pl_page.extract_text() or ""
                        else:
                            non_table_text = pl_page.extract_text() or ""

                        if non_table_text.strip():
                            # If no tables were detected by pdfplumber on this page, run table heuristic
                            if not md_tables:
                                non_table_text = DocumentParser._apply_table_heuristic(non_table_text)
                            page_parts.append(non_table_text.strip())
                        if md_tables:
                            page_parts.extend(md_tables)

                    except Exception as page_exc:
                        # Tier 1 failed for this page — fall through to tier 3 for this page
                        logger.debug("pdfplumber failed for page %d: %s", page_num, page_exc)
                        fallback_text = reader.pages[page_num - 1].extract_text() or ""
                        if fallback_text.strip():
                            page_parts.append(fallback_text.strip())

                    if page_parts:
                        combined = "\n\n".join(page_parts)
                        pages.append(f"[Page {page_num}]\n{combined}")

                pdf_pl.close()

                if pages:
                    text = "\n\n".join(pages)
                    metadata = {"page_count": page_count, "source_format": "pdf", "extraction_method": "pdfplumber"}
                    logger.debug("PDF parsed (pdfplumber): %d pages, %d chars", page_count, len(text))
                    return text, metadata
                # If pdfplumber produced no output, fall through to tier 3
            except Exception as exc:
                logger.debug("pdfplumber extraction failed entirely: %s — falling back to pypdf", exc)

        # Tier 2 & 3: pypdf extract_text with lightweight table heuristic
        pages = []
        for page_num, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            if not page_text.strip():
                continue

            # Tier 2: Lightweight heuristic — detect lines with multiple whitespace-separated columns
            enhanced_text = DocumentParser._apply_table_heuristic(page_text)
            pages.append(f"[Page {page_num}]\n{enhanced_text.strip()}")

        text = "\n\n".join(pages)
        metadata = {
            "page_count": page_count,
            "source_format": "pdf",
            "extraction_method": "pypdf" + ("+heuristic" if text != "\n\n".join(
                f"[Page {i+1}]\n{(reader.pages[i].extract_text() or '').strip()}"
                for i in range(page_count) if (reader.pages[i].extract_text() or '').strip()
            ) else ""),
        }
        logger.debug("PDF parsed (pypdf): %d pages, %d chars", page_count, len(text))
        return text, metadata

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

    @staticmethod
    def _parse_docx(content: bytes) -> tuple[str, dict]:
        """Extract paragraphs and tables from a DOCX file in document body order."""
        try:
            from docx import Document as DocxDocument
            from docx.table import Table as DocxTable
            from docx.text.paragraph import Paragraph as DocxParagraph
            from docx.oxml.ns import qn
        except ImportError:
            raise RuntimeError("python-docx is required: pip install python-docx")

        doc = DocxDocument(io.BytesIO(content))

        # Iterate body elements in document order to interleave paragraphs and tables
        parts = []
        table_count = 0
        paragraph_count = 0

        for element in doc.element.body:
            tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag

            if tag == "p":
                # Paragraph element
                para = DocxParagraph(element, doc)
                text = para.text.strip()
                if text:
                    parts.append(text)
                    paragraph_count += 1

            elif tag == "tbl":
                # Table element — convert to Markdown table
                table = DocxTable(element, doc)
                table_count += 1
                md_lines = []
                for r_idx, row in enumerate(table.rows):
                    cells = [cell.text.strip().replace("|", "\\|") for cell in row.cells]
                    md_lines.append("| " + " | ".join(cells) + " |")
                    if r_idx == 0:
                        # Add Markdown header separator after first row
                        md_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
                if md_lines:
                    parts.append("\n".join(md_lines))

        text = "\n\n".join(parts)
        metadata = {
            "paragraph_count": paragraph_count,
            "table_count": table_count,
            "has_tables": table_count > 0,
            "source_format": "docx",
        }
        # Extract core properties if available
        try:
            cp = doc.core_properties
            if cp.title:
                metadata["title"] = cp.title
            if cp.author:
                metadata["author"] = cp.author
        except Exception:
            pass
        logger.debug("DOCX parsed: %d paragraphs, %d tables, %d chars", paragraph_count, table_count, len(text))
        return text, metadata

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
        h = hashlib.sha256(filename.encode() + content[:1024]).hexdigest()[:16]
        return f"doc_{h}"


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------

class TextChunker:
    """
    Splits a Document into overlapping text chunks.

    Strategy:
      1. Split text into paragraphs (double newline boundaries).
      2. Accumulate paragraphs into windows of approximately `chunk_size` chars.
      3. Apply `overlap` characters from the end of one chunk to the start
         of the next.

    This paragraph-aware approach preserves semantic boundaries better
    than pure character splitting.
    """

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 150) -> None:
        if chunk_overlap >= chunk_size:
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
            page = self._extract_page_hint(text)
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
        if not paragraphs:
            return []

        windows: List[str] = []
        current_parts: List[str] = []
        current_len = 0
        overlap_tail = ""

        for para in paragraphs:
            para_len = len(para)

            if current_len + para_len + 2 > self.chunk_size and current_parts:
                # Emit current window
                window = "\n\n".join(current_parts)
                if overlap_tail:
                    window = overlap_tail + "\n\n" + window
                windows.append(window)

                # Build overlap tail: tail characters of current window
                raw = "\n\n".join(current_parts)
                overlap_tail = raw[-self.chunk_overlap:] if len(raw) > self.chunk_overlap else raw

                current_parts = []
                current_len = 0

            current_parts.append(para)
            current_len += para_len + 2  # +2 for the separator

        # Flush the last window
        if current_parts:
            window = "\n\n".join(current_parts)
            if overlap_tail:
                window = overlap_tail + "\n\n" + window
            windows.append(window)

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
