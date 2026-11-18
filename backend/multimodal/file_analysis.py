"""Deterministic analysis of already validated, request-scoped file selections."""

import json
import operator
import re
from collections.abc import Callable, Generator, Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
from io import StringIO

from backend.multimodal.csv_file import MAX_CSV_CELL_CHARS, MAX_CSV_SELECTION, is_csv_number
from backend.multimodal.json_file import JsonEvidence

_HEADER_UNIT = re.compile(r'^(.+?)\s*(?:\(([^()]+)\)|\[([^\[\]]+)\])$')
_THRESHOLD = re.compile(
    r'\b(?P<operator>at least|at most|above|below|greater than|less than|exceed(?:s|ing)?)\s+'
    r'(?P<value>-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?)(?=\s|[°%]|$)(?P<suffix>.*)$',
    re.IGNORECASE,
)
_THRESHOLD_WORD = re.compile(
    r'\b(?:at least|at most|above|below|greater than|less than|exceed(?:s|ing)?|threshold|abnormal)\b'
)
_OPERATIONS = {
    'Maximum': r'\b(?:highest|maximum|max|largest)\b',
    'Minimum': r'\b(?:lowest|minimum|min|smallest)\b',
    'Average': r'\b(?:average|mean)\b',
    'Sum': r'\b(?:sum|total)\b',
    'Count': r'\b(?:count|how many|number of)\b',
    'Sort': r'\b(?:sort|rank|order by)\b',
}
_LOCATE = re.compile(
    r'^(?:please\s+)?(?:find|locate|show me)\s+(?:the\s+)?'
    r'(?:section|passage|paragraph|lines?)\s+(?:discussing|about|mentioning|on)\s+(.+?)\s*[?.!]*$',
    re.IGNORECASE,
)
_ROW_RANGE = re.compile(r'\b(?:rows?\s+\d|(?:first|last|top|bottom)\s+\d+\s+rows?)\b')
_ROW_COUNT = re.compile(
    r'^(?:please )?(?:count|how many|(?:what is the )?number of) (?:the )?(?:selected )?(?:data )?rows'
    r'(?: are there)?(?: in (?:the |this )?(?:selection|csv|file|table))?$'
)
_LOOKUP = re.compile(r'^(?:please )?(?:what is|show me|look up)\s+.+\s+(?:for|of)\s+.+$')
_MAX_PASSAGE_LINES = 12
_ROW_IDENTIFIERS = {'equipment', 'equipment name', 'pump', 'name', 'item', 'id'}


def _words(value: str) -> str:
    return ' '.join(re.findall(r'\w+', value.casefold()))


def _mentions(haystack: str, phrase: str) -> bool:
    return bool(phrase) and f' {phrase} ' in f' {haystack} '


def literal_file_block(lines: list[str]) -> str:
    # Uploaded text remains literal even if it contains Markdown links or diagram fences.
    body = '\n'.join(lines)
    longest = max((len(run) for run in re.findall(r'\x60+', body)), default=0)
    fence = chr(96) * max(3, longest + 1)
    return f'{fence}text\n{body}\n{fence}'


def locate_text_passage(question: str, text: str, filename: str, first_line: int) -> str | None:
    """Return actual matching lines for a literal passage lookup."""
    match = _LOCATE.fullmatch(question)
    if not match:
        return None
    terms = _words(match[1]).split()
    with StringIO(text, newline='') as source:
        matches = []
        for number, line in enumerate(source, first_line):
            line_words = set(_words(line).split())
            if terms and line_words.issuperset(terms):
                excerpt = line.rstrip('\r\n')
                matches.append(f'Line {number}: {excerpt}')
    heading = 'Matching lines in the selected text.' if matches else 'No matching lines in the selected text.'
    if len(matches) > _MAX_PASSAGE_LINES:
        heading = f'Showing the first {_MAX_PASSAGE_LINES} of {len(matches)} matching lines in the selected text.'
    return heading + '\n\n' + literal_file_block([f'File: {filename}', *matches[:_MAX_PASSAGE_LINES]])


@dataclass(frozen=True)
class _Column:
    index: int
    header: str
    label: str
    unit: str

    @classmethod
    def from_header(cls, index: int, header: str):
        match = _HEADER_UNIT.fullmatch(header)
        if match:
            return cls(index, header, match[1].strip(), (match[2] or match[3]).strip())
        return cls(index, header, header, '')


@dataclass(frozen=True)
class _Cell:
    row: int
    raw: str
    value: Decimal | None
    label: str


def _candidate_labels(rows: list[list[str]], columns: list[_Column], metric: _Column) -> list[_Column]:
    text_columns = [
        column for column in columns if column != metric and any(
            any(char.isalpha() for char in row[column.index]) for row in rows
        )
    ]
    identifiers = [column for column in text_columns if _words(column.label) in _ROW_IDENTIFIERS]
    return identifiers or text_columns


def _cells(rows, first_row, metric, label_column):
    for number, row in enumerate(rows, first_row):
        raw = row[metric.index].strip()
        label = row[label_column.index].strip() if label_column is not None else ''
        yield _Cell(number, raw, _number(raw), label)


def _source_lines(filename: str, column: _Column, cells: list[_Cell], *, show_labels: bool,
                  evidence: JsonEvidence | None = None) -> list[str]:
    if evidence is not None:
        table = evidence.table
        lines = [f'File: {filename}', f'Source {evidence.source_number} (JSON)', table.field(column.index)]
        for cell in cells:
            label = f' ({json.dumps(cell.label, ensure_ascii=False)})' if cell.label and show_labels else ''
            lines.append(f'{table.record(cell.row)}{label}: {table.evidence(cell.row, column.index)}')
        return lines
    lines = [f'File: {filename}', f'Column {chr(65 + column.index)}: {column.header}']
    for cell in cells:
        reading = cell.raw if cell.value is not None else ('missing' if not cell.raw else 'nonnumeric')
        label = ' '.join(cell.label.split())
        identity = f' ({label})' if show_labels and label else ''
        lines.append(f'Data row {cell.row}{identity}: {reading}')
    return lines


def _threshold_value(question: str, column: _Column) -> tuple[str, Decimal] | None:
    match = _THRESHOLD.search(question)
    if match is None or len(match['value']) > MAX_CSV_CELL_CHARS:
        return None
    suffix = match['suffix'].strip()
    # Allow "exceeds 5 mm/s vibration" as well as "vibration above 5 mm/s".
    if suffix.casefold() == column.label.casefold():
        suffix = ''
    elif suffix.casefold().endswith(' ' + column.label.casefold()):
        suffix = suffix[:-len(column.label)].strip()
    limit = _quantity(f'{match["value"]} {suffix}', column)
    if limit is None:
        return None
    return match['operator'].casefold(), limit


def analyze_csv_selection(
    question: str, filename: str, headers: list[str], rows: list[list[str]], first_row: int,
    *, evidence: JsonEvidence | None = None,
) -> str | None:
    """Use parsed cells and actual selection offsets. No model or agent tool is called."""
    prompt = question.rstrip('?.!').strip()
    columns = [_Column.from_header(index, header) for index, header in enumerate(headers)]
    with localcontext() as context:
        context.prec = 2 * MAX_CSV_CELL_CHARS + 8
        composed = _composed_answer(prompt, filename, columns, rows, first_row, evidence=evidence)
    if composed is not None:
        return composed
    words = _words(prompt)
    operation_words = re.sub(r'\b(?:from )?(?:lowest to highest|highest to lowest|lowest first|highest first)\b', '', words)
    operations = {name for name, pattern in _OPERATIONS.items() if re.search(pattern, operation_words)}
    threshold_requested = _THRESHOLD_WORD.search(words) is not None
    range_requested = bool(re.search(r'\b(?:difference|range)\b', words))
    compare = bool(re.match(r'^(?:please )?compare\b', words) or range_requested)
    lookup = not (operations or threshold_requested or compare) and _LOOKUP.fullmatch(words)
    if not (operations or threshold_requested or compare or lookup):
        return None
    if operations == {'Count'} and re.search(r'\b(?:missing|invalid|empty|nonnumeric|non numeric|distinct|unique)\b', words):
        return 'This CSV workflow can count selected rows, numeric cells, or numeric rows matching a threshold.'
    if _ROW_RANGE.search(words) or re.search(r'\b(?:convert|conversion|then)\b', words):
        return 'Set the CSV row selection first and ask for one supported calculation in the column’s uploaded units.'

    if operations == {'Count'} and not threshold_requested and not compare:
        if evidence is not None and (_ROW_COUNT.fullmatch(words) or
                                     re.fullmatch(r'(?:count|how many)(?: the)? (?:records|equipment)', words)):
            return f'**Record count: {len(rows)}**\n\n' + literal_file_block([
                f'File: {filename}', *evidence.lines(first_row, len(rows)),
            ])
        if _ROW_COUNT.fullmatch(words):
            return f'**Row count: {len(rows)}**\n\n' + literal_file_block([
                f'File: {filename}', f'Selected data rows: {first_row}–{first_row + len(rows) - 1}',
            ])
        if re.search(r'\b(?:rows|no|not)\b', words):
            return 'Select the rows to count or ask for a numeric threshold on one CSV column.'

    if re.search(r'\b(?:where|excluding|only|among|before|after|during|without)\b', operation_words):
        return 'Select the exact CSV rows you want to analyze, then ask for the operation on that selection.'

    selected = [column for column in columns if _mentions(words, _words(column.label))]
    subject = re.match(r'^(?:please )?which ([\w ]+?) (?:has|have)\b', words)
    if len(selected) > 1 and subject and subject[1] in _ROW_IDENTIFIERS:
        # "Which equipment has the highest temperature?" names the row label and metric.
        selected = [column for column in selected if _words(column.label) != subject[1]]
    if evidence is not None and threshold_requested:
        selected = [column for column in selected if _words(column.label) not in _ROW_IDENTIFIERS]
        if not selected:
            selected = [column for column in columns if _threshold_value(prompt, column) is not None
                        and any(_number(row[column.index]) is not None for row in rows)]
    if not selected and threshold_requested and re.search(r'\b(?:readings|values|rows)\b', words):
        selected = [
            column for column in columns
            if any(is_csv_number(row[column.index].strip()) for row in rows)
        ]
    if len(selected) != 1:
        return 'Specify a single CSV column for this calculation or lookup.'
    column = selected[0]
    other_conversion = re.search(r'\bin\s+(.+)$', prompt, re.IGNORECASE)
    if other_conversion and not threshold_requested:
        suffix = other_conversion[1].strip()
        if suffix != column.unit and _words(suffix) not in {'the csv', 'the table', 'the selected rows', 'the file', 'this csv'}:
            return 'Use the units written in the selected CSV column. This upload workflow does not convert units.'

    labels = _candidate_labels(rows, columns, column)
    source = list(_cells(rows, first_row, column, labels[0] if len(labels) == 1 else None))
    matching = [
        cells for label in labels
        if (cells := [cell for cell in _cells(rows, first_row, column, label)
                      if _mentions(words, _words(cell.label))])
    ]
    if lookup or (compare and not operations.intersection({'Maximum', 'Minimum'})):
        required = 1 if lookup else 2
        if (len(matching) != 1 or len(matching[0]) != required
                or len({_words(cell.label) for cell in matching[0]}) != required):
            return ('Name one uniquely labeled row in the selected CSV rows.' if lookup else
                    'Name two distinct rows with unique labels in the selected CSV rows.')
        source = matching[0]
    elif matching or re.search(r'\b(?:for|from)\b', operation_words):
        return 'Select the exact CSV rows you want to analyze, then ask for the operation on that selection.'
    if lookup:
        if evidence is not None:
            return 'Value extracted from the selected JSON record.\n\n' + literal_file_block(
                _source_lines(filename, column, source, show_labels=True, evidence=evidence),
            )
        original = rows[source[0].row - first_row][column.index]
        value = source[0].raw if source[0].value is not None else (
            f'{json.dumps(original, ensure_ascii=False)} (text)' if source[0].raw else '(missing)'
        )
        return 'Value extracted from the selected CSV row.\n\n' + literal_file_block([
            f'File: {filename}', f'Column {chr(65 + column.index)}: {column.header}',
            f'Data row {source[0].row}: {value}',
        ])
    defined = [cell for cell in source if cell.value is not None]
    missing = sum(not cell.raw for cell in source)
    invalid = len(source) - len(defined) - missing
    threshold = _threshold_value(prompt, column) if threshold_requested else None
    if threshold_requested and threshold is None:
        return 'Specify a numeric threshold in the selected column’s uploaded units.'
    if not defined and operations == {'Count'} and threshold is None and not compare:
        return _render('**Count: 0 numeric cells.**', filename, column, source, missing, invalid,
                       show_labels=True, evidence=evidence)
    if not defined or (compare and len(defined) < 2):
        return _render(
            'There are insufficient numeric values for this request.', filename, column, source,
            missing, invalid, show_labels=compare, evidence=evidence,
        )

    # Cell and selection limits bound the precision needed for exact sums and differences.
    with localcontext() as context:
        context.prec = 2 * MAX_CSV_CELL_CHARS + 8
        return _finish(filename, column, source, defined, operations, threshold, compare, range_requested,
                       missing, invalid, descending=not re.search(r'\b(?:ascending|lowest first|lowest to highest)\b', words),
                       evidence=evidence)


def _render(answer, filename, column, cells, missing, invalid, *, show_labels=False, evidence=None):
    if missing or invalid:
        answer += f'\n\nExcluded cells: {missing} missing and {invalid} nonnumeric.'
    return answer + '\n\n' + literal_file_block(
        _source_lines(filename, column, cells, show_labels=show_labels, evidence=evidence),
    )


def _finish(filename, column, source, defined, operations, threshold, compare, range_requested,
            missing, invalid, *, descending, evidence=None):
    values = [cell.value for cell in defined]
    lowest, highest = min(values), max(values)
    if threshold is not None:
        if operations - {'Count'} or compare:
            return 'Ask for one calculation or threshold lookup at a time.'
        operation, limit = threshold
        compare_value = _COMPARATORS[_OP_SYMBOL[operation]]
        chosen = [cell for cell in defined if compare_value(cell.value, limit)]
        condition = 'above' if operation.startswith('exceed') else operation
        noun = 'row' if len(chosen) == 1 else 'rows'
        answer = (f'**{len(chosen)} matching data {noun}** out of {len(defined)} numeric rows checked '
                  f'for values {condition} {limit:f} in the source column units.')
    elif range_requested and operations == {'Maximum', 'Minimum'}:
        chosen = [cell for cell in defined if cell.value in (lowest, highest)]
        answer = f'**Maximum − minimum: {highest - lowest:f}** (calculated in the source column units).'
    elif compare and not operations:
        chosen = defined
        difference = abs(values[1] - values[0])
        answer = f'**Absolute difference: {difference:f}** (calculated in the source column units).'
    elif compare or len(operations) != 1:
        return 'Ask for one supported CSV operation at a time.'
    elif operations in ({'Maximum'}, {'Minimum'}):
        name = next(iter(operations))
        extreme = highest if name == 'Maximum' else lowest
        chosen = [cell for cell in defined if cell.value == extreme]
        answer = f'**{name}: {chosen[0].raw}** (from the source column).'
    elif operations == {'Sort'}:
        chosen = sorted(defined, key=lambda cell: cell.value, reverse=descending)
        direction = 'highest to lowest' if descending else 'lowest to highest'
        answer = f'Numeric rows sorted by the source column from {direction}.'
    else:
        chosen = source
        total = sum(values, Decimal(0))
        if operations == {'Average'}:
            answer = f'**Average: {_average(values)}** (calculated in the source column units).'
        elif operations == {'Sum'}:
            answer = f'**Sum: {total:f}** (calculated in the source column units).'
        else:
            answer = f'**Count: {len(defined)} numeric cells.**'
    # For a failed match, show no alleged qualifying operands. Other filtered views list
    # the matching cells; aggregate operations list every selected operand and exclusion.
    return _render(answer, filename, column, chosen, missing, invalid, show_labels=True, evidence=evidence)


def _rounded_average(values: list[Decimal]) -> tuple[Decimal, bool]:
    total = sum(values, Decimal(0))
    places = max(-value.as_tuple().exponent for value in values) + 2
    result = (total / len(values)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    return result, result * len(values) != total


def _average(values: list[Decimal]) -> str:
    result, approximate = _rounded_average(values)
    prefix = 'approximately ' if approximate else ''
    return f'{prefix}{result:f}'


_FILTER_INTRO = re.compile(r'\s+(?:with|where|whose|that (?:has|have)|have(?: both)?|has both)\s+', re.I)
_QUOTED = r'"[^"\r\n]*"' + r"|'[^'\r\n]*'"
_WORD_OPERATOR = (
    r'(?:is )?(?:strictly between|between|at least|at most|greater than or equal to|less than or equal to|'
    r'greater than|less than|not equal to|equal to|above|below|exceeds?|equals?)|is not|is'
)
_PREDICATE = re.compile(
    r'(?P<column>.+?)\s*(?:(?P<symbol>>=|<=|!=|==|>|<|=)\s*|'
    rf'(?<!\S)(?P<word>{_WORD_OPERATOR})\s+)(?P<operand>.+)', re.I,
)
_COMPARATORS = {
    '>': operator.gt, '<': operator.lt, '>=': operator.ge, '<=': operator.le,
    '=': operator.eq, '!=': operator.ne,
}
_OP_SYMBOL = {
    'above': '>', 'greater than': '>', 'exceed': '>', 'exceeds': '>', 'exceeding': '>',
    'below': '<', 'less than': '<', 'at least': '>=', 'at most': '<=',
    'greater than or equal to': '>=', 'less than or equal to': '<=',
    'equal to': '=', 'equal': '=', 'equals': '=', 'is': '=', '==': '=',
    'not equal to': '!=', 'is not': '!=',
}
_QUANTITY = re.compile(r'(?P<value>-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?)(?=\s|[°%]|$)(?P<unit>.*)')
_GENERIC_METRIC = {'value', 'values', 'reading', 'readings'}
_LOOKUP_PREFIX = r'(?:(?:show(?: me)?|find|list|what is)\s+)?(?:the\s+)?'
_TOP_N = re.compile(rf'{_LOOKUP_PREFIX}(?:(highest|lowest)\s+(\d{{1,2}})|(top|bottom)\s+(\d{{1,2}}))\s+(.+)', re.I)
_ORDINAL = re.compile(rf'{_LOOKUP_PREFIX}(second|third|fourth|fifth|\d{{1,2}}(?:st|nd|rd|th))[- ](highest|lowest)\s+(.+)', re.I)
_SORT_BY = re.compile(r'(?:sort|rank)\s+(?:.+?\s+)?by\s+(.+?)(?:\s+(ascending|descending|from lowest to highest|from highest to lowest))?', re.I)
_EXTREME_WITH_DETAIL = re.compile(r'which .+? (?:has|have) the (highest|lowest) (.+?)(?: and what is (?:its|their) (.+))?', re.I)
_EXTREMA_DIFFERENCE = re.compile(r'(.+?) difference between (?:the )?(?:hottest and coolest|coolest and hottest) (?:machines|equipment|pumps|rows)', re.I)
_COMPARE_NAMES = re.compile(r'compare (.+?) (?:and|with) (.+)', re.I)
_POSITION_COMPARE = re.compile(r'compare (?:the )?first and second results(?: by (.+))?', re.I)
_PERCENT_CHANGE = re.compile(r'(?:percent|percentage) change in (.+?) from (.+?) to (.+)', re.I)
_AGGREGATE = re.compile(r'(average|mean|sum|total|minimum|maximum|count)(?: of)?\s+(.+)', re.I)
_GROUP = re.compile(r'(?:count(?: (?:rows|records|machines))?|(?:average|mean) (?:of )?(.+?)) by (.+)', re.I)
_NARROW_SELECTION = re.compile(r'(?:now )?(?:show|find|list) (?:only )?(?:those|these) (.+)', re.I)
_FILTER_ONLY = re.compile(
    r'(?:filter|(?:which|show(?: me)?|list|find|how many|count) (?:all )?(?:matching )?'
    r'(?:rows|records|equipment|pumps|machines|readings|values|items))', re.I,
)
_FILTER_OPERATOR_HINT = re.compile(r'\b(?:above|below|greater|less|equal|between|threshold)\b|[<>=]', re.I)
_SCOPED_REFERENCE = re.compile(
    r'^(?:now )?(?:(?:show|list|find)(?: me)? (?:only )?(?:those|these|their)|which of (?:those|these))\b'
    r'|\b(?:first and second results|filtered rows|matching (?:rows|machines|equipment))\b', re.I,
)
_BAD_FILTER = 'Specify up to three CSV conditions joined by “and”. Name each column and use matching column units.'
_Result = tuple[str, list[int], list[_Column], list[str]]


@dataclass(frozen=True)
class _Condition:
    column: _Column
    operation: str
    values: tuple[Decimal | str, ...]

    def matches(self, raw: str) -> bool | None:
        if not raw.strip():
            return None
        if isinstance(self.values[0], Decimal):
            raw = raw.strip()
            if not is_csv_number(raw):
                return None
            value = Decimal(raw)
            if self.operation == 'between':
                return self.values[0] <= value <= self.values[1]
            if self.operation == 'strictly between':
                return self.values[0] < value < self.values[1]
        else:
            value = raw.strip().casefold()
        return _COMPARATORS[self.operation](value, self.values[0])


def _metric(name: str, columns: Sequence[_Column], rows: Sequence[Sequence[str]]) -> _Column | None:
    name = _words(name)
    candidates = [column for column in columns if name == _words(column.header)]
    if not candidates:
        candidates = [column for column in columns if name in {
            _words(column.label), _words(column.label) + 's',
        }]
    if not candidates and name in _GENERIC_METRIC:
        candidates = [column for column in columns if any(is_csv_number(row[column.index].strip()) for row in rows)]
    return candidates[0] if len(candidates) == 1 else None


def _quantity(operand: str, column: _Column) -> Decimal | None:
    match = _QUANTITY.fullmatch(operand.strip())
    if match is None or len(match['value']) > MAX_CSV_CELL_CHARS:
        return None
    written = ' '.join(match['unit'].split())
    declared = ' '.join(column.unit.split())
    temperatures = {'°C': 'celsius', '°F': 'fahrenheit'}
    aliases = {declared}
    if declared in temperatures:
        scale = temperatures[declared]
        aliases.update({'degrees', scale, f'degrees {scale}'})
        if written.isascii() and re.fullmatch(r'[A-Za-z ]+', written):
            written = written.casefold()
    if written and written not in aliases:
        return None
    return Decimal(match['value'])


def _split_clauses(text: str) -> list[str]:
    clauses = []
    start = 0
    range_join = False
    for token in re.finditer(rf'{_QUOTED}|\b(?:greater|less) than or equal to\b|\b(?P<join>and|or)\b', text, re.I):
        connector = token['join']
        if not connector:
            continue
        chunk = text[start:token.start()]
        if connector.casefold() == 'or':
            return []
        if not range_join and re.search(r'\bbetween\s+-?[0-9]', chunk, re.I):
            range_join = True
            continue
        clauses.append(chunk.strip())
        start = token.end()
        range_join = False
    clauses.append(text[start:].strip())
    return clauses if 1 <= len(clauses) <= 3 else []


def _parse_conditions(text: str, columns: Sequence[_Column], rows: Sequence[Sequence[str]]) -> list[_Condition]:
    conditions = []
    for clause in _split_clauses(text):
        match = _PREDICATE.fullmatch(clause)
        if not match:
            return []
        column = _metric(re.sub(r'^both\s+', '', match['column'], flags=re.I).strip(), columns, rows)
        if column is None:
            return []
        operation = (match['symbol'] or match['word']).casefold()
        if operation != 'is not':
            operation = operation.removeprefix('is ')
        operation = _OP_SYMBOL.get(operation, operation)
        operand = match['operand'].strip()
        if operation in {'between', 'strictly between'}:
            bounds = re.fullmatch(r'(.+?)\s+and\s+(.+?)(?:\s+(inclusive|exclusive))?', operand, re.I)
            if not bounds or (operation == 'strictly between' and (bounds[3] or '').casefold() == 'inclusive'):
                return []
            low, high = (_quantity(bound, column) for bound in bounds.group(1, 2))
            if low is None or high is None or low > high:
                return []
            if (bounds[3] or '').casefold() == 'exclusive':
                operation = 'strictly between'
            values = (low, high)
        else:
            number = _quantity(operand, column)
            if number is not None:
                values = (number,)
            elif operation in {'=', '!='} and re.fullmatch(_QUOTED, operand) and operand[1:-1].strip():
                values = (operand[1:-1].strip().casefold(),)
            elif operation in {'=', '!='} and re.fullmatch(r'[^\W\d_][\w -]*', operand) and not any(
                is_csv_number(row[column.index].strip()) for row in rows
            ) and operand.casefold() not in {
                'missing', 'empty', 'blank', 'null', 'none', 'invalid',
            }:
                values = (operand.casefold(),)
            else:
                return []
        conditions.append(_Condition(column, operation, values))
    return conditions


def _apply_conditions(
    conditions: list[_Condition], rows: Sequence[Sequence[str]], offsets: Iterable[int],
) -> tuple[list[int], int, int]:
    matches = []
    missing = invalid = 0
    for offset in offsets:
        row = rows[offset]
        cells = [row[condition.column.index] for condition in conditions]
        evaluations = [condition.matches(raw) for condition, raw in zip(conditions, cells)]
        if any(not raw.strip() for raw in cells):
            missing += 1
        elif None in evaluations:
            invalid += 1
        elif all(evaluations):
            matches.append(offset)
    return matches, missing, invalid


def _literal_value(raw: str) -> str:
    if not raw.strip():
        return '(missing)'
    return raw.strip() if is_csv_number(raw.strip()) else json.dumps(raw, ensure_ascii=False)


def _table_evidence(filename, columns, rows, first_row, offsets, shown_columns, details=(), *, evidence=None):
    shown = list(dict.fromkeys(shown_columns))
    identifiers = [column for column in columns if _words(column.label) in _ROW_IDENTIFIERS]
    if len(identifiers) == 1 and identifiers[0] not in shown:
        shown.insert(0, identifiers[0])
    if evidence is not None:
        table = evidence.table
        lines = [f'File: {filename}', *evidence.lines(first_row, len(rows))]
        lines.extend(table.field(column.index) for column in shown)
        lines.extend(details)
        for offset in offsets:
            row = first_row + offset
            readings = ' | '.join(table.evidence(row, column.index) for column in shown)
            lines.append(f'{table.record(row)}: {readings}')
        return literal_file_block(lines)
    lines = [f'File: {filename}', f'Selected data rows: {first_row}–{first_row + len(rows) - 1}']
    lines.extend(f'Column {chr(65 + column.index)}: {column.header}' for column in shown)
    lines.extend(details)
    for offset in offsets:
        readings = ' | '.join(f'{chr(65 + column.index)}: {_literal_value(rows[offset][column.index])}' for column in shown)
        lines.append(f'Data row {first_row + offset}: {readings}')
    return literal_file_block(lines)


def _action_body(action: str) -> str:
    return re.sub(r'^(?:please )?(?:(?:find|calculate|show(?: me)?|what is|give me)\s+)?(?:the\s+)?',
                  '', action.strip(), flags=re.I)


def _advanced_intent(action: str) -> bool:
    action = _action_body(action)
    detail = _EXTREME_WITH_DETAIL.fullmatch(action)
    return bool(
        (detail and detail[3]) or _TOP_N.fullmatch(action) or _ORDINAL.fullmatch(action)
        or _SORT_BY.fullmatch(action) or _GROUP.fullmatch(action) or _EXTREMA_DIFFERENCE.fullmatch(action)
        or re.match(r'(?:percent|percentage) change\b', action, re.I)
        or (_COMPARE_NAMES.fullmatch(action) and not re.search(r'\b(?:for|of)\b', action, re.I))
    )


def _number(raw: str) -> Decimal | None:
    return Decimal(raw.strip()) if is_csv_number(raw.strip()) else None


def _numeric_values(rows, offsets, column):
    values = {}
    missing = invalid = 0
    for offset in offsets:
        raw = rows[offset][column.index]
        value = _number(raw)
        if value is not None:
            values[offset] = value
        elif raw.strip():
            invalid += 1
        else:
            missing += 1
    return values, f'Numeric operand exclusions: {missing} missing, {invalid} nonnumeric.'


def _named_offsets(names, columns, rows, offsets):
    labels = [column for column in columns if _words(column.label) in _ROW_IDENTIFIERS]
    if not labels:
        labels = [column for column in columns if all(
            any(rows[offset][column.index].strip().casefold() == name for offset in offsets) for name in names
        )]
    if len(labels) != 1:
        return None
    label = labels[0]
    matches = [[offset for offset in offsets if rows[offset][label.index].strip().casefold() == name] for name in names]
    if any(len(found) != 1 for found in matches) or matches[0] == matches[1]:
        return None
    return [found[0] for found in matches], label


def _rank_result(name, direction, position, distinct, detail, columns, rows, offsets) -> _Result | str:
    column = _metric(name, columns, rows)
    projection = _metric(detail, columns, rows) if detail else None
    if column is None or (detail and projection is None):
        return 'Name the unique CSV column or columns for this ranking.'
    if position is not None and not 1 <= position <= MAX_CSV_SELECTION:
        return f'Choose a rank or result count from 1 to {MAX_CSV_SELECTION}.'
    values, exclusions = _numeric_values(rows, offsets, column)
    ordered = _ordered_offsets(values, descending=direction == 'highest')
    if not ordered:
        return 'No numeric rows are available for this ranking.', [], [column], [exclusions]
    if distinct:
        levels = list(dict.fromkeys(values[offset] for offset in ordered))
        if position > len(levels):
            return 'The selected rows have too few distinct numeric values for that rank.', ordered, [column], [exclusions]
        chosen = [offset for offset in ordered if values[offset] == levels[position - 1]]
        heading = f'**Rank {position} ({direction} first): {levels[position - 1]:f}.** Equal values share a rank.'
    else:
        chosen = ordered[:position]
        heading = f'**{len(chosen)} rows ranked {direction} first.** Ties keep original row order.'
    shown = [column, projection] if projection else [column]
    return heading, chosen, shown, [exclusions]


def _ordered_offsets(values: dict[int, Decimal], *, descending: bool) -> list[int]:
    return sorted(values, key=values.__getitem__, reverse=descending)


def _percentage(numerator: Decimal, denominator: Decimal) -> str:
    scaled = numerator * 100
    result = (scaled / denominator).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)
    approximation = 'approximately ' if result * denominator != scaled else ''
    return f'{approximation}{result:f}%'


def _change_result(metric, left, right, columns, rows, offsets) -> _Result | str:
    column = _metric(metric, columns, rows)
    if column is None:
        return 'Name one unique CSV column for percentage change.'
    named = _named_offsets([left.strip().casefold(), right.strip().casefold()], columns, rows, offsets)
    if named is None:
        return 'Name two distinct rows with unique labels for the baseline and final value.'
    pair, label = named
    values, _ = _numeric_values(rows, pair, column)
    if len(values) != 2 or values[pair[0]] <= 0:
        return ('Percentage change requires a positive numeric baseline and a numeric final value.',
                pair, [label, column], [])
    baseline, final = (values[offset] for offset in pair)
    result = _percentage(final - baseline, baseline)
    return f'**Percentage change: {result}.**', pair, [label, column], ['Source rows are shown baseline first.']


def _group_members(rows, offsets, group) -> tuple[list[list[int]], int]:
    groups: dict[str, list[int]] = {}
    missing = 0
    for offset in offsets:
        label = rows[offset][group.index].strip()
        if label:
            groups.setdefault(label.casefold(), []).append(offset)
        else:
            missing += 1
    return list(groups.values()), missing


def _group_result(metric, key, columns, rows, offsets) -> _Result | str:
    group = _metric(key, columns, rows)
    column = _metric(metric, columns, rows) if metric else None
    if group is None or (metric and column is None):
        return 'Name unique CSV columns for the grouping and, for averages, the numeric measure.'
    groups, missing = _group_members(rows, offsets, group)
    details = [f'Rows without a group value: {missing}.']
    evidence = []
    for members in groups:
        label = _literal_value(rows[members[0]][group.index])
        evidence.extend(members)
        if column is None:
            details.append(f'Group {label}: count {len(members)}')
            continue
        values, exclusions = _numeric_values(rows, members, column)
        average = _average(list(values.values())) if values else 'unavailable'
        details.append(f'Group {label}: average {average}. {exclusions}')
    return f'**Grouped {"averages" if column else "counts"}: {len(groups)} groups.**', evidence, (
        [group, column] if column else [group]
    ), details


def _selection_result(action, columns, rows, offsets, condition_columns) -> _Result | str | None:
    action = _action_body(action)
    if condition_columns:
        action = re.sub(r'^their\s+', '', action, flags=re.I)
        action = re.sub(r'\s+among (?:the )?filtered rows$', '', action, flags=re.I)
    if match := _GROUP.fullmatch(action):
        return _group_result(match[1], match[2], columns, rows, offsets)
    if match := _PERCENT_CHANGE.fullmatch(action):
        return _change_result(*match.groups(), columns, rows, offsets)
    if match := _EXTREMA_DIFFERENCE.fullmatch(action):
        column = _metric('temperature', columns, rows)
        if column is None or column != _metric(match[1], columns, rows):
            return 'Name the unique temperature column for the difference between hottest and coolest rows.'
        values, exclusions = _numeric_values(rows, offsets, column)
        if len(values) < 2:
            return 'Two numeric rows are required for the temperature difference.', offsets, [column], [exclusions]
        low, high = min(values.values()), max(values.values())
        evidence = [offset for offset, value in values.items() if value in (low, high)]
        return f'**Maximum − minimum: {high - low:f}.**', evidence, [column], [exclusions]
    if match := _POSITION_COMPARE.fullmatch(action):
        metrics = [_metric(match[1], columns, rows)] if match[1] else list(dict.fromkeys(condition_columns))
        if len(offsets) < 2:
            return 'The filter yielded fewer than two rows. The comparison could not be completed.'
        if len(metrics) != 1 or metrics[0] is None:
            return 'Name one numeric CSV column for the comparison of the first two matches.'
        pair = offsets[:2]
        values, exclusions = _numeric_values(rows, pair, metrics[0])
        heading = ('Both matching rows need numeric values for the comparison.' if len(values) != 2 else
                   f'**Absolute difference: {abs(values[pair[0]] - values[pair[1]]):f}.**')
        return heading, pair, metrics, [exclusions]
    if match := _COMPARE_NAMES.fullmatch(action):
        named = _named_offsets([name.strip().casefold() for name in match.groups()], columns, rows, offsets)
        if named is None:
            return 'Name two distinct rows with unique labels in the selected CSV rows.'
        pair, label = named
        measures = [column for column in columns if column != label and any(
            _number(rows[offset][column.index]) is not None for offset in offsets
        )]
        if not measures:
            return 'No numeric CSV columns are available for this row comparison.'
        return '**Comparison of the two named rows, by column.**', pair, [label, *measures], []

    if match := _TOP_N.fullmatch(action):
        direction = match[1] or ('highest' if match[3].casefold() == 'top' else 'lowest')
        return _rank_result(match[5], direction.casefold(), int(match[2] or match[4]), False, None, columns, rows, offsets)
    if match := _ORDINAL.fullmatch(action):
        ordinal = match[1].casefold()
        ranks = {'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5}
        rank = ranks[ordinal] if ordinal in ranks else int(ordinal[:-2])
        return _rank_result(match[3], match[2].casefold(), rank, True, None, columns, rows, offsets)
    if match := _SORT_BY.fullmatch(action):
        descending = (match[2] or '').casefold() not in {'ascending', 'from lowest to highest'}
        return _rank_result(match[1], 'highest' if descending else 'lowest', None, False, None, columns, rows, offsets)
    if match := _EXTREME_WITH_DETAIL.fullmatch(action):
        return _rank_result(match[2], match[1].casefold(), 1, True, match[3], columns, rows, offsets)
    if re.fullmatch(r'rank matching (?:machines|rows|equipment|pumps)', action, re.I):
        if len(set(condition_columns)) != 1:
            return 'Name one column to rank the matching rows by.'
        return _rank_result(condition_columns[0].header, 'highest', None, False, None, columns, rows, offsets)

    if not condition_columns:
        return None
    if re.fullmatch(r'(?:count|how many) (?:the )?(?:matching )?(?:rows|machines|equipment|pumps|records)', action, re.I):
        return f'**Matching rows: {len(offsets)}.**', offsets, [], []
    if match := _AGGREGATE.fullmatch(action):
        column = _metric(re.sub(r'^their\s+', '', match[2], flags=re.I), columns, rows)
        if column is None:
            return 'Name one unique CSV column for this aggregate.'
        values, exclusions = _numeric_values(rows, offsets, column)
        operands = list(values.values())
        operation = match[1].casefold()
        if not operands and operation != 'count':
            return 'The filter yielded no usable numeric operands. The aggregate could not be completed.', offsets, [column], [exclusions]
        if operation in {'average', 'mean'}:
            value = _average(operands)
        elif operation in {'sum', 'total'}:
            value = f'{sum(operands):f}'
        elif operation in {'minimum', 'maximum'}:
            value = f'{(min if operation == "minimum" else max)(operands):f}'
        else:
            value = str(len(operands))
        return f'**{operation.capitalize()}: {value}.**', offsets, [column], [exclusions]
    return None


def _filter_details(conditions: list[_Condition], total: int, matched: int, missing: int, invalid: int) -> list[str]:
    lines = [f'Filter {condition.column.header}: {condition.operation} '
             + ' and '.join(f'{v:f}' if isinstance(v, Decimal) else json.dumps(v, ensure_ascii=False)
                            for v in condition.values) for condition in conditions]
    return lines + [f'Filter matched {matched} of {total} rows. '
                    f'Excluded rows: {missing} with missing values, {invalid} with invalid numeric values.']


def _composed_answer(
    prompt: str, filename: str, columns: list[_Column], rows: list[list[str]], first_row: int,
    *, evidence: JsonEvidence | None = None,
) -> str | None:
    """Resolve a bounded filter and its optional next operation on this selection."""
    steps = [match for match in re.finditer(rf'{_QUOTED}|(?P<step>,?\s+then\s+)', prompt, re.I) if match['step']]
    base, action = prompt, prompt
    if steps:
        cut = steps[0]
        base, action = prompt[:cut.start()].strip(), prompt[cut.end():].strip()

    introduction = _FILTER_INTRO.search(base)
    filter_text = None
    condition_text = base[introduction.end():] if introduction else ''
    if introduction and (
        _PREDICATE.fullmatch(condition_text)
        or (_FILTER_ONLY.fullmatch(base[:introduction.start()]) and _FILTER_OPERATOR_HINT.search(condition_text))
    ):
        filter_text = condition_text
        if not steps:
            action = base[:introduction.start()]
    elif re.match(r'^filter\b', base, re.I):
        filter_text = re.sub(r'^filter(?:\s+(?:to\s+)?(?:rows|records|equipment))?\s+', '', base, flags=re.I)
        action = action if steps else 'filter'
    elif direct := re.fullmatch(r'(?:find|show(?: me)?|list)\s+(?:all\s+)?(.+)', base, re.I):
        predicate = _PREDICATE.fullmatch(direct[1])
        if predicate and (steps or len(_split_clauses(direct[1])) != 1
                          or predicate['symbol'] or predicate['word'].casefold() not in {
            'above', 'below', 'greater than', 'less than', 'at least', 'at most', 'exceed', 'exceeds',
        }):
            filter_text = direct[1]
            if not steps:
                action = 'filter'

    if steps:
        if filter_text is None:
            return None  # Retain the existing single-operation clarification.
        if len(steps) > 1 or not action:
            return 'Ask for a filter followed by one supported CSV operation.'

    if filter_text is None and _SCOPED_REFERENCE.search(prompt):
        return 'Reattach the CSV and restate the row selection or filter in this question.'
    if filter_text is None and not _advanced_intent(action):
        return None

    conditions = _parse_conditions(filter_text, columns, rows) if filter_text is not None else []
    if filter_text is not None and not conditions:
        return _BAD_FILTER
    offsets = list(range(len(rows)))
    details = []
    condition_columns = [condition.column for condition in conditions]
    if conditions:
        offsets, missing, invalid = _apply_conditions(conditions, rows, offsets)
        details = _filter_details(conditions, len(rows), len(offsets), missing, invalid)

    if steps and (narrow := _NARROW_SELECTION.fullmatch(action)):
        metrics = list(dict.fromkeys(condition.column for condition in conditions if isinstance(condition.values[0], Decimal)))
        extra = _parse_conditions(f'values {narrow[1]}', metrics, rows) if len(metrics) == 1 else []
        if len(extra) == 1 and len(conditions) < 3 and isinstance(extra[0].values[0], Decimal):
            narrowed, missing, invalid = _apply_conditions(extra, rows, offsets)
            notes = _filter_details(extra, len(offsets), len(narrowed), missing, invalid)
            result = (f'**Matching rows after narrowing: {len(narrowed)}.**', narrowed, [], notes)
        else:
            result = 'Use a numeric comparison on the single metric from the first filter to narrow these rows.'
    else:
        result = _selection_result(action, columns, rows, offsets, condition_columns)
    if result is None:
        if not conditions or steps or not _FILTER_ONLY.fullmatch(action):
            return 'Ask for a filter followed by one supported CSV operation.'
        result = (f'**Matching rows: {len(offsets)}.**', offsets, [], [])
    if isinstance(result, str):
        if not conditions:
            return result
        return result + '\n\n' + _table_evidence(filename, columns, rows, first_row, offsets, condition_columns, details,
                                                 evidence=evidence)
    heading, evidence_rows, metrics, notes = result
    return heading + '\n\n' + _table_evidence(
        filename, columns, rows, first_row, evidence_rows, condition_columns + metrics, details + notes, evidence=evidence,
    )


@dataclass(frozen=True)
class CsvWorkflowStep:
    summary: str
    answer: str | None = None


@dataclass(frozen=True)
class CsvWorkflow:
    descriptions: tuple[str, ...]
    _run: Callable[[], Generator[CsvWorkflowStep, None, None]]

    def updates(self) -> Generator[CsvWorkflowStep, None, None]:
        # Do not leave the decimal context installed while the HTTP stream is suspended.
        with closing(self._run()) as steps:
            while True:
                with localcontext() as context:
                    context.prec = 2 * MAX_CSV_CELL_CHARS + 8
                    step = next(steps, None)
                if step is None:
                    return
                yield step


class CsvWorkflowError(ValueError):
    """A safe, user-facing failure in a deterministic attachment step."""


@dataclass(frozen=True)
class _CsvSelection:
    source: object
    offsets: tuple[int, ...]


@dataclass(frozen=True)
class _WorkflowTable:
    filename: str
    columns: tuple[_Column, ...]
    rows: tuple[tuple[str, ...], ...]
    first_row: int
    json_evidence: JsonEvidence | None = None

    def column(self, name: str) -> _Column:
        column = _metric(name, self.columns, self.rows)
        if column is None:
            raise CsvWorkflowError('Name one unambiguous column in this upload for the operation.')
        return column

    def verify(self, selection: _CsvSelection, expected: tuple[int, ...]) -> tuple[int, ...]:
        if (not isinstance(selection, _CsvSelection) or selection.source is not self
                or type(selection.offsets) is not tuple
                or any(type(offset) is not int or not 0 <= offset < len(self.rows) for offset in selection.offsets)
                or len(set(selection.offsets)) != len(selection.offsets)
                or selection.offsets != expected):
            raise CsvWorkflowError('The previous row selection could not be verified for this upload.')
        return selection.offsets

    def evidence(self, offsets, columns, details):
        return _table_evidence(self.filename, self.columns, self.rows, self.first_row, offsets, columns, details,
                               evidence=self.json_evidence)

    @property
    def positions(self):
        return 'JSON record indices' if self.json_evidence is not None else 'data rows'

    def row_numbers(self, offsets):
        first = self.first_row - (self.json_evidence is not None)
        return ', '.join(str(first + offset) for offset in offsets)


def _share_workflow(table: _WorkflowTable, filter_text: str, final_action: str) -> Generator[CsvWorkflowStep, None, None]:
    conditions = _parse_conditions(filter_text, table.columns, table.rows)
    if len(conditions) != 1 or not isinstance(conditions[0].values[0], Decimal):
        raise CsvWorkflowError('Use one numeric filter on a named column or the only numeric column, with matching units.')
    column = conditions[0].column
    offsets, missing, invalid = _apply_conditions(conditions, table.rows, range(len(table.rows)))
    expected = tuple(offsets)
    matched = _CsvSelection(table, expected)
    details = _filter_details(conditions, len(table.rows), len(expected), missing, invalid)
    yield CsvWorkflowStep(f'Filter matched {len(expected)} selected rows.')

    verified = table.verify(matched, expected)
    count = len(verified)
    yield CsvWorkflowStep(f'Counted {count} matching rows.')

    if not re.fullmatch(r'(?:calculate|find|compute) (?:what |the )?percentage of all valid readings'
                        r'(?: they represent)?', final_action, re.I):
        raise CsvWorkflowError('Use the percentage of all valid readings in the filtered column as the denominator.')
    valid, _ = _numeric_values(table.rows, range(len(table.rows)), column)
    if not valid:
        raise CsvWorkflowError('There are no valid numeric readings in the selected rows to use as a denominator.')
    if any(offset not in valid for offset in verified):
        raise CsvWorkflowError('The matching rows could not be verified against the denominator.')
    percentage = _percentage(Decimal(count), Decimal(len(valid)))
    details.extend([
        f'Matching count: {count}. Valid denominator: {len(valid)}.',
        f'Valid {table.positions}: {table.row_numbers(valid)}. Matching {table.positions}: '
        f'{table.row_numbers(verified) or "none"}.',
    ])
    answer = (f'**{percentage}** of valid readings in the selected rows match the filter '
              f'({count} of {len(valid)}).\n\n' + table.evidence(valid, [column], details))
    yield CsvWorkflowStep('Calculated the share of valid readings.', answer)


def _rank_comparison_workflow(
    table: _WorkflowTable, limit: int, direction: str, metric: str,
) -> Generator[CsvWorkflowStep, None, None]:
    temperature = table.column('temperature')
    if not 2 <= limit <= MAX_CSV_SELECTION:
        raise CsvWorkflowError(f'Choose between 2 and {MAX_CSV_SELECTION} ranked rows for a comparison.')
    values, exclusions = _numeric_values(table.rows, range(len(table.rows)), temperature)
    expected = tuple(_ordered_offsets(values, descending=direction == 'hottest')[:limit])
    if not expected:
        raise CsvWorkflowError('No numeric temperatures are available for ranking.')
    ranked = _CsvSelection(table, expected)
    yield CsvWorkflowStep(f'Ranked {len(expected)} rows by temperature. Selected {table.positions} {table.row_numbers(expected)}.')

    offsets = table.verify(ranked, expected)
    if len(offsets) < limit:
        raise CsvWorkflowError('There are fewer valid temperatures than requested ranked rows.')
    comparison = table.column(metric)
    measured, comparison_exclusions = _numeric_values(table.rows, offsets, comparison)
    if len(measured) < 2:
        raise CsvWorkflowError('At least two ranked rows must have numeric values in the comparison column.')
    spread = max(measured.values()) - min(measured.values())
    details = [f'First ranked the {limit} {direction} rows by temperature. Equal temperatures keep original row order.',
               f'Temperature ranking. {exclusions}', f'Comparison. {comparison_exclusions}',
               f'Comparison range in the source unit: {spread:f}.']
    answer = (f'**The comparison values span {spread:f} in the source column units** across '
              f'{len(measured)} of the {limit} {direction} selected rows.\n\n'
              + table.evidence(offsets, [temperature, comparison], details))
    yield CsvWorkflowStep('Compared verified readings from the ranked rows.', answer)


def _group_winner_workflow(table: _WorkflowTable, key: str, metric: str) -> Generator[CsvWorkflowStep, None, None]:
    group = table.column(key)
    members, missing = _group_members(table.rows, range(len(table.rows)), group)
    selections = [(_CsvSelection(table, tuple(rows)), tuple(rows)) for rows in members]
    yield CsvWorkflowStep(f'Found {len(selections)} groups. Rows without a group value: {missing}.')

    column = table.column(metric)
    groups = []
    details = [f'Rows without a group value: {missing}.']
    evidence = []
    for selection, expected in selections:
        offsets = table.verify(selection, expected)
        values, exclusions = _numeric_values(table.rows, offsets, column)
        label = _literal_value(table.rows[offsets[0]][group.index])
        evidence.extend(offsets)
        details.append(f'Group {label}, {table.positions} {table.row_numbers(offsets)}. {exclusions}')
        if values:
            operands = list(values.values())
            # Compare exact means so display rounding cannot produce a false tie.
            mean = Fraction(sum(operands)) / len(operands)
            groups.append((mean, label, _average(operands), tuple(values)))
        else:
            details.append('Average unavailable.')
    averages = tuple(groups)
    if not averages:
        raise CsvWorkflowError('No group contains a valid numeric reading in that column.')
    yield CsvWorkflowStep(f'Calculated averages for {len(averages)} groups with valid readings.')

    greatest = max(mean for mean, *_ in averages)
    for mean, label, display, offsets in averages:
        rank = 'Highest average' if mean == greatest else 'Average'
        details.append(f'{rank} for {label}: {display}. Valid {table.positions}: {table.row_numbers(offsets)}.')
    best = next(display for mean, _, display, _ in averages if mean == greatest)
    ties = sum(mean == greatest for mean, *_ in averages)
    answer = (f'**Highest group average: {best} in the source column units.** '
              f'{ties} group{"s" if ties != 1 else ""} at that exact average.\n\n'
              + table.evidence(evidence, [group, column], details))
    yield CsvWorkflowStep('Identified the highest verified group average.', answer)


def _extrema_workflow(table: _WorkflowTable, metric: str) -> Generator[CsvWorkflowStep, None, None]:
    column = table.column(metric)
    values, exclusions = _numeric_values(table.rows, range(len(table.rows)), column)
    if len(values) < 2:
        raise CsvWorkflowError('At least two valid numeric readings are required for an extrema comparison.')
    high, low = max(values.values()), min(values.values())
    expected = tuple(offset for offset, value in values.items() if value in (high, low))
    extremes = _CsvSelection(table, expected)
    positions = 'Data rows' if table.json_evidence is None else table.positions
    yield CsvWorkflowStep(f'Found highest {high:f} and lowest {low:f}. {positions} {table.row_numbers(expected)}.')

    offsets = table.verify(extremes, expected)
    readings = [values[offset] for offset in offsets]
    difference = max(readings) - min(readings)
    unit = f' {column.unit}' if column.unit else ''
    details = [exclusions, f'Highest: {high:f}{unit}. {positions}: {table.row_numbers(i for i in offsets if values[i] == high)}.',
               f'Lowest: {low:f}{unit}. {positions}: {table.row_numbers(i for i in offsets if values[i] == low)}.',
               f'Difference: {high:f} − {low:f} = {difference:f}{unit}.']
    answer = (f'**The highest reading is {difference:f} above the lowest in the source column units.**\n\n'
              + table.evidence(offsets, [column], details))
    yield CsvWorkflowStep(f'Calculated the same-column difference: {difference:f}.', answer)


_SHARE_WORKFLOW = re.compile(
    r'(?:filter|find|show)\s+(.+?)(?:,\s*|\s+)(?:then\s+)?count (?:them|the matches)'
    r'(?:,\s*|\s+)(?:and\s+|then\s+)?(.+)', re.I,
)
_RANK_COMPARISON_WORKFLOW = re.compile(
    r'(?:find|show|rank) (?:the )?(\w+) (hottest|coolest) (?:machines|pumps|rows)'
    r'(?:,?\s+)(?:and|then) compare their (.+?)(?: readings| values)?', re.I,
)
_GROUP_WINNER_WORKFLOW = re.compile(
    r'group (?:the )?(?:equipment|machines|pumps|records|rows) by (.+?),\s*(?:then )?'
    r'(?:calculate|compute|find) (?:the )?average (.+?) for each group,?\s*'
    r'(?:and|then) (?:identify|find|show) (?:the )?highest(?: group(?: average)?| average)?', re.I,
)
_EXTREMA_WORKFLOW = re.compile(
    r'(?:(?:find|show) (?:the )?highest and lowest (.+?),?\s+then calculate (?:their|the) difference|'
    r'which .+? has the (?:greatest|highest) (.+?),?\s+and how much higher is it than the lowest|'
    r'(?:find|calculate|what is) (?:the )?difference between (?:the )?'
    r'(?:highest and lowest|lowest and highest) (.+))', re.I,
)
_RANK_WORDS = ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve')


def prepare_csv_workflow(
    question: str, filename: str, headers: list[str], rows: list[list[str]], first_row: int,
    *, evidence: JsonEvidence | None = None,
) -> CsvWorkflow | None:
    """Recognize fixed dependent operations in the existing attachment analysis path."""
    prompt = re.sub(r'^please\s+', '', question.rstrip('?.!').strip(), flags=re.I)
    if match := _SHARE_WORKFLOW.fullmatch(prompt):
        descriptions = ('Filter selected rows', 'Count matching rows', 'Calculate percentage of valid readings')
        run, args = _share_workflow, match.groups()
    elif match := _RANK_COMPARISON_WORKFLOW.fullmatch(prompt):
        count = match[1].casefold()
        limit = int(count) if re.fullmatch(r'[0-9]{1,2}', count) else (_RANK_WORDS.index(count) if count in _RANK_WORDS else 0)
        descriptions = ('Rank selected rows by temperature', 'Compare readings from ranked rows')
        run, args = _rank_comparison_workflow, (limit, match[2].casefold(), match[3])
    elif match := _GROUP_WINNER_WORKFLOW.fullmatch(prompt):
        descriptions = ('Group selected rows', 'Calculate each group average', 'Identify the highest group average')
        run, args = _group_winner_workflow, match.groups()
    elif match := _EXTREMA_WORKFLOW.fullmatch(prompt):
        descriptions = ('Find the highest and lowest readings', 'Calculate the difference')
        run, args = _extrema_workflow, (next(value for value in match.groups() if value is not None),)
    else:
        return None
    columns = tuple(_Column.from_header(index, name) for index, name in enumerate(headers))
    table = _WorkflowTable(filename, columns, tuple(tuple(row) for row in rows), first_row, evidence)
    return CsvWorkflow(descriptions, lambda: run(table, *args))
