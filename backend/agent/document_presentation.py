"""Project verified offline document findings while their evidence scope is active."""

from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import math

from backend.multimodal.document_reasoning import ComparisonResult, ReasoningError, TableResult


def display_number(value, unit=None):
    if isinstance(value, Fraction):
        remainder = value.denominator
        for factor in (2, 5):
            while remainder % factor == 0:
                remainder //= factor
        if remainder == 1:
            with localcontext() as context:
                context.prec = 700
                value = Decimal(value.numerator) / Decimal(value.denominator)
    number = f'{value:f}' if isinstance(value, Decimal) else str(value)
    return f'{number} {unit}' if unit else number


def _invalid():
    return ReasoningError('CITATION_UNVERIFIED', 'The document evidence could not be presented safely.')


def _location(format_name, location, *, warning=False):
    fields = {key: value for key, value in asdict(location).items() if value is not None}
    pos = {key for key in fields if key not in {'sheet', 'cell'}}
    if any(type(fields[key]) is not int or fields[key] < 1 for key in pos):
        raise _invalid()

    keys = set(fields)
    table_cell = {'table', 'row', 'column'}
    if format_name == 'pdf':
        allowed = ({'page'}, {'page'} | table_cell)
        if warning:
            allowed += ({'page', 'table'},)
        if keys not in allowed:
            raise _invalid()
    elif format_name == 'docx':
        allowed = ({'paragraph'}, table_cell)
        if warning:
            allowed += ({'table'},)
        if keys - {'body'} not in allowed:
            raise _invalid()
    elif format_name == 'xlsx':
        if (keys != {'sheet', 'cell', 'row', 'column'} or not isinstance(location.sheet, str)
                or not 1 <= len(location.sheet) <= 31 or not 1 <= location.column <= 16384
                or not 1 <= location.row <= 1048576):
            raise _invalid()
        letters, column = '', location.column
        while column:
            column, digit = divmod(column - 1, 26)
            letters = chr(65 + digit) + letters
        if location.cell != f'{letters}{location.row}':
            raise _invalid()
    else:
        raise _invalid()
    return fields


def _value(citation):
    value = citation.value
    if citation.data_type == 'f':
        raise _invalid()
    if type(value) is bool:
        return 'boolean', 'true' if value else 'false'
    if type(value) is int or (type(value) is float and math.isfinite(value)):
        return 'number', display_number(value)
    if isinstance(value, (datetime, date, time)):
        return 'date_time', value.isoformat()
    if isinstance(value, timedelta):
        return 'duration', str(value)
    if isinstance(value, str):
        return ('cell_error' if citation.data_type == 'e' else 'text'), value
    raise _invalid()


def _citation(source, citation):
    if source is None or citation.filename != source.filename:
        raise _invalid()
    location = _location(source.format, citation.location)
    kind, value = _value(citation)
    result = {'source_id': source.source_id, 'location': location, 'value_type': kind, 'value': value}
    if citation.heading_level is not None:
        if (source.format != 'docx' or 'paragraph' not in location or type(citation.heading_level) is not int
                or not 1 <= citation.heading_level <= 9):
            raise _invalid()
        result['heading_level'] = citation.heading_level
    if citation.number_format is not None and source.format == 'xlsx':
        result['number_format'] = citation.number_format
    return result


def _finding(result, evidence, citations):
    if isinstance(result, TableResult):
        calculation = result.calculation
        if calculation is None or calculation.value is None:
            raise _invalid()
        return {'kind': 'calculation', 'evidence': evidence, 'operation': result.operation,
                'value': display_number(calculation.value), 'unit': calculation.unit,
                'approximate': calculation.approximate, 'excluded': {
                    'missing': calculation.missing, 'nonnumeric': calculation.nonnumeric,
                    'formulas': calculation.formulas}}
    if isinstance(result, ComparisonResult):
        positions = {id(cite): index for index, cite in enumerate(citations)}
        rows = []
        for row in result.rows:
            if row.difference is None or len(row.citations) != 4 or row.citations[-2].source_id == row.citations[-1].source_id:
                raise _invalid()
            rows.append({'record': row.identifier, 'difference': display_number(row.difference),
                         'operands': [positions[id(cite)] for cite in row.citations[-2:]]})
        return {'kind': 'comparison', 'evidence': evidence, 'unit': result.unit, 'rows': rows}
    return {'kind': 'passage', 'evidence': evidence}


def present_answer(scope, answer):
    source_by_id = {source.source_id: source for source in scope.sources()}
    sources = {}
    findings = []
    shown = set()
    for finding in answer.findings:
        citations = finding.citations()
        scope.verify_citations(*citations)
        shown.update(id(cite) for cite in citations)
        evidence = []
        for cite in citations:
            source = source_by_id.get(cite.source_id)
            evidence.append(_citation(source, cite))
            if source.source_id not in sources:
                sources[source.source_id] = {'source_id': source.source_id, 'filename': source.filename,
                                             'format': source.format, 'warnings': [
                    {'code': warning.code, 'location': _location(source.format, warning.location, warning=True)}
                    for warning in source.warnings
                ]}
        findings.append(_finding(finding.result, evidence, citations))
    if shown != {id(cite) for cite in answer.citations}:
        raise _invalid()
    scope.verify_citations(*answer.citations)
    return {'text': answer.text, 'sources': list(sources.values()), 'findings': findings}
