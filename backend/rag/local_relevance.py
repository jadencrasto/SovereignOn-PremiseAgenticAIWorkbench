"""Bounded structure and technical matching for registered local text."""

from dataclasses import dataclass
from decimal import Decimal
import re

from backend.agent.numeric_receipts import local_unit_name
from backend.tools.unit_conversion import convert_units, local_unit_dimension, validate_absolute_temperature


_ENTITY = re.compile(
    r'(?<![\w-])(?!m3[ \t]*/)(?:LINE|MOTOR|HX|PT|TT|FT|LT|FV|PV|TK|P|V|M)'
    r'(?:[ \t]*[-\u2010-\u2013][ \t]*|[ \t]+)?[0-9]{1,8}[A-Z]{0,2}(?:\.[0-9]{1,4})?(?![\w-]|\.\w)', re.I,
)
_ENTITY_SEPARATOR = re.compile(r'[ \t]*[-\u2010-\u2013][ \t]*')
_ATX = re.compile(r'^(#{1,6})[ \t]+(.+)$')
_SETEXT = re.compile(r'(=+|-+)')
_TXT_SECTION = re.compile(r'^section[ \t]+(\d+(?:\.\d+){0,5})[ \t]*:[ \t]+\S', re.I)
_FENCE = re.compile(r'^(`{3,}|~{3,})(.*)$')
_ROW_DIVIDER = re.compile(r':?-{3,}:?')
_LIST = re.compile(r'^(?:[-*+]|\d+[.)])\s+')
_EXACT_UNITS = {
    'bar': 'pressure', 'barg': 'pressure', 'bara': 'pressure', 'psi': 'pressure',
    'psig': 'pressure', 'psia': 'pressure', 'pa': 'pressure', 'kpa': 'pressure', 'mpa': 'pressure',
    'l/min': 'flow', 'l/s': 'flow', 'ml/min': 'flow', 'm³/h': 'flow', 'm³/s': 'flow',
    'day': 'time', 'days': 'time', 'week': 'time', 'weeks': 'time',
    'month': 'time', 'months': 'time', 'year': 'time', 'years': 'time',
    'rpm': 'speed', 'a': 'current', 'v': 'voltage', 'w': 'power', 'kw': 'power',
    'c': 'temperature', 'f': 'temperature',
}
_UNIT_PATTERN = (
    r'(?:m(?:³|3|\^3)[ \t]*/[ \t]*[hs]|m?l[ \t]*/[ \t]*(?:min|s)|'
    r'us[ \t]+(?:fl[ \t]+oz|cup|gal)|(?:delta[ \t]+|δ[ \t]*)?'
    r'(?:°[ \t]*[cf]|degrees?[ \t]+[cf]|kelvin|celsius|fahrenheit)|'
    r'barg|bara|bar|psig|psia|psi|[km]?pa|'
    r'days?|weeks?|months?|years?|hours?|minutes?|seconds?|min|'
    r'rpm|kw|[mk]?[gml]|cm|mm|in|ft|mi|oz|lb|[hskcfavw])'
)
_NUMBER = r'[+-]?(?:[0-9]{1,12}(?:\.[0-9]{1,9})?|\.[0-9]{1,9})'
_MEASUREMENT = re.compile(
    rf'(?<![\w.,])(?P<low>{_NUMBER})(?:[ \t]*(?:[–—-]|\bto\b)[ \t]*(?P<high>{_NUMBER}))?'
    rf'[ \t]*(?P<unit>{_UNIT_PATTERN})(?![\w°]|[ \t]*[/^])', re.I,
)
_HEADER_UNIT = re.compile(rf'\([ \t]*(?P<unit>{_UNIT_PATTERN})[ \t]*\)', re.I)
_CONDITIONS = {
    'normal': r'normal(?:ly)?',
    'minimum': r'min(?:imum)?',
    'maximum': r'max(?:imum)?',
    'warning': r'warn(?:ing)?',
    'alarm': r'alarms?',
    'startup': r'start[ -]?up',
    'shutdown': r'shut[ -]?down',
    'steady': r'steady[ -]state',
    'design': r'design',
    'rated': r'rated',
}
_CONDITION = re.compile('|'.join(rf'(?P<{name}>\b{pattern}\b)' for name, pattern in _CONDITIONS.items()), re.I)
_CONDITION_GROUPS = ({'normal', 'warning', 'alarm', 'startup', 'shutdown', 'steady', 'design', 'rated'},
                     {'minimum', 'maximum'})
_QUANTITIES = {
    'pressure': r'pressure',
    'temperature_difference': r'(?:temperature|thermal)[ -]+(?:difference|differential)',
    'temperature': r'temperature|thermal',
    'flow': r'flow(?:[ -]rate)?',
    'time': r'interval|frequency|how often',
    'volume': r'(?:tank|vessel)[ -]capacity|volume',
    'voltage': r'voltage',
    'current': r'current rating|rated current',
    'power': r'power rating|rated power',
}
_QUANTITY = re.compile('|'.join(rf'(?P<{name}>\b(?:{pattern})\b)' for name, pattern in _QUANTITIES.items()), re.I)


@dataclass(frozen=True)
class Section:
    title: str
    line: int
    level: int


@dataclass(frozen=True)
class LineContext:
    sections: tuple[Section, ...]
    entity_context: str
    table_header: tuple[str, ...] = ()
    searchable: bool = True
    block_line: int | None = None
    block_kind: str = ''


def _cells(line):
    return tuple(cell.strip() for cell in line.strip().strip('|').split('|')) if '|' in line else ()


def line_contexts(text, *, markdown):
    """Describe structure independently of the durable line-based passage locator."""
    lines = text.split('\n')
    sections, contexts = [], []
    table_header, fence, label = (), None, ''
    block_line, block_kind = None, ''
    masked_heading_subject = False
    heading_underline = None
    for index, raw in enumerate(lines):
        line = raw.strip()
        number = index + 1
        following = lines[index + 1].strip() if number < len(lines) else ''
        if markdown and (marker := _FENCE.fullmatch(line)):
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            table_header, label, block_line = (), '', None
            contexts.append(LineContext(tuple(sections), '', searchable=False))
            continue
        if fence is not None or number == heading_underline:
            contexts.append(LineContext(tuple(sections), '', searchable=False))
            continue

        heading = None
        if match := _ATX.fullmatch(line):
            heading = Section(re.sub(r'\s+#+$', '', match[2]), number, len(match[1]))
        elif markdown and line and not _LIST.match(line) and not _cells(line) and _SETEXT.fullmatch(following):
            heading = Section(line, number, 1 if following.startswith('=') else 2)
            heading_underline = number + 1
        elif not markdown and (match := _TXT_SECTION.match(line)):
            heading = Section(line, number, match[1].count('.') + 1)
        if heading is not None:
            while sections and sections[-1].level >= heading.level:
                sections.pop()
            sections.append(heading)
            table_header, label, masked_heading_subject, block_line = (), '', False, None
            contexts.append(LineContext(tuple(sections), '', searchable=False))
            continue

        ancestor = next((item.title for item in reversed(sections) if entities(item.title)), '')
        direct = entities(line)
        if direct:
            # An explicitly different entity inside a section makes later bare lines ambiguous.
            if ancestor and not set(direct) <= set(entities(ancestor)):
                masked_heading_subject = True
            label = line if _ENTITY.fullmatch(line.rstrip(':').strip()) else ''
        elif not line:
            label, block_line = '', None
        subject = label or ('' if masked_heading_subject else ancestor)
        cells, next_cells = _cells(line), _cells(following)
        if markdown and cells and len(cells) == len(next_cells) and all(_ROW_DIVIDER.fullmatch(c) for c in next_cells):
            table_header = cells
            contexts.append(LineContext(tuple(sections), subject, searchable=False))
            continue
        if table_header and len(cells) != len(table_header):
            table_header = ()
        kind = ('table' if table_header else 'list' if _LIST.match(line) or
                (block_line is not None and block_kind == 'list' and raw.startswith((' ', '\t'))) else 'paragraph')
        if line and (block_line is None or block_kind != kind):
            block_line, block_kind = number, kind
        contexts.append(LineContext(tuple(sections), subject, table_header,
                                    searchable=not cells or not all(_ROW_DIVIDER.fullmatch(c) for c in cells),
                                    block_line=block_line, block_kind=block_kind if block_line is not None else ''))
    return contexts


def entities(text):
    """Return explicit identifiers keyed by conservative punctuation form."""
    return {_spelling(match[0]): match[0] for match in _ENTITY.finditer(text)}


def _spelling(value):
    return ' '.join(_ENTITY_SEPARATOR.sub('-', value).upper().split())


def _entity_key(value):
    return value.replace('-', '').replace(' ', '')


def resolve_entities(question, known):
    resolved, ambiguous = [], []
    for spelling, label in entities(question).items():
        aliases = [item for item in known if _entity_key(item) == _entity_key(spelling)]
        if spelling in known or not aliases:
            resolved.append(spelling)
        elif len(aliases) == 1:
            resolved.append(aliases[0])
        else:
            ambiguous.append(label)
    return tuple(dict.fromkeys(resolved)), tuple(ambiguous)


@dataclass(frozen=True)
class Measurement:
    raw: str
    low: Decimal
    high: Decimal
    unit: str
    dimension: str
    convertible: bool
    start: int
    end: int


def _measurement(raw, low, high, unit, start, end, *, temperature=False):
    unit = re.sub(r'\s*/\s*', '/', unit.casefold()).replace('m3/', 'm³/').replace('m^3/', 'm³/').replace('° ', '°')
    if temperature and unit in ('c', 'f'):
        unit = '°' + unit
    try:
        canonical = local_unit_name(unit)
    except ValueError:
        dimension = _EXACT_UNITS.get(unit)
        if dimension is None:
            return None
        if unit in ('days', 'weeks', 'months', 'years'):
            unit = unit[:-1]
        return Measurement(raw, low, high, unit, dimension, False, start, end)
    try:
        validate_absolute_temperature(low, canonical)
    except ValueError:
        return None
    dimension = local_unit_dimension(canonical)
    if canonical.startswith('Δ'):
        dimension = 'temperature_difference'
    return Measurement(raw, low, high, canonical, dimension, True, start, end)


def measurements(text, table_header=(), *, topic=''):
    values = []
    temperature = 'temperature' in (quantities(text) or quantities(topic))
    without_tags = _ENTITY.sub(lambda item: ' ' * len(item[0]), text)
    for match in _MEASUREMENT.finditer(without_tags):
        if re.search(r'[0-9][ \t]+$', without_tags[:match.start()]):
            continue  # Do not match the suffix of an unsupported grouped number.
        low, high = Decimal(match['low']), Decimal(match['high'] or match['low'])
        if low <= high:
            value = _measurement(text[match.start():match.end()], low, high, match['unit'],
                                 match.start(), match.end(), temperature=temperature)
            if value is not None:
                values.append(value)
    if table_header and len(cells := _cells(text)) == len(table_header):
        # A bare numeric cell inherits only an explicitly marked unit in its column header.
        spans = list(re.finditer(r'[^|]+', text.strip('|')))
        offset = int(text.startswith('|'))
        if len(spans) == len(cells):
            for cell, header, span in zip(cells, table_header, spans):
                if re.fullmatch(_NUMBER, cell) and (unit := _HEADER_UNIT.search(header)):
                    number = Decimal(cell)
                    start = offset + span.start() + len(span[0]) - len(span[0].lstrip())
                    value = _measurement(f'{cell} ({unit["unit"]} in column header)', number, number,
                                         unit['unit'], start, start + len(cell),
                                         temperature='temperature' in quantities(header))
                    if value is not None:
                        values.append(value)
    return tuple(values)


def conditions(text):
    return {match.lastgroup for match in _CONDITION.finditer(text)}


def quantities(text):
    return {match.lastgroup for match in _QUANTITY.finditer(text)}


def measurement_conditions(text, value, table_header=()):
    if table_header and len(_cells(text)) == len(table_header):
        column = text.strip('|')[:value.start - int(text.startswith('|'))].count('|')
        return conditions(table_header[column]) if 0 <= column < len(table_header) else set()
    left = max(text.rfind(sep, 0, value.start) for sep in ';,')
    right = min((text.find(sep, value.end) for sep in ';,' if sep in text[value.end:]), default=len(text))
    return conditions(text[left + 1:right])


def condition_matches(wanted, actual):
    if not wanted <= actual:
        return False
    return all(not wanted & group or (actual & group) <= wanted for group in _CONDITION_GROUPS)


def merge_conditions(*layers):
    result = set()
    for layer in layers:
        for group in _CONDITION_GROUPS:
            if layer & group:
                result.difference_update(group)
        result.update(layer)
    return result


def interval_in(source, target):
    if source.dimension != target.dimension:
        return None
    if source.unit == target.unit:
        return source.low, source.high
    if not (source.convertible and target.convertible):
        return None
    try:
        if any(Decimal(str(float(value))) != value for value in (source.low, source.high)):
            return None
        return tuple(Decimal(str(convert_units(float(value), source.unit, target.unit, local=True)))
                     for value in (source.low, source.high))
    except ValueError:
        return None


def measurement_match(query, candidate):
    interval = interval_in(query, candidate)
    if interval == (candidate.low, candidate.high):
        return 'equivalent_measurement' if query.unit != candidate.unit else 'exact_measurement'
    if interval is not None and candidate.low <= interval[0] <= interval[1] <= candidate.high:
        return 'within_range'
    return None


def lexical_text(text, values=()):
    """Remove technical identifier and measurement spans before ordinary token ranking."""
    for value in sorted(values, key=lambda item: item.start, reverse=True):
        text = text[:value.start] + ' ' * (value.end - value.start) + text[value.end:]
    return _ENTITY.sub(' ', text)
