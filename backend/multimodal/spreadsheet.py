"""Bounded, non-authorizing spreadsheet transformations for supplied bytes.

No routes, file access, workbook calculation or artifact generation are provided.
"""
from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from statistics import mean
from sys import float_info
from typing import Any
from uuid import uuid4

from backend.multimodal.document_evidence import (
    ExtractedCell, ExtractionBudget, ExtractionLimit, ExtractionWarning, SourceLocation, header_unit,
    valid_source_filename,
)
from backend.multimodal.openxml_limits import validate_openxml

MAX_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_BYTES = 30 * 1024 * 1024
MAX_CELLS = 20_000
MAX_SHEETS = 20


@dataclass(frozen=True)
class Sheet:
    name: str
    rows: tuple[tuple[Any, ...], ...]
    cells: tuple[ExtractedCell, ...] = field(default=(), compare=False, repr=False)

    def header_units(self, header_row: int) -> tuple[str | None, ...]:
        if type(header_row) is not int or not 1 <= header_row <= len(self.rows):
            raise ValueError('Select an existing one-based header row.')
        return tuple(header_unit(value) if isinstance(value, str) else None for value in self.rows[header_row - 1])


@dataclass(frozen=True)
class Spreadsheet:
    filename: str
    sheets: tuple[Sheet, ...]
    source_id: str = field(default_factory=lambda: uuid4().hex, compare=False)
    format: str = 'xlsx'
    warnings: tuple[ExtractionWarning, ...] = ()

    def worksheet(self, name: str) -> Sheet:
        for sheet in self.sheets:
            if sheet.name == name:
                return sheet
        raise ValueError('Select an exact worksheet name from this workbook.')


def csv_rows(text: str):
    """Read comma-delimited records with newline preservation inside quoted cells."""
    with io.StringIO(text, newline='') as source:
        yield from csv.reader(source, strict=True)


def parse_spreadsheet(filename: str, data: bytes) -> Spreadsheet:
    """Read synthetic CSV/XLSX bytes; formula cells remain unevaluated strings."""
    if not valid_source_filename(filename):
        raise ValueError('Invalid spreadsheet filename')
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError('Empty or oversized spreadsheet')
    if filename.lower().endswith('.csv'):
        if b'\x00' in data or data.startswith((b'PK\x03\x04', b'%PDF-')):
            raise ValueError('Invalid CSV content')
        try:
            rows = []
            cells = 0
            for row in csv_rows(data.decode('utf-8-sig')):
                cells += len(row)
                if cells > MAX_CELLS or len(rows) >= MAX_CELLS:
                    raise ValueError('Spreadsheet cell limit exceeded')
                rows.append(tuple(None if value == '' else value for value in row))
        except (UnicodeError, csv.Error) as exc:
            raise ValueError('Invalid CSV content') from exc
        if not rows: raise ValueError('Empty spreadsheet')
        return Spreadsheet(filename, (Sheet('CSV', tuple(rows)),), format='csv')
    if not filename.lower().endswith('.xlsx'):
        raise ValueError('Unsupported spreadsheet format')
    budget = ExtractionBudget(5, 2 * 1024 * 1024)
    try:
        validate_openxml(data, 'xl/workbook.xml', MAX_EXPANDED_BYTES, budget, sheet_limit=MAX_SHEETS)
    except ExtractionLimit:
        raise
    except Exception:
        raise ValueError('Unsupported or oversized workbook') from None
    try:
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
        try:
            if not 1 <= len(workbook.worksheets) <= MAX_SHEETS:
                raise ValueError('Workbook sheet limit exceeded')
            sheets, warnings = [], []
            count = 0
            for ws in workbook.worksheets:
                if ws.max_row is not None and ws.max_column is not None and ws.max_row * ws.max_column + count > MAX_CELLS:
                    raise ValueError('Spreadsheet cell limit exceeded')
                # Worksheet dimensions can be absent or understate the actual data.
                # Read observed cells and enforce the budget before rectangular padding.
                ws.reset_dimensions()
                rows = []
                evidence = []
                width = 0
                for row_number, cells in enumerate(ws.iter_rows(), 1):
                    budget.check()
                    width = max(width, len(cells))
                    if count + (len(rows) + 1) * max(1, width) > MAX_CELLS:
                        raise ValueError('Spreadsheet cell limit exceeded')
                    rows.append(tuple(cell.value for cell in cells))
                    for column, cell in enumerate(cells, 1):
                        if cell.value is None:
                            continue
                        if (cell.row, cell.column) != (row_number, column):
                            raise ValueError('Worksheet cell coordinate could not be verified.')
                        location = SourceLocation(sheet=ws.title, row=row_number, column=column, cell=cell.coordinate)
                        value = cell.value
                        if cell.data_type == 'f' and not isinstance(value, str):
                            value = getattr(value, 'text', None)
                            if not isinstance(value, str):
                                value = None
                                warnings.append(ExtractionWarning('XLSX_FORMULA_TEXT_UNAVAILABLE', location))
                        if value is not None and not isinstance(value, (str, int, float, bool, date, time, timedelta)):
                            raise ValueError('Unsupported worksheet cell type.')
                        if type(value) in (int, float) and not _finite_number(value):
                            raise ValueError('Worksheet cell value is outside the finite numeric range.')
                        if isinstance(value, str):
                            budget.consume(value)
                        number_format = cell.number_format
                        if number_format:
                            budget.consume(number_format)
                        evidence.append(ExtractedCell(location, value, cell.data_type, number_format))
                count += len(rows) * max(1, width)
                sheets.append(Sheet(ws.title, tuple(row + (None,) * (width - len(row)) for row in rows), tuple(evidence)))
            budget.check()
            return Spreadsheet(filename, tuple(sheets), warnings=tuple(warnings))
        finally:
            workbook.close()
    except ValueError:
        raise
    except Exception:
        raise ValueError('Malformed spreadsheet') from None


def numeric_summary(sheet: Sheet, column: int, *, skip_header: bool = True) -> dict:
    """Summarize actual XLSX numeric cells; do not evaluate formulas or coerce CSV text."""
    if not isinstance(column, int) or isinstance(column, bool) or column < 0:
        raise ValueError('Invalid column')
    rows=sheet.rows[1:] if skip_header else sheet.rows
    values=[]; missing=0; nonnumeric=0
    for row in rows:
        value=row[column] if column < len(row) else None
        if value is None or value == '': missing+=1
        elif _finite_number(value): values.append(value)
        else: nonnumeric+=1
    return {'count':len(values),'missing':missing,'nonnumeric':nonnumeric,
            'mean':float(mean(values)) if values else None,'max':max(values) if values else None}


def _finite_number(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) <= float_info.max
    return isinstance(value, float) and math.isfinite(value)
