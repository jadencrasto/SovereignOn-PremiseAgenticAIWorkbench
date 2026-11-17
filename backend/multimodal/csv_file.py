"""Bounded CSV preview and selected rows for transient local analysis."""
import csv
import re

from backend.multimodal.spreadsheet import csv_rows
from backend.multimodal.text_file import TextFileError, decode_text_file

MAX_CSV_ROWS = 32
MAX_CSV_COLUMNS = 8
MAX_CSV_CELL_CHARS = 128
MAX_CSV_SELECTION = 12
MAX_CSV_CONTEXT_BYTES = 4096
_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?')


def is_csv_number(value: str) -> bool:
    return _NUMBER.fullmatch(value) is not None


def parse_csv_file(data: bytes, media_type: str) -> tuple[list[str], list[list[str]]]:
    kind, separator, params = media_type.partition(';')
    if kind.strip().lower() not in ('text/csv', 'text/plain', 'application/octet-stream', 'application/vnd.ms-excel'):
        raise TextFileError('Use a UTF-8 CSV file.', status=415, code='CSV_FILE_TYPE')
    text = decode_text_file(data, 'text/plain' + separator + params)
    records = csv_rows(text)
    try:
        raw_header = next(records, [])
        header = [cell.strip() for cell in raw_header]
        if (not 1 <= len(header) <= MAX_CSV_COLUMNS
                or any(not cell for cell in header)
                or any(len(cell) > MAX_CSV_CELL_CHARS or any(c in cell for c in '\r\n\t') for cell in raw_header)
                or len({cell.casefold() for cell in header}) != len(header)):
            raise TextFileError('Use 1 to 8 unique, non-empty single-line CSV headers of up to 128 characters.',
                                code='CSV_HEADER_INVALID')
        rows = []
        for row in records:
            if len(row) != len(header):
                raise TextFileError('Every CSV row must match the header width.', code='CSV_ROW_WIDTH')
            if len(rows) == MAX_CSV_ROWS or any(len(cell) > MAX_CSV_CELL_CHARS for cell in row):
                raise TextFileError('CSV supports up to 32 rows and 128 characters per cell.', code='CSV_LIMIT')
            rows.append(row)
        if not rows:
            raise TextFileError('Add at least one CSV data row.', code='CSV_EMPTY')
        return header, rows
    except csv.Error:
        raise TextFileError('The CSV quoting or record structure is invalid.', code='CSV_INVALID') from None
    finally:
        records.close()


def select_csv_rows(rows: list[list[str]], start: str | None, end: str | None, *, preview: bool = False):
    if preview and start is None and end is None:
        return rows[:MAX_CSV_SELECTION], len(rows) > MAX_CSV_SELECTION
    if (start is not None and end is not None
            and re.fullmatch(r'[1-9][0-9]?', start) and re.fullmatch(r'[1-9][0-9]?', end)):
        first, last = int(start), int(end)
        if first <= last <= len(rows) and last - first < MAX_CSV_SELECTION:
            return rows[first - 1:last], False
    raise TextFileError('Select an inclusive range of 1 to 12 data rows.', code='CSV_RANGE_INVALID')


def csv_column_types(rows: list[list[str]]) -> list[str]:
    """Describe nonempty cells without normalizing, coercing or evaluating them."""
    types = []
    for column in zip(*rows):
        values = [value.strip() for value in column if value.strip()]
        if not values:
            types.append('Empty')
        elif all(is_csv_number(value) for value in values):
            types.append('Number')
        elif all(value.lower() in ('true', 'false') for value in values):
            types.append('Boolean')
        else:
            types.append('Text')
    return types
