"""Bounded structure and technical matching for registered local text."""

from collections import Counter
from dataclasses import dataclass, replace
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
MAX_TABLE_COLUMNS = 16
MAX_TABLE_ROWS = 32
MAX_TABLE_CELL = 120
MAX_TABLE_LINE = 320  # A structured row must fit inside one existing citation passage.
_TABLE_ROLES = {
    'equipment': {'equipment', 'device', 'asset', 'tag', 'equipment id', 'equipment tag'},
    'condition': {'condition', 'operating condition', 'operating mode', 'mode', 'state'},
    'component': {'component', 'subsystem'},
}
_EXACT_UNITS = {
    'bar': 'pressure', 'barg': 'pressure', 'bara': 'pressure', 'psi': 'pressure',
    'psig': 'pressure', 'psia': 'pressure', 'pa': 'pressure', 'kpa': 'pressure', 'mpa': 'pressure',
    'l/min': 'flow', 'l/s': 'flow', 'ml/min': 'flow', 'm³/h': 'flow', 'm³/s': 'flow',
    'day': 'time', 'days': 'time', 'week': 'time', 'weeks': 'time',
    'month': 'time', 'months': 'time', 'year': 'time', 'years': 'time',
    'rpm': 'speed', 'a': 'current', 'v': 'voltage', 'w': 'power', 'kw': 'power',
    'c': 'temperature', 'f': 'temperature',
    'n·m': 'torque', 'lbf·ft': 'torque',
}
_TORQUE_UNIT = r'(?:n[ \t]*(?:[·⋅*-][ \t]*)?m|lbf?[ \t]*[·⋅*-][ \t]*ft|ft[ \t]*[·⋅*-][ \t]*lbf?)'
_UNIT_PATTERN = (
    rf'(?:{_TORQUE_UNIT}|m(?:³|3|\^3)[ \t]*/[ \t]*[hs]|m?l[ \t]*/[ \t]*(?:min|s)|'
    r'us[ \t]+(?:fl[ \t]+oz|cup|gal)|(?:delta[ \t]+|δ[ \t]*)?'
    r'(?:°[ \t]*[cf]|degrees?[ \t]+[cf]|kelvin|celsius|fahrenheit)|'
    r'barg|bara|bar|psig|psia|psi|[km]?pa|'
    r'days?|weeks?|months?|years?|hours?|minutes?|seconds?|min|'
    r'rpm|kw|[mk]?[gml]|cm|mm|in|ft|mi|oz|lb|[hskcfavw])'
)
_NUMBER = r'[+-]?(?:[0-9]{1,12}(?:\.[0-9]{1,9})?|\.[0-9]{1,9})'
_RANGE = rf'(?P<low>{_NUMBER})(?:[ \t]*(?:[–—-]|\bto\b)[ \t]*(?P<high>{_NUMBER}))?'
_NUMBER_RANGE = re.compile(_RANGE, re.I)
_MEASUREMENT = re.compile(
    rf'(?<![\w.,]){_RANGE}'
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
    'pressure': r'pressures?',
    'temperature_difference': r'(?:temperature|thermal)[ -]+(?:difference|differential)',
    'temperature': r'temperatures?|thermal',
    'flow': r'flow(?:[ -]rate)?',
    'time': r'intervals?|frequenc(?:y|ies)|cadence|how (?:often|frequently)',
    'volume': r'(?:tank|vessel)[ -]capacity|volume',
    'voltage': r'voltage',
    'current': r'current rating|rated current',
    'power': r'power rating|rated power',
    'torque': r'torque|tightening moment',
    'speed': r'speed|rpm',
}
_QUANTITY = re.compile('|'.join(rf'(?P<{name}>\b(?:{pattern})\b)' for name, pattern in _QUANTITIES.items()), re.I)

_FACT_MARKER = re.compile(
    r'\b(?:(?P<limit>operating (?:limits?|bounds?))|(?P<alarm>alarm (?:thresholds?|setpoints?))|'
    r'(?P<procedure>(?:inspection|maintenance) (?:procedures?|recommendations?|requirements?|instructions?|steps?))|'
    r'(?P<duration>duration|how long)|(?P<height>height))\b', re.I,
)
_TOPIC_SEARCH = re.compile(
    r'^\s*(?:summari[sz]e|give (?:me )?an overview of)\b|'
    r'\b(?:notes|sections|passages|documents|information) (?:about|on)\b', re.I,
)
_UNHANDLED_FACT = re.compile(
    r'\b(?:warrant(?:y|ies)|manufacturer|certified|'
    r'(?:serial|model|part|drawing|certification) (?:number|code|identifier)|'
    r'(?:inspection|calibration|maintenance|installation|manufacture|certification) dates?)\b', re.I,
)
_COMPONENT = re.compile(
    r'\b(?:(?:left|right|upper|lower|primary|secondary)[ -]+)?'
    r'(?:seal carriers?|valve stems?|outlet probes?|inlet probes?|coolant circuits?|test loops?|tube bundles?|'
    r'access platforms?|relief valves?|drive bearings?|seals?|flanges?|bearings?|couplings?|nozzles?|'
    r'inlets?|outlets?|discharges?|drains?|shafts?|rotors?|impellers?|housings?|fasteners?)\b', re.I,
)
_PROCEDURE_ACTION = re.compile(
    r'(?:^|[:;.!?])\s*(?:[-*+]\s+|\d+[.)]\s+)?'
    r'(?:inspect|check|examine|verify|test|replace|service|calibrate|lubricate|clean|tighten)\b', re.I,
)
_REQUIREMENT_STATEMENT = re.compile(r'\b(?:must|shall|is required to|are required to|is prohibited)\b', re.I)
_INSPECTION = re.compile(r'\b(?:inspect(?:ion|ions)?|check|examine|verify|test)\b', re.I)
_REPETITION = re.compile(r'\b(?:every|each)\s*$', re.I)
_BOUND = re.compile(r'\b(?:limits?|thresholds?|setpoints?)\b', re.I)
_BOUND_CONDITIONS = frozenset({'minimum', 'maximum', 'alarm', 'warning'})
_CLAUSE_BREAK = re.compile(
    r';|,(?!\d)|[.!?](?=\s|$)|\s+(?:and|but)\s+(?=(?:it\s+)?'
    r'(?:operates?|runs?|has|requires?|specifies|sets?)\b)', re.I,
)
_OPERATING_OBSERVATION = re.compile(r'\b(?:operates?|runs?)\s+(?:normally\s+)?at\s*$', re.I)
_VALUE_ONLY = re.compile(r'(?:\s|[.:]|\b(?:is|of|to|at|value|set|maximum|minimum|limit|range)\b)*', re.I)
_NON_FACT = re.compile(r'\b(?:not (?:yet )?(?:set|specified|available|recorded)|not\s+[+-]?\d+|unknown|unavailable|'
                       r'tbd|for (?:example|reference)|do not (?:use|apply))\b', re.I)


@dataclass(frozen=True)
class RequestedFact:
    attribute: str
    components: frozenset[str]
    inspection: bool = False
    bound: bool = False

    @property
    def dimension(self):
        return {'duration': 'time', 'height': 'length'}.get(self.attribute, self.attribute)


def fact_components(text):
    return frozenset(' '.join(match.group().casefold().replace('-', ' ').split()).rstrip('s')
                     for match in _COMPONENT.finditer(text))


def _fact_markers(text):
    return quantities(text) | {match.lastgroup for match in _FACT_MARKER.finditer(text)}


def requested_fact(question, values, *, navigation=False):
    """Recognize a small set of requested attributes without inferring missing slots."""
    text = lexical_text(question, values)
    if _UNHANDLED_FACT.search(text) and not navigation:
        return RequestedFact('unrecognized', frozenset())
    attributes = _fact_markers(text)
    specific = attributes - {'limit', 'alarm', 'procedure'}
    if specific:
        attributes = specific
    elif len(attributes) > 1:
        attributes -= {'limit'}
    if len(attributes) == 1:
        attribute = attributes.pop()
        return RequestedFact(
            attribute, fact_components(text),
            inspection=attribute == 'time' and bool(_INSPECTION.search(text)), bound=bool(_BOUND.search(text)),
        )
    if not attributes and re.search(r'\brequirements?\b', text, re.I):
        return RequestedFact('requirements', frozenset())
    if not attributes and (navigation or _TOPIC_SEARCH.search(text)):
        return None
    return RequestedFact('unrecognized', frozenset())


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
    table_header_line: int | None = None


@dataclass(frozen=True)
class PipeCell:
    text: str
    start: int
    end: int


def _pipe_cells(line):
    if ('|' not in line or len(line) > MAX_TABLE_LINE or '\\|' in line
            or re.search(r'`[^`]*\|[^`]*`', line)):
        return ()
    cells, offset = [], 0
    for index, raw in enumerate(line.split('|')):
        if not (index == 0 and not raw.strip() or offset + len(raw) == len(line) and not raw.strip()):
            start = offset + len(raw) - len(raw.lstrip())
            cell = raw.strip()
            cells.append(PipeCell(cell, start, start + len(cell)))
        offset += len(raw) + 1
    if not 2 <= len(cells) <= MAX_TABLE_COLUMNS or any(len(cell.text) > MAX_TABLE_CELL for cell in cells):
        return ()
    return tuple(cells)


def _cells(line):
    return tuple(cell.text for cell in _pipe_cells(line))


def _role_columns(headers, role):
    return [index for index, header in enumerate(headers)
            if ' '.join(header.casefold().split()) in _TABLE_ROLES[role]]


def _table_role(text, headers, role):
    columns = _role_columns(headers, role)
    cells = _cells(text)
    return cells[columns[0]] if len(columns) == 1 and len(cells) == len(headers) else None


def table_equipment(text, headers):
    tag = _table_role(text, headers, 'equipment')
    return None if tag is None else entities(tag) if _ENTITY.fullmatch(tag) else {}


def table_component(text, headers):
    return _table_role(text, headers, 'component')


def line_contexts(text, *, markdown):
    """Describe structure independently of the durable line-based passage locator."""
    lines = text.split('\n')
    sections, contexts = [], []
    table_header, table_line, table_rows, fence, label = (), None, 0, None, ''
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
            table_header, table_line, label, block_line = (), None, '', None
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
            table_header, table_line, label, masked_heading_subject, block_line = (), None, '', False, None
            contexts.append(LineContext(tuple(sections), '', searchable=False))
            continue

        ancestor = next((item.title for item in reversed(sections) if entities(item.title)), '')
        if table_line == number - 1:
            contexts.append(LineContext(tuple(sections), '', searchable=False))
            continue
        cells, next_cells = _cells(line), _cells(following)
        if '|' in line and next_cells and all(_ROW_DIVIDER.fullmatch(cell) for cell in next_cells):
            normalized = {' '.join(cell.casefold().split()) for cell in cells}
            table_header = cells if (len(cells) == len(next_cells) == len(normalized) and all(cells)
                                     and all(len(_role_columns(cells, role)) <= 1 for role in _TABLE_ROLES)) else ()
            table_line, table_rows, label, block_line = number, 0, '', None
            contexts.append(LineContext(tuple(sections), '', searchable=False))
            continue
        if table_line is not None and '|' in line:
            table_rows += 1
            valid = bool(table_header and len(cells) == len(table_header) and table_rows <= MAX_TABLE_ROWS
                         and table_equipment(line, table_header) != {}
                         and not all(_ROW_DIVIDER.fullmatch(cell) for cell in cells))
            contexts.append(LineContext(tuple(sections), ancestor, table_header, searchable=valid,
                                        block_line=table_line, block_kind='table', table_header_line=table_line))
            continue
        table_header, table_line = (), None
        if line.startswith('|') or line.endswith('|') or line.count('|') >= 2:
            contexts.append(LineContext(tuple(sections), '', searchable=False))
            continue
        direct = entities(line)
        if direct:
            # An explicitly different entity inside a section makes later bare lines ambiguous.
            if ancestor and not set(direct) <= set(entities(ancestor)):
                masked_heading_subject = True
            label = line if _ENTITY.fullmatch(line.rstrip(':').strip()) else ''
        elif not line:
            label, block_line = '', None
        subject = label or ('' if masked_heading_subject else ancestor)
        kind = ('list' if _LIST.match(line) or
                (block_line is not None and block_kind == 'list' and raw.startswith((' ', '\t'))) else 'paragraph')
        if line and (block_line is None or block_kind != kind):
            block_line, block_kind = number, kind
        contexts.append(LineContext(tuple(sections), subject,
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
    source_unit: str
    column: int | None = None


def _measurement(raw, low, high, unit, start, end, *, temperature=False):
    source_unit = unit
    if re.fullmatch(_TORQUE_UNIT, unit, re.I):
        if unit == 'nm':  # The lowercase SI symbol is a nanometre.
            return None
        unit = 'n·m' if unit.casefold().startswith('n') else 'lbf·ft'
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
        return Measurement(raw, low, high, unit, dimension, False, start, end, source_unit)
    try:
        validate_absolute_temperature(low, canonical)
    except ValueError:
        return None
    dimension = local_unit_dimension(canonical)
    if canonical.startswith('Δ'):
        dimension = 'temperature_difference'
    return Measurement(raw, low, high, canonical, dimension, True, start, end, source_unit)


def measurements(text, table_header=(), *, topic=''):
    if table_header:
        cells = _pipe_cells(text)
        if len(cells) != len(table_header):
            return ()
        values = []
        for column, (cell, header) in enumerate(zip(cells, table_header)):
            if any(column in _role_columns(table_header, role) for role in _TABLE_ROLES):
                continue
            declarations = tuple(_HEADER_UNIT.finditer(header))
            if len(declarations) > 1:
                continue
            locally = measurements(cell.text, topic=header)
            if declarations:
                unit = declarations[0]['unit']
                declared_unit = _measurement('', Decimal(0), Decimal(0), unit, 0, 0,
                                             temperature='temperature' in quantities(header))
                if declared_unit is None or any(value.unit != declared_unit.unit for value in locally):
                    continue
                if match := _NUMBER_RANGE.fullmatch(cell.text):
                    low, high = Decimal(match['low']), Decimal(match['high'] or match['low'])
                    if low <= high and (value := _measurement(
                            f'{cell.text} ({unit} in column header)', low, high, unit, 0, len(cell.text),
                            temperature='temperature' in quantities(header))):
                        locally = (value,)
            values.extend(replace(value, start=cell.start + value.start, end=cell.start + value.end, column=column)
                          for value in locally)
        return tuple(values)
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
    return tuple(values)


def conditions(text):
    return {match.lastgroup for match in _CONDITION.finditer(text)}


def quantities(text):
    return {match.lastgroup for match in _QUANTITY.finditer(text)}


def _value_context(text, value, table_header):
    """Return the value's clause or cell and any declared column label."""
    left, right, header = 0, len(text), ''
    if value.column is not None:
        cell = _pipe_cells(text)[value.column]
        left, right, header = cell.start, cell.end, table_header[value.column]
    for separator in _CLAUSE_BREAK.finditer(text, left, right):
        if separator.end() <= value.start:
            left = separator.end()
        elif separator.start() >= value.end:
            right = separator.start()
            break
    return text[left:right], header, left


def measurement_conditions(text, value, table_header=()):
    clause, header, _ = _value_context(text, value, table_header)
    row = _table_role(text, table_header, 'condition') if header else None
    if row is not None and not conditions(row):
        return None
    return conditions(clause) | conditions(header) | conditions(row or '')


def effective_conditions(text, value, table_header, headings):
    tags = measurement_conditions(text, value, table_header)
    if tags is None or any(len(tags & group) > 1 for group in _CONDITION_GROUPS):
        return None
    return merge_conditions(*(conditions(title) for title in headings), tags)


def fact_evidence(fact, text, table_header, headings, values, qualified):
    """Keep only evidence with the requested value type and an attributable subject."""
    if fact.attribute == 'unrecognized':
        return False, [], frozenset()
    if fact.attribute == 'requirements':
        return bool(not table_header and _REQUIREMENT_STATEMENT.search(text)), [], frozenset()
    heading_component = next((item for heading in reversed(headings) if (item := fact_components(heading))), frozenset())

    def matching_components(label):
        actual = fact_components(label) or heading_component
        if fact.components and (len(actual) != 1 or not actual <= fact.components):
            return None
        return actual

    if fact.attribute == 'procedure':
        if table_header:
            return False, [], frozenset()
        for clause in _CLAUSE_BREAK.split(text):
            if _PROCEDURE_ACTION.search(clause) and not _NON_FACT.search(clause):
                if (component := matching_components(clause)) is not None:
                    return True, qualified, component
        return False, [], frozenset()

    heading_fields = next((fields for heading in reversed(headings) if (fields := _fact_markers(heading))), set())
    contexts = {value: _value_context(text, value, table_header) for value in values}
    value_counts = Counter((context[2], value.dimension) for value, context in contexts.items())
    matched, components = [], set()
    for value in qualified:
        if fact.attribute not in ('limit', 'alarm') and value.dimension != fact.dimension:
            continue
        clause, header, position = contexts[value]
        subject = f'{header} {clause}'
        if _NON_FACT.search(subject):
            continue
        if fact.bound and not any(_BOUND.search(part) or _BOUND_CONDITIONS & conditions(part)
                                  for part in (subject, *headings)):
            continue
        component_text = f'{subject} {table_component(text, table_header) or ""}' if header else subject
        component = matching_components(component_text)
        if component is None:
            continue
        markers = _fact_markers(header or subject)
        prefix = clause[:value.start - position]
        if fact.attribute == 'time' and not markers and not header and _REPETITION.search(prefix):
            markers.add('time')
        if (not header and fact.attribute == value.dimension
                and (markers == {'alarm'} or not markers and _OPERATING_OBSERVATION.search(prefix))):
            markers.add(value.dimension)
        if (not markers and not header
                and _VALUE_ONLY.fullmatch(clause[:value.start - position] + clause[value.end - position:])):
            markers = heading_fields
        labeled_bound = (fact.attribute == 'limit' and value.dimension in markers
                         and any(_BOUND_CONDITIONS & conditions(part) for part in (subject, *headings)))
        if fact.attribute not in markers and not labeled_bound:
            continue
        if fact.inspection and not _INSPECTION.search(f'{subject} {" ".join(headings)}'):
            continue
        # Markers in a shared clause cannot disambiguate two same-dimension values.
        if value_counts[position, value.dimension] > 1:
            continue
        matched.append(value)
        components.update(component)
    return bool(matched), matched, frozenset(components)


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
