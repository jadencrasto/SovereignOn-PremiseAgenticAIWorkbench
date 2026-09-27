"""
backend/tools/artifact_verifier.py
-----------------------------------
Post-generation verification tool for generated artifacts (DOCX, XLSX, CSV, Markdown, TXT).

Ensures deterministic correctness before concluding agent workflows:
1. Re-opens and parses generated file from disk.
2. For Word documents (.docx), validates genuine OOXML ZIP structure via python-docx.
3. Checks file integrity, non-zero size, and SHA-256 hash.
4. Validates required headers, schema consistency, and non-empty row/paragraph data.
5. Returns verified structured report to the agent memory and audit trail.
"""

from __future__ import annotations

import csv
import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.tools.safety import validate_path_within

logger = logging.getLogger(__name__)


class ArtifactVerifierInput(BaseModel):
    """Input schema for the artifact_verifier tool."""
    relative_path: Optional[str] = Field(
        default=None,
        description="Path to the artifact to verify, e.g. 'data/sandbox/P-204_Maintenance_Summary.docx' or filename.",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Alternative alias for relative_path.",
    )
    expected_columns: Optional[List[str]] = Field(
        default=None,
        description="Optional list of column names or headings that must exist in the artifact.",
    )
    expected_content: Optional[Any] = Field(
        default=None,
        description="Optional list of keywords or text strings that must appear in the artifact content.",
    )
    min_row_count: Optional[int] = Field(
        default=1,
        description="Minimum expected data rows or paragraphs.",
    )


def create_artifact_verifier(sandbox_dir: Path) -> callable:
    """Create the artifact verifier execution function."""

    async def execute_artifact_verifier(args: ArtifactVerifierInput) -> Dict[str, Any]:
        path_str = args.relative_path or args.file_path or ""
        if not path_str:
            raise ValueError("Artifact path not specified (relative_path or file_path is required).")

        # Strip path prefixes if provided
        clean_name = path_str.replace("data/sandbox/", "").replace("data\\sandbox\\", "").strip()
        target_path = validate_path_within(clean_name, sandbox_dir)

        if not target_path.exists():
            raise FileNotFoundError(f"Artifact not found on filesystem: {clean_name}")

        file_bytes = target_path.read_bytes()
        if len(file_bytes) == 0:
            raise ValueError(f"Artifact {clean_name} is empty (0 bytes).")

        sha256_hash = hashlib.sha256(file_bytes).hexdigest()
        suffix = target_path.suffix.lower()

        detected_headers: List[str] = []
        row_count = 0
        preview_rows: List[Any] = []
        doc_format = suffix.lstrip(".")
        extra_metadata: Dict[str, Any] = {}

        if suffix == ".docx":
            try:
                import docx

                doc = docx.Document(target_path)

                # Extract paragraphs
                paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
                paragraph_count = len(paragraphs)
                table_count = len(doc.tables)

                for p in doc.paragraphs:
                    style_name = p.style.name.lower() if p.style else ""
                    if "heading" in style_name or "title" in style_name:
                        txt = p.text.strip()
                        if txt and txt not in detected_headers:
                            detected_headers.append(txt)
                    for run in p.runs:
                        if run.bold and run.text.strip() and ":" in run.text:
                            label = run.text.strip().rstrip(":")
                            if label and label not in detected_headers:
                                detected_headers.append(label)

                # Inspect tables if present and collect all cell texts
                table_cell_texts: List[str] = []
                table_data_row_count = 0
                for tbl in doc.tables:
                    num_t_rows = len(tbl.rows)
                    if num_t_rows > 1:
                        table_data_row_count += (num_t_rows - 1)
                    for r_idx, row in enumerate(tbl.rows):
                        row_vals = [c.text.strip() for c in row.cells]
                        for c in row.cells:
                            c_txt = c.text.strip()
                            if c_txt:
                                table_cell_texts.append(c_txt)
                        if r_idx == 0:
                            for val in row_vals:
                                if val and val not in detected_headers:
                                    detected_headers.append(val)
                        if len(preview_rows) < 5:
                            preview_rows.append(row_vals)

                # If tables exist and expected_columns was specified, ensure table has populated data rows
                if len(doc.tables) > 0 and args.expected_columns and table_data_row_count == 0:
                    raise ValueError(
                        f"Artifact verification failed: Document '{clean_name}' contains a table with headers {detected_headers} but no data rows (table is empty)."
                    )

                # If no preview from tables, use paragraph preview
                if not preview_rows:
                    preview_rows = [[p] for p in paragraphs[:5]]

                row_count = max(paragraph_count, len(preview_rows), 1 if (paragraph_count > 0 or table_cell_texts) else 0)
                extra_metadata["paragraph_count"] = paragraph_count
                extra_metadata["table_count"] = table_count
                extra_metadata["table_data_row_count"] = table_data_row_count
                extra_metadata["table_cell_texts"] = table_cell_texts
                extra_metadata["preview_text"] = "\n".join(paragraphs[:3])

            except Exception as exc:
                raise ValueError(f"Corrupted or invalid DOCX document: {exc}")

        elif suffix == ".xlsx":
            try:
                import openpyxl
                wb = openpyxl.load_workbook(target_path, data_only=True)
                ws = wb.active
                if ws is None:
                    raise ValueError(f"Workbook '{clean_name}' contains no active worksheet.")

                # In styled xlsx_report, headers are in row 4; otherwise find row with multiple values
                header_row_idx = 4 if ws.max_row >= 4 and any(ws.cell(row=4, column=c).value is not None for c in range(1, min(ws.max_column + 1, 10))) else 1
                if header_row_idx != 4 or not any(ws.cell(row=4, column=c).value is not None for c in range(1, min(ws.max_column + 1, 10))):
                    best_row = 1
                    best_cnt = 0
                    for r in range(1, min(ws.max_row + 1, 11)):
                        cnt = sum(1 for c in range(1, ws.max_column + 1) if ws.cell(row=r, column=c).value is not None)
                        if cnt > best_cnt:
                            best_cnt = cnt
                            best_row = r
                    header_row_idx = best_row

                for col in range(1, ws.max_column + 1):
                    val = ws.cell(row=header_row_idx, column=col).value
                    if val is not None and str(val).strip():
                        detected_headers.append(str(val).strip())

                all_cell_texts: List[str] = []
                # Count data rows
                for r in range(header_row_idx + 1, ws.max_row + 1):
                    row_vals = [ws.cell(row=r, column=c).value for c in range(1, max(len(detected_headers), ws.max_column) + 1)]
                    if any(v is not None and str(v).strip() for v in row_vals):
                        row_count += 1
                        for v in row_vals:
                            if v is not None:
                                all_cell_texts.append(str(v))
                        if len(preview_rows) < 5:
                            preview_rows.append(row_vals)

                extra_metadata["column_count"] = len(detected_headers)
                extra_metadata["sheet_names"] = wb.sheetnames
                extra_metadata["all_cell_texts"] = all_cell_texts

                data_cells = [str(t).strip() for t in all_cell_texts if str(t).strip()]
                if not data_cells:
                    raise ValueError(f"Artifact verification failed: Workbook '{clean_name}' contains no populated data cells.")
                if all("not stated" in c.lower() or c.lower() in ("todo", "n/a", "none") for c in data_cells):
                    raise ValueError(f"Artifact verification failed: Workbook '{clean_name}' contains no grounded evidence (all data cells are unpopulated or 'Not stated').")
                # If headers request findings/observations/actions, ensure they are not all 'Not stated'
                detail_headers = [
                    idx for idx, h in enumerate(detected_headers)
                    if any(k in h.lower() for k in ("finding", "observation", "action", "recommend", "cause", "defect", "repair", "problem", "improvement", "issue", "solution"))
                ]
                if detail_headers and len(all_cell_texts) >= len(detected_headers):
                    detail_cells = []
                    for r_idx in range(row_count):
                        for col_idx in detail_headers:
                            cell_pos = r_idx * len(detected_headers) + col_idx
                            if cell_pos < len(all_cell_texts):
                                detail_cells.append(str(all_cell_texts[cell_pos]).strip())
                    if detail_cells and all("not stated" in c.lower() or c.lower() in ("todo", "n/a", "none", "") for c in detail_cells):
                        raise ValueError(
                            f"Artifact verification failed: Workbook '{clean_name}' contains no substantive evidence in maintenance data columns (all findings/observations/actions are 'Not stated')."
                        )
                wb.close()
            except Exception as exc:
                raise ValueError(f"Corrupted or invalid XLSX workbook: {exc}")

        elif suffix in (".csv", ".txt"):
            try:
                text_content = file_bytes.decode("utf-8")
                reader = csv.reader(text_content.splitlines())
                rows = list(reader)
                if rows:
                    detected_headers = rows[0]
                    data_rows = rows[1:]
                    row_count = len(data_rows)
                    preview_rows = data_rows[:5]
            except Exception as exc:
                raise ValueError(f"Failed to parse CSV artifact: {exc}")
        else:
            # Generic file
            row_count = 1
            detected_headers = ["file_content"]

        # Validate minimum row count
        if args.min_row_count is not None and row_count < args.min_row_count:
            raise ValueError(
                f"Artifact verification failed: found {row_count} rows/paragraphs, expected at least {args.min_row_count}."
            )

        # Validate expected columns
        missing_columns: List[str] = []
        if args.expected_columns:
            headers_lower = [h.lower() for h in detected_headers]
            for exp_col in args.expected_columns:
                s_col = str(exp_col).strip().lower()
                # Check directly in detected_headers
                if any(s_col in h or h in s_col for h in headers_lower):
                    continue
                # For DOCX documents, allow matching section titles, headings, or paragraph keys
                if suffix == ".docx":
                    docx_text_corpus = (" ".join(detected_headers) + " " + " ".join(paragraphs)).lower()
                    if s_col in docx_text_corpus:
                        continue
                missing_columns.append(exp_col)

            if missing_columns:
                raise ValueError(
                    f"Artifact verification failed: Missing required columns/headings: {missing_columns}. Found: {detected_headers}"
                )

        # Validate expected content
        if args.expected_content:
            corpus = ""
            if suffix == ".docx":
                corpus = " ".join(paragraphs) + " " + " ".join(extra_metadata.get("table_cell_texts", []))
            elif suffix == ".xlsx":
                corpus = " ".join(detected_headers) + " " + " ".join(extra_metadata.get("all_cell_texts", []))
            else:
                corpus = file_bytes.decode("utf-8", errors="ignore")

            corpus_lower = corpus.lower()
            missing_content = []

            items_to_check: List[str] = []

            def _extract_exp_items(val: Any):
                if isinstance(val, str):
                    items_to_check.append(val)
                elif isinstance(val, dict):
                    for k, v in val.items():
                        if str(k).lower() not in {"tables", "table", "headers", "rows", "columns", "content", "expected_content"}:
                            _extract_exp_items(k)
                        _extract_exp_items(v)
                elif isinstance(val, (list, tuple, set)):
                    for it in val:
                        _extract_exp_items(it)

            _extract_exp_items(args.expected_content)

            structural_terms = {"tables", "table", "headers", "header", "rows", "row", "columns", "column", "content", "expected_content", "title"}
            for exp in items_to_check:
                s_exp = str(exp).strip()
                s_lower = s_exp.lower()
                if not s_lower or s_lower in structural_terms:
                    continue
                # Skip generic placeholder labels if present
                if (
                    s_lower.endswith(" text")
                    or s_lower.startswith("text ")
                    or s_lower in ("text", "findings text", "observations text", "actions text", "placeholder", "todo", "sample", "example")
                ):
                    continue
                if s_lower not in corpus_lower:
                    missing_content.append(exp)
            if missing_content:
                raise ValueError(
                    f"Artifact verification failed: Required content not found in {clean_name}: {missing_content}"
                )

        logger.info(
            "artifact_verified | path=%s rows=%d headers=%s hash=%s",
            clean_name, row_count, detected_headers, sha256_hash[:16]
        )

        res = {
            "verified": True,
            "filename": clean_name,
            "file_size_bytes": len(file_bytes),
            "sha256_hash": sha256_hash,
            "detected_headers": detected_headers,
            "row_count": row_count,
            "sample_preview": preview_rows,
            "format": doc_format,
            "status": "PASSED_VERIFICATION",
        }
        res.update(extra_metadata)
        return res

    return execute_artifact_verifier
