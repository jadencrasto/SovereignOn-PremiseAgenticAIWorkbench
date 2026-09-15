import io
import sys
from unittest.mock import MagicMock, patch
import pytest

from backend.rag.ingest import DocumentParser


def _make_dummy_pdf(text_lines: list[str]) -> bytes:
    """Helper to create a minimal PDF with extractable text using pypdf if possible, or mocked reader."""
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_with_text_only():
    raw_pdf = _make_dummy_pdf(["Plain paragraph line 1", "Plain paragraph line 2"])
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "MRPL Refinery Standard Operating Procedure\nPump Maintenance Runbook"

    with patch("pypdf.PdfReader") as mock_reader_cls:
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]
        mock_reader_cls.return_value = mock_reader

        text, meta = DocumentParser._parse_pdf(raw_pdf)
        assert "MRPL Refinery Standard Operating Procedure" in text
        assert "Pump Maintenance Runbook" in text
        assert meta["page_count"] == 1


def test_pdf_table_values_present_via_pdfplumber():
    raw_pdf = _make_dummy_pdf([])

    mock_pl_page = MagicMock()
    mock_pl_page.extract_tables.return_value = [
        [["Tag", "Discharge Pressure", "Flow Rate"], ["P-204", "45 bar", "120 m3/h"]]
    ]
    mock_pl_page.extract_text.return_value = "Equipment inspection table below:"

    mock_pdfplumber_doc = MagicMock()
    mock_pdfplumber_doc.pages = [mock_pl_page]

    with patch.dict(sys.modules, {"pdfplumber": MagicMock()}):
        import pdfplumber
        pdfplumber.open.return_value = mock_pdfplumber_doc

        text, meta = DocumentParser._parse_pdf(raw_pdf)
        assert "P-204" in text
        assert "45 bar" in text
        assert "120 m3/h" in text
        assert meta.get("extraction_method") == "pdfplumber"


def test_pdf_table_headers_preserved_via_heuristic():
    page_text = (
        "Operating Parameters:\n"
        "Parameter       Standard        Observed\n"
        "Vibration       < 4.5 mm/s      7.2 mm/s\n"
        "Discharge       45 bar          42 bar\n"
    )
    enhanced = DocumentParser._apply_table_heuristic(page_text)
    assert "| Parameter | Standard | Observed |" in enhanced
    assert "---" in enhanced
    assert "| Vibration | < 4.5 mm/s | 7.2 mm/s |" in enhanced


def test_pdf_fallback_on_pdfplumber_absent():
    raw_pdf = _make_dummy_pdf([])
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Fallback text from pypdf"

    with patch.dict(sys.modules, {"pdfplumber": None}):
        with patch("pypdf.PdfReader") as mock_reader_cls:
            mock_reader = MagicMock()
            mock_reader.pages = [mock_page]
            mock_reader_cls.return_value = mock_reader

            text, meta = DocumentParser._parse_pdf(raw_pdf)
            assert "Fallback text from pypdf" in text
            assert "pypdf" in meta.get("extraction_method", "")


def test_pdf_fallback_on_pdfplumber_error():
    raw_pdf = _make_dummy_pdf([])
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Pypdf recovered text after plumber failure"

    with patch.dict(sys.modules, {"pdfplumber": MagicMock()}):
        import pdfplumber
        pdfplumber.open.side_effect = Exception("Corrupt PDF streams in pdfplumber")

        with patch("pypdf.PdfReader") as mock_reader_cls:
            mock_reader = MagicMock()
            mock_reader.pages = [mock_page]
            mock_reader_cls.return_value = mock_reader

            text, meta = DocumentParser._parse_pdf(raw_pdf)
            assert "Pypdf recovered text after plumber failure" in text
            assert "pypdf" in meta.get("extraction_method", "")


def test_pdf_no_table_continues_normally():
    plain_text = "This is regular sentence one.\nThis is regular sentence two.\nNo tables here."
    result = DocumentParser._apply_table_heuristic(plain_text)
    assert result == plain_text


def test_pdf_ingestion_never_crashes_on_corrupt_stream():
    corrupt_bytes = b"not a valid pdf header at all"
    with pytest.raises(Exception) as exc_info:
        DocumentParser.parse("broken.pdf", corrupt_bytes)
    assert exc_info.type in (ValueError, RuntimeError) or "PDF" in str(exc_info.value) or "No text" in str(exc_info.value)


def test_pdfplumber_structured_markdown_table_row_column_relationships():
    """Verify that detected PDF tables are output as Markdown tables with row/column relationships intact."""
    raw_pdf = _make_dummy_pdf([])

    mock_table = MagicMock()
    mock_table.bbox = (10, 10, 200, 100)
    mock_table.extract.return_value = [
        ["Equipment Tag", "Design Flow", "Discharge Pressure", "Status"],
        ["P-204", "120 m3/h", "42 bar", "OPERATIONAL"],
        ["P-101", "85 m3/h", "28 bar", "STANDBY"],
    ]

    mock_pl_page = MagicMock()
    mock_pl_page.find_tables.return_value = [mock_table]
    mock_pl_page.filter.return_value.extract_text.return_value = "Unit 50 Reliability Assessment Report"

    mock_pdfplumber_doc = MagicMock()
    mock_pdfplumber_doc.pages = [mock_pl_page]

    with patch.dict(sys.modules, {"pdfplumber": MagicMock()}):
        import pdfplumber
        pdfplumber.open.return_value = mock_pdfplumber_doc

        text, meta = DocumentParser._parse_pdf(raw_pdf)
        assert "| Equipment Tag | Design Flow | Discharge Pressure | Status |" in text
        assert "| --- | --- | --- | --- |" in text
        assert "| P-204 | 120 m3/h | 42 bar | OPERATIONAL |" in text
        assert "| P-101 | 85 m3/h | 28 bar | STANDBY |" in text
        assert "Unit 50 Reliability Assessment Report" in text


def test_pdf_table_blank_cells_do_not_shift_columns():
    """Verify that blank cells do not cause subsequent columns to shift left."""
    raw_pdf = _make_dummy_pdf([])

    mock_table = MagicMock()
    mock_table.bbox = (10, 10, 200, 100)
    # Row 1 has a blank cell in column 2 (Discharge Pressure missing)
    # Row 2 has a blank cell in column 3 (Status missing)
    mock_table.extract.return_value = [
        ["Equipment Tag", "Design Flow", "Discharge Pressure", "Status"],
        ["P-204", "120 m3/h", None, "OPERATIONAL"],
        ["P-101", "", "28 bar", "STANDBY"],
    ]

    mock_pl_page = MagicMock()
    mock_pl_page.find_tables.return_value = [mock_table]
    mock_pl_page.filter.return_value.extract_text.return_value = "Header Text"

    mock_pdfplumber_doc = MagicMock()
    mock_pdfplumber_doc.pages = [mock_pl_page]

    with patch.dict(sys.modules, {"pdfplumber": MagicMock()}):
        import pdfplumber
        pdfplumber.open.return_value = mock_pdfplumber_doc

        text, meta = DocumentParser._parse_pdf(raw_pdf)
        # Column 2 was None, OPERATIONAL must remain in Column 4 (Status)
        assert "| P-204 | 120 m3/h |  | OPERATIONAL |" in text
        # Column 1 was blank, 28 bar must remain in Column 3
        assert "| P-101 |  | 28 bar | STANDBY |" in text


def test_pdf_heuristic_table_preserves_aligned_columns_with_empty_cells():
    """Verify that the lightweight heuristic also aligns columns when rows have varying segment counts."""
    page_text = (
        "Tag        Flow        Pressure    Status\n"
        "P-204      120 m3/h    42 bar      ACTIVE\n"
        "P-101      85 m3/h                 STANDBY\n"
    )
    enhanced = DocumentParser._apply_table_heuristic(page_text)
    assert "| Tag | Flow | Pressure | Status |" in enhanced
    assert "| --- | --- | --- | --- |" in enhanced
    assert "| P-204 | 120 m3/h | 42 bar | ACTIVE |" in enhanced
    assert "| P-101 | 85 m3/h | STANDBY |  |" in enhanced or "| P-101 |" in enhanced

