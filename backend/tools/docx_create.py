"""
backend/tools/docx_create.py
------------------------------
Dedicated Microsoft Word (.docx) OOXML artifact generation tool.

Security & Integrity:
- Generates genuine ZIP-based OOXML .docx files using python-docx
- Writes strictly within data/sandbox/
- Atomic file generation via temporary files and os.replace()
- Path traversal and filename sanitization via backend.tools.safety
- Computes cryptographic SHA-256 hash of the generated artifact
- Mutating & high risk: requires human confirmation and planning approval
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.tools.safety import sanitize_filename, validate_path_within

logger = logging.getLogger(__name__)

_DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class DocxCreateInput(BaseModel):
    """Input schema for creating genuine Word (.docx) documents."""
    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Target document filename ending in .docx, e.g. 'P-204_Maintenance_Summary.docx'.",
    )
    title: Optional[str] = Field(
        default="",
        description="Document title or top-level heading.",
    )
    content: Optional[str] = Field(
        default="",
        description="Document body text or markdown formatted content.",
    )
    paragraphs: Optional[List[str]] = Field(
        default=None,
        description="Optional list of paragraph strings to add to the document.",
    )
    sections: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Optional structured sections (e.g. [{'heading': '...', 'body': '...'}]).",
    )
    tables: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Optional tables to include (e.g. [{'headers': [...], 'rows': [[...]]}]).",
    )
    overwrite: bool = Field(
        default=True,
        description="Whether to overwrite existing file if present.",
    )


def _is_markdown_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _extract_markdown_tables(text: str) -> List[Dict[str, Any]]:
    import re
    tables = []
    lines = text.splitlines()
    in_table = False
    cur_headers: List[str] = []
    cur_rows: List[List[str]] = []
    for line in lines:
        stripped = line.strip()
        if _is_markdown_table_line(stripped):
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            # Check if separator row like |---|---|
            if all(re.match(r"^:?-+:?$", c) for c in cells if c):
                in_table = True
                continue
            if not in_table:
                cur_headers = cells
            else:
                cur_rows.append(cells)
        else:
            if in_table and cur_headers and cur_rows:
                tables.append({"headers": cur_headers, "rows": cur_rows})
            in_table = False
            cur_headers = []
            cur_rows = []
    if in_table and cur_headers and cur_rows:
        tables.append({"headers": cur_headers, "rows": cur_rows})
    return tables


def create_docx_create(sandbox_dir: Path) -> callable:
    """
    Create the docx_create execution function bound to sandbox_dir.
    """
    sandbox_resolved = sandbox_dir.resolve()
    sandbox_resolved.mkdir(parents=True, exist_ok=True)

    async def execute_docx_create(args: DocxCreateInput) -> Dict[str, Any]:
        # 1. Sanitize and validate filename
        safe_name = sanitize_filename(args.filename)
        if not safe_name.lower().endswith(".docx"):
            safe_name += ".docx"

        target_path = validate_path_within(safe_name, sandbox_resolved)

        if target_path.exists() and not args.overwrite:
            raise ValueError(f"File '{safe_name}' already exists. Set overwrite=True to replace.")

        # 2. Build Document using python-docx
        try:
            import docx
            from docx.shared import Inches, Pt, RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH

            doc = docx.Document()

            # Ensure document has a clear title
            doc_title = None
            if args.title and args.title.strip():
                doc_title = args.title.strip()
            elif args.content and args.content.strip():
                first_line = args.content.strip().splitlines()[0].strip()
                if first_line.startswith("# ") and len(first_line) > 2:
                    doc_title = first_line[2:].strip()
            if not doc_title:
                clean_stem = Path(safe_name).stem.replace("_", " ").strip()
                doc_title = clean_stem.title() if clean_stem else "Document Summary"

            h0 = doc.add_heading(doc_title, level=0)
            h0.paragraph_format.space_after = Pt(12)

            # Pre-process content: split lines that pack multiple training fields onto one line
            raw_content = (args.content or "").strip()
            content_lines: List[str] = []
            if raw_content:
                import re
                field_split_pattern = re.compile(
                    r'(?:,\s*|\.\s+|\s{2,})(?=(?:Training(?:\s+Program|\s+Name)?|Date|Training\s+Date|Trainer|Duration|Participants|Number\s+of\s+Participants|Topics(?:\s+Covered)?)\s*:)',
                    re.IGNORECASE
                )
                for rl in raw_content.splitlines():
                    trimmed_rl = rl.strip()
                    if not trimmed_rl or _is_markdown_table_line(trimmed_rl):
                        continue
                    # Skip if line was already used as title
                    if doc_title and trimmed_rl in (f"# {doc_title}", doc_title):
                        continue
                    parts = field_split_pattern.split(trimmed_rl)
                    for pt in parts:
                        if pt.strip() and not _is_markdown_table_line(pt.strip()):
                            content_lines.append(pt.strip())

            # Add explicit paragraphs if provided
            if args.paragraphs:
                for p_text in args.paragraphs:
                    if p_text and p_text.strip():
                        content_lines.append(p_text.strip())

            # Render content lines with structured paragraphs and headings
            if content_lines:
                current_p_lines: List[str] = []

                def flush_p():
                    if current_p_lines:
                        p = doc.add_paragraph(" ".join(current_p_lines).strip())
                        p.paragraph_format.space_after = Pt(4)
                        current_p_lines.clear()

                in_topics_section = False
                for trimmed in content_lines:
                    if not trimmed:
                        flush_p()
                        continue

                    # Markdown headings
                    if trimmed.startswith("### "):
                        flush_p()
                        in_topics_section = False
                        h = doc.add_heading(trimmed[4:].strip(), level=3)
                        h.paragraph_format.space_after = Pt(4)
                    elif trimmed.startswith("## "):
                        flush_p()
                        in_topics_section = False
                        h = doc.add_heading(trimmed[3:].strip(), level=2)
                        h.paragraph_format.space_after = Pt(6)
                    elif trimmed.startswith("# "):
                        flush_p()
                        in_topics_section = False
                        h = doc.add_heading(trimmed[2:].strip(), level=1)
                        h.paragraph_format.space_after = Pt(8)
                    elif trimmed.startswith("- ") or trimmed.startswith("* ") or trimmed.startswith("• "):
                        flush_p()
                        bullet_text = re.sub(r"^[-*•]\s*", "", trimmed).strip()
                        bp = doc.add_paragraph(bullet_text, style="List Bullet")
                        bp.paragraph_format.space_after = Pt(2)
                    elif trimmed.lower().startswith("topics covered") or trimmed.lower().startswith("topics:"):
                        flush_p()
                        in_topics_section = True
                        h_text = "Topics Covered"
                        val_part = ""
                        if ":" in trimmed:
                            h_cand, _, val_cand = trimmed.partition(":")
                            h_text = h_cand.strip() or "Topics Covered"
                            val_part = val_cand.strip()
                        h = doc.add_heading(h_text, level=2)
                        h.paragraph_format.space_before = Pt(8)
                        h.paragraph_format.space_after = Pt(4)
                        if val_part:
                            # Split comma/semicolon/newline-separated topics into separate bullet points
                            topic_items = [t.strip().lstrip("-*• ") for t in re.split(r"[,;\n]", val_part) if t.strip()]
                            for item in topic_items:
                                bp = doc.add_paragraph(item, style="List Bullet")
                                bp.paragraph_format.space_after = Pt(2)
                    elif in_topics_section and not (":" in trimmed and not trimmed.startswith(("http:", "https:"))):
                        # Continue topics list bullets under Topics Covered heading
                        flush_p()
                        clean_item = re.sub(r"^[-*•\d\.\)]\s*", "", trimmed).strip()
                        if clean_item:
                            bp = doc.add_paragraph(clean_item, style="List Bullet")
                            bp.paragraph_format.space_after = Pt(2)
                    elif ":" in trimmed and not trimmed.startswith(("http:", "https:")):
                        # Key: Value field (e.g. Training Program: Fire Safety Awareness)
                        flush_p()
                        in_topics_section = False
                        key, _, value = trimmed.partition(":")
                        key = key.strip()
                        value = value.strip()
                        p = doc.add_paragraph()
                        p.paragraph_format.space_after = Pt(4)
                        run_key = p.add_run(f"{key}: ")
                        run_key.bold = True
                        if value:
                            p.add_run(value)
                    else:
                        current_p_lines.append(trimmed)

                flush_p()

            # Add explicit structured sections if provided
            if args.sections:
                for sec in args.sections:
                    sec_heading = sec.get("heading")
                    sec_body = sec.get("body", "")
                    sec_level = sec.get("level", 1)
                    if sec_heading:
                        doc.add_heading(str(sec_heading), level=sec_level)
                    if sec_body:
                        doc.add_paragraph(str(sec_body))

            # Add tables (from args.tables or extracted from markdown content)
            tables_to_render: List[Dict[str, Any]] = []
            if args.tables:
                for tbl_spec in args.tables:
                    if isinstance(tbl_spec, dict):
                        tables_to_render.append(tbl_spec)

            has_populated_table = any(tbl.get("rows") for tbl in tables_to_render)
            if not has_populated_table and raw_content:
                md_tables = _extract_markdown_tables(raw_content)
                if md_tables:
                    tables_to_render.extend(md_tables)

            for tbl_spec in tables_to_render:
                headers = tbl_spec.get("headers", [])
                rows = tbl_spec.get("rows", [])
                if headers or rows:
                    num_cols = max(len(headers), max((len(r) for r in rows), default=0))
                    if num_cols > 0:
                        table = doc.add_table(rows=1 if headers else 0, cols=num_cols)
                        table.style = "Table Grid"

                        if headers:
                            hdr_cells = table.rows[0].cells
                            for i, h in enumerate(headers):
                                if i < num_cols:
                                    hdr_cells[i].text = str(h)
                                    for p in hdr_cells[i].paragraphs:
                                        p.paragraph_format.space_before = Pt(3)
                                        p.paragraph_format.space_after = Pt(3)
                                        for run in p.runs:
                                            run.bold = True

                        for r in rows:
                            row_cells = table.add_row().cells
                            for i, cell_val in enumerate(r):
                                if i < num_cols:
                                    row_cells[i].text = str(cell_val)
                                    for p in row_cells[i].paragraphs:
                                        p.paragraph_format.space_before = Pt(2)
                                        p.paragraph_format.space_after = Pt(2)

            # 3. Save to temporary file and replace atomically
            temp_path = target_path.with_suffix(".tmp.docx")
            doc.save(str(temp_path))

            os.replace(temp_path, target_path)

        except Exception as exc:
            logger.error("Docx generation failed: %s", exc)
            if "temp_path" in locals() and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            raise RuntimeError(f"Failed to generate DOCX document '{safe_name}': {exc}")

        # 4. Verify post-write integrity and compute SHA-256
        file_bytes = target_path.read_bytes()
        if len(file_bytes) == 0:
            raise RuntimeError(f"Generated DOCX document '{safe_name}' is empty on disk.")

        sha256_hash = hashlib.sha256(file_bytes).hexdigest()
        rel_path = f"data/sandbox/{safe_name}"

        logger.info(
            "docx_create | path=%s size=%d hash=%s",
            rel_path, len(file_bytes), sha256_hash[:16]
        )

        return {
            "created_path": rel_path,
            "filename": safe_name,
            "size_bytes": len(file_bytes),
            "sha256_hash": sha256_hash,
            "content_type": _DOCX_MIME_TYPE,
        }

    return execute_docx_create
