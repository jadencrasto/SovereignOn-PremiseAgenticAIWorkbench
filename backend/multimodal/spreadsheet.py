"""Bounded, non-authorizing spreadsheet transformations for supplied bytes.

No routes, file access, workbook calculation or artifact generation are provided.
"""
from __future__ import annotations

import csv
import io
import math
import zipfile
from dataclasses import dataclass
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_BYTES = 30 * 1024 * 1024
MAX_CELLS = 20_000
MAX_SHEETS = 20


@dataclass(frozen=True)
class Sheet:
    name: str
    rows: tuple[tuple[Any, ...], ...]


@dataclass(frozen=True)
class Spreadsheet:
    filename: str
    sheets: tuple[Sheet, ...]


def csv_rows(text: str):
    """Read comma-delimited records with newline preservation inside quoted cells."""
    with io.StringIO(text, newline='') as source:
        yield from csv.reader(source, strict=True)


def parse_spreadsheet(filename: str, data: bytes) -> Spreadsheet:
    """Read synthetic CSV/XLSX bytes; formula cells remain unevaluated strings."""
    if not isinstance(filename, str) or not filename or '/' in filename or '\\' in filename:
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
        return Spreadsheet(filename, (Sheet('CSV', tuple(rows)),))
    if not filename.lower().endswith('.xlsx'):
        raise ValueError('Unsupported spreadsheet format')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            entries = archive.infolist()
            normalized = {name.casefold() for name in names}
            if (len(entries) > 10000 or sum(x.file_size for x in entries) > MAX_EXPANDED_BYTES
                or '[Content_Types].xml' not in names or 'xl/workbook.xml' not in names
                or len(normalized) != len(names)
                or any(name.endswith('vbaproject.bin')
                       or name.startswith(('xl/externallinks/', 'xl/macrosheets/', 'xl/intlmacrosheets/'))
                       for name in normalized)):
                raise ValueError('Unsupported or oversized workbook')
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
        try:
            if not 1 <= len(workbook.worksheets) <= MAX_SHEETS:
                raise ValueError('Workbook sheet limit exceeded')
            sheets = []
            count = 0
            for ws in workbook.worksheets:
                if ws.max_row is not None and ws.max_column is not None and ws.max_row * ws.max_column + count > MAX_CELLS:
                    raise ValueError('Spreadsheet cell limit exceeded')
                # Worksheet dimensions can be absent or understate the actual data.
                # Read observed cells and enforce the budget before rectangular padding.
                ws.reset_dimensions()
                rows = []
                width = 0
                for cells in ws.iter_rows():
                    width = max(width, len(cells))
                    if count + (len(rows) + 1) * max(1, width) > MAX_CELLS:
                        raise ValueError('Spreadsheet cell limit exceeded')
                    rows.append(tuple(cell.value for cell in cells))
                count += len(rows) * max(1, width)
                sheets.append(Sheet(ws.title, tuple(row + (None,) * (width - len(row)) for row in rows)))
            return Spreadsheet(filename, tuple(sheets))
        finally:
            workbook.close()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Malformed spreadsheet') from exc


def numeric_summary(sheet: Sheet, column: int, *, skip_header: bool = True) -> dict:
    """Summarize actual XLSX numeric cells; do not evaluate formulas or coerce CSV text."""
    if not isinstance(column, int) or isinstance(column, bool) or column < 0:
        raise ValueError('Invalid column')
    rows=sheet.rows[1:] if skip_header else sheet.rows
    values=[]; missing=0; nonnumeric=0
    for row in rows:
        value=row[column] if column < len(row) else None
        if value is None or value == '': missing+=1
        elif isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value): values.append(value)
        else: nonnumeric+=1
    return {'count':len(values),'missing':missing,'nonnumeric':nonnumeric,
            'mean':sum(values)/len(values) if values else None,'max':max(values) if values else None}
