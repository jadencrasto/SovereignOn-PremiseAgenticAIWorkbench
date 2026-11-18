"""Request-local locations from byte parsers. These are not authorization or RAG metadata."""

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from time import monotonic
from uuid import uuid4

_EXPLICIT_UNITS = frozenset({
    '%', '°C', '°F', 'K', 'mm', 'cm', 'm', 'km', 'mm/s', 'm/s', 'm/s²',
    'Pa', 'kPa', 'MPa', 'bar', 'psi', 'g', 'kg', 'L', 'L/min', 'm³/h', 'm3/h',
    'rpm', 'Hz', 'W', 'kW', 'V', 'A', 's', 'ms', 'min', 'h',
})


class ExtractionLimit(ValueError):
    """An extraction stopped before publishing a partial evidence result."""


class ExtractionBudget:
    def __init__(self, seconds: float, max_chars: int):
        self._deadline = monotonic() + seconds
        self._remaining = max_chars

    def check(self):
        if monotonic() >= self._deadline:
            raise ExtractionLimit("Document extraction exceeded its time budget.")

    def consume(self, text: str):
        self.check()
        self._remaining -= len(text)
        if self._remaining < 0:
            raise ExtractionLimit("Extracted text exceeds the parser limit.")


@dataclass(frozen=True)
class SourceLocation:
    """One-based source positions. Missing coordinates are unknown or inapplicable."""

    page: int | None = None
    paragraph: int | None = None
    table: int | None = None
    row: int | None = None
    column: int | None = None
    sheet: str | None = None
    cell: str | None = None
    body: int | None = None


@dataclass(frozen=True)
class ExtractedBlock:
    location: SourceLocation
    text: str
    heading_level: int | None = None


@dataclass(frozen=True)
class ExtractedTable:
    location: SourceLocation
    rows: tuple[tuple[str | None, ...], ...]
    units: tuple[str | None, ...]

    def cell_location(self, row: int, column: int) -> SourceLocation:
        if not 1 <= row <= len(self.rows) or not 1 <= column <= len(self.rows[row - 1]):
            raise IndexError('Table position is outside the extracted grid.')
        return replace(self.location, row=row, column=column)


@dataclass(frozen=True)
class ExtractedCell:
    location: SourceLocation
    value: str | int | float | bool | date | datetime | time | timedelta | None
    data_type: str
    number_format: str | None = None


@dataclass(frozen=True)
class ExtractionWarning:
    code: str
    location: SourceLocation


@dataclass(frozen=True)
class DocumentEvidence:
    source_id: str
    filename: str
    format: str
    blocks: tuple[ExtractedBlock, ...]
    tables: tuple[ExtractedTable, ...]
    warnings: tuple[ExtractionWarning, ...]

    @classmethod
    def from_parts(cls, filename, format_name, blocks, tables, warnings):
        return cls(uuid4().hex, filename, format_name, tuple(blocks), tuple(tables), tuple(warnings))


def valid_source_filename(filename) -> bool:
    return (isinstance(filename, str) and bool(filename.strip()) and len(filename) <= 255
            and not any(char in '/\\:' or not char.isprintable() for char in filename))


def header_unit(text: str | None) -> str | None:
    """Report an explicitly printed parenthesized/bracketed column unit."""
    if not text:
        return None
    match = re.fullmatch(r'.+?\s*(?:\(([^()]{1,24})\)|\[([^\[\]]{1,24})\])', text)
    unit = (match[1] or match[2]).strip() if match else None
    return unit if unit in _EXPLICIT_UNITS else None
