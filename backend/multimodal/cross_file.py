"""Deterministic, request-local TXT limits and CSV or JSON measurements."""

import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from io import StringIO

from backend.multimodal.csv_file import MAX_CSV_CELL_CHARS, MAX_CSV_SELECTION
from backend.multimodal.file_analysis import (
    CsvWorkflow, CsvWorkflowError, CsvWorkflowStep, _Column, _ROW_IDENTIFIERS,
    _literal_value, _number, _numeric_values, _percentage, _words, literal_file_block,
)
from backend.multimodal.json_file import JsonTable
from backend.multimodal.text_file import TextFileError, TextSource, resolve_text_source
from backend.tools.unit_conversion import unit_scale_factors

_SOURCE = r'(?:"(?:[^"\r\n]+?\.(?:csv|json|txt)|source\s+[0-9]+)"|source\s+[0-9]+|[^"\r\n]+?\.(?:csv|json|txt))'
_NAMED = re.compile(rf'compare (?P<table>{_SOURCE}) (?:against|with) (?P<txt>{_SOURCE})[.?!]?', re.I)
_ROLES = re.compile(
    rf'use (?P<txt>{_SOURCE}) as (?:the )?limits?\s*,?\s+and '
    rf'(?P<table>{_SOURCE}) as (?:the )?(?:measurements|readings)[.?!]?', re.I,
)
_NAMED_LIMIT = re.compile(
    rf'use (?:the )?(?P<parameter>[a-z][a-z0-9]*(?:[ -][a-z0-9]+){{0,7}}) limit in (?P<txt>{_SOURCE}) '
    rf'to (?:identify|find|show) (?:the )?(?:equipment|records|readings|measurements) exceeding '
    rf'(?:the )?(?:threshold|limit) in (?P<table>{_SOURCE})[.?!]?', re.I,
)
_IMPLICIT = re.compile(
    r'(?:check whether (?:the )?(?P<check_format>csv|json) (?:values|readings|measurements) '
    r'(?:exceed|meet|violate) (?:the )?limits? in (?:the )?(?:txt|text) file'
    r'|find (?:the )?threshold in (?:the )?(?:txt|text) file and compare it with '
    r'(?:the )?(?:(?P<find_format>csv|json) |sensor )?(?:readings|measurements)'
    r'|compare (?:the )?(?P<compare_format>csv|json)(?: (?:file|readings|measurements))? '
    r'(?:against|with) (?:the )?(?:txt|text) file'
    r'|identify (?:the )?equipment violating (?:either|any|(?:the )?stated) limits?)'
    r'[.?!]?', re.I,
)


def is_cross_file_question(question: str) -> bool:
    question = question.strip()
    return any(pattern.fullmatch(question) for pattern in (_NAMED, _ROLES, _NAMED_LIMIT, _IMPLICIT))


@dataclass(frozen=True)
class CrossFileSources:
    limits: TextSource
    measurements: TextSource
    parameter: str | None = None

    def metadata(self) -> list[dict]:
        return [
            {'role': role, 'attachment_id': f'source-{source.number}', 'filename': source.filename,
             'mime_type': source.mime_type, 'size_bytes': source.size_bytes}
            for role, source in (('limits', self.limits), ('measurements', self.measurements))
        ]


def select_cross_file_sources(
    sources: list[TextSource], selector: str | None, question: str,
) -> CrossFileSources | None:
    pair = _NAMED.fullmatch(question) or _ROLES.fullmatch(question) or _NAMED_LIMIT.fullmatch(question)
    if pair:
        limits = resolve_text_source(sources, pair['txt'].strip('"'))
        measurements = resolve_text_source(sources, pair['table'].strip('"'))
    elif implicit := _IMPLICIT.fullmatch(question):
        requested_format = next((kind.casefold() for kind in implicit.groups() if kind is not None), None)
        texts = [source for source in sources if not source.tabular]
        tables = [source for source in sources if source.tabular
                  and (requested_format is None or source.filename.lower().endswith('.' + requested_format))]
        if not texts or not tables:
            raise TextFileError('Attach the TXT limits and requested CSV or JSON measurements.', code='TEXT_SOURCE_INVALID')
        if len(texts) != 1 or len(tables) != 1:
            raise TextFileError('Name both files uniquely or specify their source numbers and roles.',
                                code='TEXT_SOURCE_AMBIGUOUS')
        limits, measurements = texts[0], tables[0]
    else:
        return None
    if limits.tabular or not measurements.tabular:
        raise TextFileError('Choose TXT for limits and CSV or JSON for measurements.',
                            code='TEXT_SOURCE_INVALID')
    if selector is not None and resolve_text_source(sources, selector) is not measurements:
        raise TextFileError('Choose the measurements file as the source for this comparison.',
                            code='TEXT_SOURCE_CONFLICT')
    parameter = pair.groupdict().get('parameter') if pair else None
    return CrossFileSources(limits, measurements, parameter)


_PARAMETER = r'(?P<parameter>[a-z][a-z0-9]*(?:[ -][a-z0-9]+){0,7})'
_VALUE = r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?'
_UNIT = r'(?:%|[a-z°µμ][a-z0-9°µμ²³^*/-]{0,19})'
_QUANTITY = rf'(?P<value>{_VALUE})[ \t]*(?P<unit>{_UNIT})'
_PREFIX = re.compile(
    rf'(?P<direction>maximum|minimum)(?: (?:allowed|acceptable))? {_PARAMETER}\s*:\s*{_QUANTITY}\.?', re.I,
)
_OPERATING = re.compile(rf'operating {_PARAMETER} limit\s*:\s*{_QUANTITY}\.?', re.I)
_OPERATORS = {
    'shall not exceed': '<=', 'must not exceed': '<=', 'must be at most': '<=',
    'must be less than': '<', 'must be at least': '>=', 'must be greater than': '>',
    'must equal': '=',
}
_STATEMENT = re.compile(
    rf'{_PARAMETER} (?P<direction>{"|".join(_OPERATORS)})\s+{_QUANTITY}\.?', re.I,
)
_RANGE = re.compile(
    rf'{_PARAMETER} allowed range\s*:\s*(?P<low>{_VALUE})\s+to\s+{_QUANTITY}\.?', re.I,
)
_LIMIT_LIKE = re.compile(
    r'^(?:max(?:imum)?\b|min(?:imum)?\b|limit for\b|threshold for\b|operating\b[^:]*\blimit\b)'
    r'|^[\w -]+(?:\blimit\b|\ballowed range\b)\s*:'
    r'|^[\w -]+\b(?:shall not exceed|must not exceed|must be|must equal)\b', re.I,
)
_MAX_LIMITS = 4


@dataclass(frozen=True)
class _Limit:
    line: int
    text: str
    parameter: str
    operator: str
    value: Decimal
    unit: str
    low: Decimal | None = None


def _extract_limits(text: str) -> list[_Limit]:
    limits = {}
    with StringIO(text, newline='') as source:
        for line, raw in enumerate(source, 1):
            written = raw.rstrip('\r\n')
            statement = written.strip()
            low = None
            if match := _PREFIX.fullmatch(statement):
                operator = '<=' if match['direction'].lower() == 'maximum' else '>='
            elif match := _OPERATING.fullmatch(statement):
                operator = '<='
            elif match := _STATEMENT.fullmatch(statement):
                operator = _OPERATORS[match['direction'].lower()]
            elif match := _RANGE.fullmatch(statement):
                operator = 'range'
                low = _number(match['low']) if len(match['low']) <= MAX_CSV_CELL_CHARS else None
                if low is None:
                    raise CsvWorkflowError(f'TXT line {line} contains an invalid range.')
            elif _LIMIT_LIKE.search(re.sub(r'^(?:[-*•]|[0-9]+[.)])\s+', '', statement)):
                raise CsvWorkflowError(f'TXT line {line} needs an explicit numeric limit, direction and unit.')
            else:
                continue
            value = _number(match['value']) if len(match['value']) <= MAX_CSV_CELL_CHARS else None
            if value is None or (low is not None and low > value):
                raise CsvWorkflowError(f'TXT line {line} contains an invalid threshold or range.')
            key = _words(match['parameter'])
            if key in limits:
                raise CsvWorkflowError(f'TXT line {line} repeats or conflicts with another limit for the same parameter.')
            limits[key] = _Limit(line, written, match['parameter'], operator, value, match['unit'], low)
            if len(limits) > _MAX_LIMITS:
                raise CsvWorkflowError(f'Compare at most {_MAX_LIMITS} explicit limits in one request.')
    if not limits:
        raise CsvWorkflowError('The TXT file does not contain a supported explicit numeric limit with units.')
    return list(limits.values())


@dataclass(frozen=True)
class _Comparison:
    limit: _Limit
    column: _Column
    values: dict[int, Decimal]
    exclusions: str
    limit_scale: Decimal
    measurement_scale: Decimal


def _bind_measurements(
    limits: list[_Limit], columns: list[_Column], rows: list[list[str]], *, json_table: JsonTable | None = None,
) -> list[_Comparison]:
    comparisons = []
    for limit in limits:
        matches = [column for column in columns if _words(column.label) == _words(limit.parameter)]
        if len(matches) != 1:
            field_type = 'JSON field' if json_table is not None else 'CSV column'
            raise CsvWorkflowError(f'Match the parameter on TXT line {limit.line} to one {field_type} with the same name.')
        column = matches[0]
        location = (f'JSON field {chr(65 + column.index)}' if json_table is not None
                    else f'CSV column {chr(65 + column.index)}')
        if not column.unit:
            raise CsvWorkflowError(f'{location} needs an explicit measurement unit.')
        limit_scale = measurement_scale = Decimal(1)
        if limit.unit != column.unit:
            try:
                limit_scale, measurement_scale = unit_scale_factors(limit.unit, column.unit)
            except ValueError:
                raise CsvWorkflowError(f'The units on TXT line {limit.line} and {location} cannot be compared.') from None
        values, exclusions = _numeric_values(rows, range(len(rows)), column)
        if not values:
            raise CsvWorkflowError(f'{location} has no valid numeric readings. {exclusions}')
        comparisons.append(_Comparison(limit, column, values, exclusions, limit_scale, measurement_scale))
    return comparisons


def _violation(value: Decimal, operator: str, high: Decimal, low: Decimal | None) -> Decimal | None:
    if low is not None:
        return low - value if value < low else value - high if value > high else None
    if operator in ('<', '<='):
        return value - high if value > high or (operator == '<' and value == high) else None
    if operator in ('>', '>='):
        return high - value if value < high or (operator == '>' and value == high) else None
    return abs(value - high) if value != high else None


def _quantity(value: Decimal, scale: Decimal, unit: str) -> str:
    exact = Fraction(value) / Fraction(scale)
    converted = value / scale
    approximate = Fraction(converted) != exact
    if approximate:
        converted = converted.quantize(Decimal('0.000001'))
    return f'{"approximately " if approximate else ""}{converted:f} {unit}'


def _bound_text(high: str, low: str | None, operator: str) -> str:
    return f'{low} to {high} (inclusive)' if low is not None else f'{operator} {high}'


def _render(
    sources: CrossFileSources, comparisons: list[_Comparison], columns: list[_Column],
    rows: list[list[str]], first_row: int, json_table: JsonTable | None = None,
) -> str:
    txt, measurement = sources.limits, sources.measurements
    format_name = 'JSON' if json_table is not None else 'CSV'
    evidence = [f'File: {measurement.filename}', f'Source {txt.number} (TXT limits): {txt.filename}']
    for comparison in comparisons:
        limit = comparison.limit
        evidence.append(f'Line {limit.line}: {limit.text}')
    evidence.append(f'Source {measurement.number} ({format_name} measurements): {measurement.filename}')
    labels = [column for column in columns if _words(column.label) in _ROW_IDENTIFIERS]
    label = labels[0] if len(labels) == 1 else None
    if label is not None:
        evidence.append(json_table.field(label.index) if json_table else f'Column {chr(65 + label.index)}: {label.header}')
    results = ['**Verified threshold comparison.**']
    violating_rows = set()
    complete_rows = set(range(len(rows)))
    for comparison in comparisons:
        limit, column, values = comparison.limit, comparison.column, comparison.values
        unit = column.unit
        high = limit.value * comparison.limit_scale
        low = limit.low * comparison.limit_scale if limit.low is not None else None
        field = json_table.field(column.index) if json_table else f'CSV column {chr(65 + column.index)}'
        evidence.append(field if json_table else f'Column {chr(65 + column.index)}: {column.header}')
        original_bound = _bound_text(f'{limit.value:f} {limit.unit}',
                                     f'{limit.low:f} {limit.unit}' if limit.low is not None else None,
                                     limit.operator)
        evidence.append(f'TXT line {limit.line} applied as {original_bound}.')
        if limit.unit != unit:
            converted_bound = _bound_text(_quantity(high, comparison.measurement_scale, unit),
                                          _quantity(low, comparison.measurement_scale, unit) if low is not None else None,
                                          limit.operator)
            evidence.append(f'Converted threshold in {format_name} units: {converted_bound}.')
        violations = {}
        for offset, row in enumerate(rows):
            reading = row[column.index].strip()
            identity = f' ({_literal_value(row[label.index])})' if label is not None else ''
            if offset in values:
                deviation = _violation(values[offset] * comparison.measurement_scale, limit.operator, high, low)
                if deviation is not None:
                    violations[offset] = deviation
                status = ('violates by ' + _quantity(deviation, comparison.measurement_scale, unit)
                          if deviation is not None else 'meets the stated limit')
                outcome = f'{reading} {unit} - {status}'
            else:
                outcome = 'missing measurement' if not reading else 'invalid numeric measurement'
            number = first_row + offset
            if json_table:
                identity = f' ({json_table.evidence(number, label.index)})' if label is not None else ''
                evidence.append(f'{json_table.record(number)}{identity}: {json_table.evidence(number, column.index)} - {outcome}')
            else:
                evidence.append(f'Data row {number}{identity}: {outcome}')
        valid, failed = len(values), len(violations)
        violating_rows.update(violations)
        complete_rows.intersection_update(values)
        percentage = _percentage(Decimal(failed), Decimal(valid))
        location = 'JSON field' if json_table else field
        results.append(f'TXT line {limit.line} / {location}: {failed} of {valid} valid readings '
                       f'violate the limit ({percentage}). {valid - failed} meet the stated limit. {comparison.exclusions}')
        if failed:
            max_deviation = max(violations.values())
            start = first_row - (json_table is not None)
            tied = ', '.join(str(start + offset) for offset, value in violations.items() if value == max_deviation)
            positions = 'JSON record indices' if json_table else 'data rows'
            results.append(f'Maximum deviation: {_quantity(max_deviation, comparison.measurement_scale, unit)} '
                           f'({positions} {tied}).')
    if len(comparisons) > 1:
        inconclusive = len(rows) - len(violating_rows | complete_rows)
        selection = 'JSON records' if json_table else 'data rows'
        results.append(f'Selected {selection} across all limits: {len(violating_rows)} violating at least one limit, '
                       f'{len(complete_rows - violating_rows)} meeting every limit, {inconclusive} inconclusive.')
    return '\n'.join(results) + '\n\n' + literal_file_block(evidence)


def prepare_cross_file_workflow(
    sources: CrossFileSources, first_row: int, row_count: int,
) -> CsvWorkflow:
    format_name = 'CSV' if sources.measurements.csv else 'JSON'

    def run():
        limit_text = sources.limits.content
        if sources.limits.tabular or not isinstance(limit_text, str):
            raise CsvWorkflowError('The selected limits source is not verified TXT.')
        limits = _extract_limits(limit_text)
        if sources.parameter is not None:
            limits = [limit for limit in limits if _words(limit.parameter) == _words(sources.parameter)]
            if len(limits) != 1 or limits[0].operator not in ('<', '<='):
                raise CsvWorkflowError('The TXT file needs one explicit upper limit for the requested parameter.')
        yield CsvWorkflowStep(f'Explicit limits verified in the selected TXT source: {len(limits)}.')
        table = sources.measurements.content
        if (not sources.measurements.tabular or not isinstance(table, (tuple, JsonTable))
                or sources.measurements.number == sources.limits.number):
            raise CsvWorkflowError('The selected measurements source could not be verified.')
        json_table = table if isinstance(table, JsonTable) else None
        headers, all_rows = (json_table.headers, json_table.rows) if json_table is not None else table
        end = first_row - 1 + row_count
        if not 1 <= row_count <= MAX_CSV_SELECTION or not 1 <= first_row <= end <= len(all_rows):
            selection = 'CSV row range' if json_table is None else 'JSON record selection'
            raise CsvWorkflowError(f'The {selection} could not be verified for this upload.')
        rows = all_rows[first_row - 1:end]
        columns = [_Column.from_header(index, header) for index, header in enumerate(headers)]
        comparisons = _bind_measurements(limits, columns, rows, json_table=json_table)
        field_type = 'columns' if json_table is None else 'fields'
        yield CsvWorkflowStep(f'Limits matched to compatible {format_name} measurement {field_type}: {len(comparisons)}.')
        answer = _render(sources, comparisons, columns, rows, first_row, json_table)
        yield CsvWorkflowStep('Compared the selected readings with the verified limits.', answer)

    verification = 'Verify CSV columns and units' if sources.measurements.csv else 'Verify JSON fields and units'
    return CsvWorkflow(('Extract TXT limits', verification, 'Compare readings with limits'), run)
