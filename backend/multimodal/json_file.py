"""Bounded JSON records projected onto the existing tabular analyzer."""

from dataclasses import dataclass
from decimal import Decimal, DecimalException
import json
import re
import unicodedata

from backend.multimodal.csv_file import MAX_CSV_CELL_CHARS, MAX_CSV_COLUMNS, MAX_CSV_ROWS, is_csv_number
from backend.multimodal.text_file import TextFileError, decode_text_file

MAX_JSON_DEPTH = 6
MAX_JSON_OBJECTS = 64
_MAX_MAGNITUDE = Decimal('1e12')
_UNITS = {
    'temperature_c': ('temperature', '°C'), 'temperature_f': ('temperature', '°F'),
    'vibration_mm_s': ('vibration', 'mm/s'), 'pressure_bar': ('pressure', 'bar'),
    'flow_l_min': ('flow', 'L/min'), 'length_mm': ('length', 'mm'), 'length_cm': ('length', 'cm'),
    'length_m': ('length', 'm'), 'duration_s': ('duration', 's'), 'duration_min': ('duration', 'min'),
}


def _invalid(message: str, code: str = 'JSON_INVALID') -> TextFileError:
    return TextFileError(message, code=code)


def _validate_depth(text: str) -> None:
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise _invalid('JSON supports at most six levels of nesting.', 'JSON_LIMIT')
        elif char in ']}':
            depth -= 1


def _string(value: str, *, key: bool = False) -> str:
    if len(value) > MAX_CSV_CELL_CHARS:
        raise _invalid('JSON keys and text values are limited to 128 characters.', 'JSON_LIMIT')
    if any(unicodedata.category(char).startswith('C') for char in value) or (key and not value.strip()):
        raise _invalid('JSON keys must be nonempty and JSON text must use printable characters.')
    return value


@dataclass(frozen=True)
class _Number:
    raw: str
    decimal: str


def _number(token: str) -> _Number:
    if len(token) > 40:
        raise _invalid('The JSON number exceeds the supported precision or magnitude.', 'JSON_NUMBER')
    try:
        number = Decimal(token)
    except DecimalException:
        raise _invalid('The JSON number exceeds the supported precision or magnitude.', 'JSON_NUMBER') from None
    if (not number.is_finite() or number.copy_abs() > _MAX_MAGNITUDE
            or not -12 <= number.as_tuple().exponent <= 24 or len(number.as_tuple().digits) > 24):
        raise _invalid('The JSON number exceeds the supported precision or magnitude.', 'JSON_NUMBER')
    return _Number(token, format(number, 'f'))


def _nonfinite(_token: str):
    raise _invalid('JSON numbers must be finite.', 'JSON_NUMBER')


def _pointer(path: tuple[str, ...]) -> str:
    return ''.join('/' + part.replace('~', '~0').replace('/', '~1') for part in path)


def _records(root) -> tuple[tuple[str, ...], bool, list[dict]]:
    path = ()
    while isinstance(root, dict) and len(root) == 1:
        key, value = next(iter(root.items()))
        if isinstance(value, (dict, list)):
            path += (key,)
            root = value
        else:
            break
    if isinstance(root, dict) and root:
        return path, False, [root]
    if isinstance(root, list):
        if len(root) > MAX_CSV_ROWS:
            raise _invalid('JSON supports up to 32 records.', 'JSON_LIMIT')
        if root and all(isinstance(record, dict) and record for record in root):
            return path, True, root
    raise _invalid('Use a nonempty JSON object or an array of nonempty records under a single named path.', 'JSON_SHAPE')


def _fields(record: dict, prefix: tuple[str, ...] = ()):
    for key, value in record.items():
        path = (*prefix, key)
        if isinstance(value, dict) and value and not prefix:
            yield from _fields(value, path)
        elif isinstance(value, str):
            yield path, _string(value)
        elif isinstance(value, _Number) or value is None:
            yield path, value
        else:
            raise _invalid('Records support numbers, strings, null and one level of named subfields.', 'JSON_SHAPE')


def _header(path: tuple[str, ...]) -> str:
    name = path[-1]
    if mapped := _UNITS.get(name):
        parameter, unit = mapped
        return f'{parameter} ({unit})'
    return name


def _tabular_value(value) -> str:
    if isinstance(value, _Number):
        return value.decimal
    if isinstance(value, str):
        # Numeric-looking strings stay nonnumeric in the existing CSV numeric predicate.
        return json.dumps(value, ensure_ascii=False) if is_csv_number(value.strip()) else value
    return ''


@dataclass(frozen=True)
class JsonTable:
    headers: list[str]
    rows: list[list[str]]
    fields: tuple[tuple[str, ...], ...]
    originals: tuple[dict, ...]
    path: tuple[str, ...]
    array: bool

    def record_path(self, row: int) -> tuple[str, ...]:
        return (*self.path, str(row - 1)) if self.array else self.path

    def record(self, row: int) -> str:
        pointer = _pointer(self.record_path(row))
        return f'JSON record index {row - 1} at {pointer or "(root object)"}'

    def field(self, index: int) -> str:
        original = self.fields[index][-1]
        header = self.headers[index]
        unit_mapping = f' ({header})' if original != header else ''
        return f'Field {json.dumps(original, ensure_ascii=False)}{unit_mapping}'

    def value(self, row: int, index: int) -> tuple[str, str]:
        path = self.fields[index]
        pointer = _pointer((*self.record_path(row), *path))
        record = self.originals[row - 1]
        if path not in record:
            return pointer, '(field absent)'
        value = record[path]
        literal = value.raw if isinstance(value, _Number) else json.dumps(value, ensure_ascii=False)
        return pointer, literal

    def evidence(self, row: int, index: int) -> str:
        pointer, literal = self.value(row, index)
        return f'{pointer} = {literal}'

    def preview(self, first_row: int, count: int) -> list[list[str]]:
        return [[self.value(row, col)[1] for col in range(len(self.headers))]
                for row in range(first_row, first_row + count)]


@dataclass(frozen=True)
class JsonEvidence:
    table: JsonTable
    source_number: int

    def lines(self, first_row: int, count: int) -> list[str]:
        return [f'Source {self.source_number} (JSON)',
                f'Selected JSON record indices: {first_row - 1}-{first_row + count - 2} (zero-based)']


def parse_json_file(data: bytes, media_type: str) -> JsonTable:
    kind, separator, params = media_type.partition(';')
    if kind.strip().lower() not in ('application/json', 'text/plain', 'application/octet-stream'):
        raise TextFileError('Upload a UTF-8 JSON file.', status=415, code='JSON_FILE_TYPE')
    text = decode_text_file(data, 'text/plain' + separator + params)
    _validate_depth(text)
    objects = 0

    def object_pairs(pairs):
        nonlocal objects
        objects += 1
        if objects > MAX_JSON_OBJECTS or len(pairs) > MAX_CSV_COLUMNS:
            raise _invalid('The JSON object or property limit was exceeded.', 'JSON_LIMIT')
        result = {}
        for key, value in pairs:
            _string(key, key=True)
            if key in result:
                raise _invalid('JSON property names must be unique within each object.', 'JSON_DUPLICATE_KEY')
            result[key] = value
        return result

    try:
        root = json.loads(text, object_pairs_hook=object_pairs, parse_int=_number,
                          parse_float=_number, parse_constant=_nonfinite)
    except (json.JSONDecodeError, RecursionError):
        raise _invalid('The JSON syntax is invalid.') from None
    path, array, records = _records(root)
    originals = tuple(dict(_fields(record)) for record in records)
    fields = tuple(dict.fromkeys(field for record in originals for field in record))
    if not 1 <= len(fields) <= MAX_CSV_COLUMNS:
        raise _invalid('JSON supports up to eight distinct tabular fields.', 'JSON_LIMIT')
    headers = [_header(field) for field in fields]
    # A natural-language metric must always map back to one field path.
    names = [tuple(re.findall(r'\w+', re.split(r'\s*[\[(]', header, maxsplit=1)[0].casefold())) for header in headers]
    if not all(names) or len(set(names)) != len(names):
        raise _invalid('Use unique field labels for the selected records.', 'JSON_AMBIGUOUS_FIELD')
    rows = [[_tabular_value(record.get(field)) for field in fields] for record in originals]
    return JsonTable(headers, rows, fields, originals, path, array)
