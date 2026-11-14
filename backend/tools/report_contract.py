"""Bounded, non-authorizing report inputs for existing document tools.

Source maps are supplied by a future trusted authorization boundary. This
module does not establish source ownership, freshness or publication rights.
"""
from __future__ import annotations

import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.tools.docx_create import DocxCreateInput
from backend.tools.xlsx_report import XlsxReportInput


class ReportSection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    heading: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4000)
    kind: Literal['source_supported', 'model_interpretation']
    citations: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def validate_sources(self):
        if self.kind == 'source_supported' and not self.citations:
            raise ValueError('Source-supported sections require a citation')
        return self


class ReportTable(BaseModel):
    model_config = ConfigDict(extra='forbid')
    headers: list[str] = Field(min_length=1, max_length=20)
    rows: list[list[str | int | float | None]] = Field(max_length=500)

    @model_validator(mode='after')
    def validate_table(self):
        if any(not h.strip() or len(h) > 200 for h in self.headers):
            raise ValueError('Invalid table header')
        for row in self.rows:
            if len(row) != len(self.headers): raise ValueError('Invalid table dimensions')
            for value in row:
                if isinstance(value, str) and len(value) > 1000: raise ValueError('Cell too long')
                if isinstance(value, float) and not math.isfinite(value): raise ValueError('Invalid number')
        return self


class ReportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    output_format: Literal['docx', 'xlsx', 'pdf']
    sections: list[ReportSection] = Field(default_factory=list, max_length=20)
    tables: list[ReportTable] = Field(default_factory=list, max_length=5)
    dependencies: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def validate_report(self):
        if not self.title.strip() or not (self.sections or self.tables): raise ValueError('Incomplete report')
        if self.output_format == 'xlsx' and len(self.tables) != 1:
            raise ValueError('Spreadsheet report requires exactly one table')
        if len(set(self.dependencies)) != len(self.dependencies): raise ValueError('Duplicate dependencies')
        return self


def tool_arguments(report: ReportInput, source_filenames: dict[str, str]):
    """Construct existing tool arguments; no file creation or authorization.

    Source IDs and filenames must come from a separately authorized scope; this
    module merely checks consistency against the supplied mapping.
    """
    if not isinstance(report, ReportInput) or not isinstance(source_filenames, dict):
        raise ValueError('Invalid report context')
    report = ReportInput.model_validate(report.model_dump())
    refs = {citation for section in report.sections for citation in section.citations}
    if any(not isinstance(key, str) or not key or not isinstance(value, str) or not value.strip()
           or len(value) > 255 or '/' in value or '\\' in value for key, value in source_filenames.items()):
        raise ValueError('Invalid source records')
    if not refs.issubset(source_filenames) or not refs.issubset(report.dependencies):
        raise ValueError('Unknown source reference or dependency')
    if not set(report.dependencies).issubset(source_filenames):
        raise ValueError('Unknown dependency')
    if report.output_format in ('docx', 'pdf'):
        sections = [{'heading': s.heading, 'body': f'[{"source-attributed, not independently verified" if s.kind == "source_supported" else "model interpretation"}] {s.text}' +
                    ((' (' + ', '.join(s.citations) + ')') if s.citations else '')} for s in report.sections]
        if refs:
            sections.append({'heading':'Sources', 'body':'\n'.join(f'{key}: {source_filenames[key]}' for key in sorted(refs))})
        if report.output_format == 'pdf':
            raise ValueError('PDF requires the isolated PDF renderer')
        return DocxCreateInput(filename='report.docx', title=report.title, sections=sections,
                               tables=[{'headers': t.headers, 'rows': t.rows} for t in report.tables])
    table = report.tables[0]
    notes = ' | '.join(f'{"source-attributed, not independently verified" if s.kind == "source_supported" else "model interpretation"}: {s.heading}: {s.text}' for s in report.sections)
    if refs: notes += ' | Sources: ' + ', '.join(f'{key}: {source_filenames[key]}' for key in sorted(refs))
    return XlsxReportInput(filename='report.xlsx', title=report.title, headers=table.headers,
                           rows=table.rows, summary_notes=notes)
