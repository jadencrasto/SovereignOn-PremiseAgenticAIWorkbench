"""Deterministic reasoning over parser evidence inside one authorized offline request.

The caller supplies independently authenticated request scope. No route, durable
history, retrieval service, or model receives these results.
"""

from __future__ import annotations

import re
from collections import Counter, deque
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import PurePath
from time import monotonic
from typing import Callable

from backend.multimodal.document_evidence import (
    DocumentEvidence, ExtractedCell, ExtractedTable, ExtractionLimit, ExtractionWarning,
    SourceLocation, header_unit, valid_source_filename,
)
from backend.multimodal.file_analysis import _ROW_IDENTIFIERS, _number, _rounded_average, _words
from backend.multimodal.spreadsheet import Sheet, Spreadsheet, parse_spreadsheet
from backend.multimodal.workflow import MAX_INPUTS, MAX_TOTAL_BYTES
from backend.rag.ingest import DocumentParser
from backend.tools.unit_conversion import unit_scale_factors

MAX_ROWS = 128
MAX_CITATIONS = 2048
_PASSAGE_PREFIX = re.compile(
    r'^(?:find (?:the )?(?:section|passage|paragraph) (?:about|mentioning|containing)|'
    r'what does the document say about|which paragraph contains|show the source text (?:for|supporting))\s+', re.I,
)
_HEADER_SUFFIX = re.compile(r'\s*(?:\([^()]+\)|\[[^\[\]]+\])$')
_ALLOWED_OPERATIONS = {'sum', 'average', 'min', 'max', 'count', 'difference', 'above'}


def document_inputs(files) -> tuple[tuple[str, bytes], ...]:
    """Snapshot validated byte inputs for authorization and extraction."""
    if (not isinstance(files, (list, tuple)) or not 1 <= len(files) <= MAX_INPUTS
            or any(not isinstance(item, (list, tuple)) or len(item) != 2 for item in files)):
        raise ReasoningError('INVALID_INPUT', 'Supply bounded document bytes with original filenames.')
    inputs = tuple(tuple(item) for item in files)
    if (any(not valid_source_filename(name) or not isinstance(content, bytes) for name, content in inputs)
            or sum(len(content) for _, content in inputs) > MAX_TOTAL_BYTES):
        raise ReasoningError('INVALID_INPUT', 'Supply bounded document bytes with original filenames.')
    return inputs


class ReasoningError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Source:
    source_id: str
    filename: str
    format: str
    locations: tuple[SourceLocation, ...]
    warnings: tuple[ExtractionWarning, ...]


@dataclass(frozen=True)
class Citation:
    source_id: str
    filename: str
    location: SourceLocation
    value: object = field(repr=False)
    data_type: str = 'text'
    number_format: str | None = None
    heading_level: int | None = None


@dataclass(frozen=True)
class TableSelection:
    source_id: str
    page: int | None = None
    table: int | None = None
    sheet: str | None = None
    header_row: int = 1
    rows: tuple[int, ...] | None = None


@dataclass(frozen=True)
class TableQuery:
    operation: str
    column: str
    identifier: str | None = None
    names: tuple[str, ...] = ()
    threshold: Decimal | int | None = None
    unit: str | None = None
    group_by: str | None = None


@dataclass(frozen=True)
class Calculation:
    value: Decimal | int | None
    unit: str | None
    citations: tuple[Citation, ...]
    approximate: bool = False
    missing: int = 0
    nonnumeric: int = 0
    formulas: int = 0


@dataclass(frozen=True)
class TableResult:
    operation: str
    calculation: Calculation | None
    matches: tuple[Citation, ...] = ()
    groups: tuple[tuple[str, Calculation], ...] = ()


@dataclass(frozen=True)
class Comparison:
    identifier: str
    citations: tuple[Citation, ...]
    difference: Fraction | None

    @property
    def conflict(self) -> bool:
        """Two recorded measurements differ after unit conversion."""
        return self.difference is not None and self.difference != 0


@dataclass(frozen=True)
class ComparisonResult:
    rows: tuple[Comparison, ...]
    unit: str | None
    headers: tuple[Citation, ...]


def _header_key(value: str) -> str:
    value = _HEADER_SUFFIX.sub('', value) if header_unit(value) else value
    return _words(value)


@dataclass(frozen=True)
class _Grid:
    source: Source
    table: ExtractedTable | None
    cells: dict[tuple[int, int], ExtractedCell]
    headers: tuple
    header_row: int
    rows: tuple[int, ...]

    def column(self, name: str) -> int:
        key = _header_key(name) if isinstance(name, str) and len(name) <= 256 else ''
        matches = [i for i, header in enumerate(self.headers, 1) if key and _header_key(header) == key]
        if len(matches) != 1:
            raise ReasoningError('COLUMN_UNAVAILABLE', 'Select one unambiguous table column.')
        return matches[0]

    def unit(self, column: int) -> str | None:
        return header_unit(self.headers[column - 1])

    def citation(self, row: int, column: int) -> Citation | None:
        if self.table is None:
            cell = self.cells.get((row, column))
            if cell is None:
                return None
            return Citation(self.source.source_id, self.source.filename, cell.location, cell.value,
                            cell.data_type, cell.number_format)
        values = self.table.rows[row - 1]
        value = values[column - 1] if column <= len(values) else None
        if value is None:
            return None
        return Citation(self.source.source_id, self.source.filename, self.table.cell_location(row, column), value)


def _decimal(citation: Citation | None, unit: str | None) -> tuple[Decimal | None, str]:
    if citation is None:
        return None, 'missing'
    value = citation.value
    if citation.data_type == 'f':
        return None, 'formulas'
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, 'missing'
    if type(value) in (int, float) and citation.data_type == 'n':
        result = Decimal(str(value))
        return (result, '') if result.is_finite() else (None, 'nonnumeric')
    if citation.location.sheet is None and isinstance(value, str) and len(value) <= 128:
        value = value.strip()
        number = _number(value)
        if number is not None:
            return number, ''
        pieces = value.split(maxsplit=1)
        number_text, written_unit = pieces if len(pieces) == 2 else (pieces[0], '')
        number = _number(number_text)
        if number is not None and header_unit(f'Reading ({written_unit.strip()})'):
            if written_unit.strip() != unit:
                raise ReasoningError('INCOMPATIBLE_UNITS', 'A cell unit does not match the selected column.')
            return number, ''
    return None, 'nonnumeric'


def _matching_excerpt(text: str, terms: set[str], check) -> str | None:
    relevant = deque()
    counts: Counter = Counter()
    for index, match in enumerate(re.finditer(r'\w+', text)):
        if index % 128 == 0:
            check()
        word = match[0].casefold()
        if word not in terms:
            continue
        relevant.append((word, match.start()))
        counts[word] += 1
        while match.end() - relevant[0][1] > 400:
            first, _ = relevant.popleft()
            counts[first] -= 1
            if counts[first] == 0:
                del counts[first]
            if not relevant:
                break
        if counts.keys() == terms:
            start = relevant[0][1]
            # Prefer the full original short passage when it already fits.
            return text if len(text) <= 400 else text[start:start + 400]
    return None


class _DocumentRequest:
    def __init__(self, owner, conversation, allowed, cancelled):
        self._scope = (owner, conversation)
        self._allowed = allowed
        self._cancelled = cancelled
        self._deadline = monotonic() + 15
        self._documents: dict[str, DocumentEvidence | Spreadsheet] = {}
        self._issued: dict[int, Citation] = {}
        self._closed = False

    def _close(self):
        self._closed = True
        self._issued.clear()
        self._documents.clear()

    def _check(self):
        if self._closed:
            raise ReasoningError('REQUEST_CLOSED', 'The document request has closed.')
        if self._cancelled is not None:
            try:
                cancelled = self._cancelled()
            except Exception:
                cancelled = True
            if cancelled is not False:
                self._close()
                raise ReasoningError('CANCELLED', 'Document analysis was cancelled.')
        if monotonic() >= self._deadline:
            self._close()
            raise ReasoningError('TIMEOUT', 'Document analysis exceeded its time budget.')
        try:
            allowed = self._allowed(*self._scope)
        except Exception:
            allowed = False
        if allowed is not True:
            self._close()
            raise ReasoningError('AUTHORIZATION_UNAVAILABLE', 'Document request authorization is unavailable.')

    def _load(self, files):
        for filename, content in document_inputs(files):
            self._check()
            suffix = PurePath(filename).suffix.lower()
            if suffix not in ('.pdf', '.docx', '.xlsx'):
                raise ReasoningError('UNSUPPORTED_FORMAT', 'Select a supported offline document format.')
            try:
                document = (parse_spreadsheet(filename, content) if suffix == '.xlsx'
                            else DocumentParser.parse(filename, content).evidence)
            except ExtractionLimit:
                raise ReasoningError('EXTRACTION_LIMIT', 'The document exceeded an extraction limit.') from None
            except (ValueError, RuntimeError):
                raise ReasoningError('EXTRACTION_FAILED', 'The document could not be extracted safely.') from None
            self._check()
            if document is None or document.source_id in self._documents:
                raise ReasoningError('SOURCE_UNAVAILABLE', 'The extracted source could not be verified.')
            self._documents[document.source_id] = document

    @staticmethod
    def _source(document) -> Source:
        locations = (tuple(SourceLocation(sheet=sheet.name) for sheet in document.sheets)
                     if isinstance(document, Spreadsheet) else tuple(table.location for table in document.tables))
        return Source(document.source_id, document.filename, document.format, locations, document.warnings)

    def sources(self) -> tuple[Source, ...]:
        self._check()
        return tuple(self._source(document) for document in self._documents.values())

    def _document(self, source_id):
        self._check()
        if not isinstance(source_id, str) or source_id not in self._documents:
            raise ReasoningError('SOURCE_UNAVAILABLE', 'Select a source from this document request.')
        return self._documents[source_id]

    def _publish(self, citations):
        self._check()
        records = {id(citation): citation for citation in citations}
        if len(self._issued) + len(records) > MAX_CITATIONS:
            raise ReasoningError('CITATION_LIMIT', 'Document request exceeds its citation budget.')
        self._issued.update(records)

    def verify_citations(self, *citations: Citation) -> None:
        """Require original immutable handles issued inside this still-authorized request."""
        self._check()
        if not citations or any(id(citation) not in self._issued for citation in citations):
            raise ReasoningError('CITATION_UNVERIFIED', 'Select original citations from this document request.')

    def find_passages(self, source_id: str, question: str, *, limit: int = 3) -> tuple[Citation, ...]:
        document = self._document(source_id)
        if not isinstance(document, DocumentEvidence) or not document.blocks:
            raise ReasoningError('PASSAGES_UNAVAILABLE', 'No extracted passages are available for this source.')
        if not isinstance(question, str) or len(question) > 256 or type(limit) is not int or not 1 <= limit <= 5:
            raise ReasoningError('INVALID_QUERY', 'Use a short search and request at most five passages.')
        prompt = _PASSAGE_PREFIX.sub('', question.strip())
        terms = set(_words(prompt).split())
        if not 1 <= len(terms) <= 12:
            raise ReasoningError('INVALID_QUERY', 'Supply up to twelve distinct passage search terms.')
        results = []
        for block in document.blocks:
            self._check()
            quote = _matching_excerpt(block.text, terms, self._check)
            if quote is not None:
                results.append(Citation(document.source_id, document.filename, block.location, quote,
                                        heading_level=block.heading_level))
                if len(results) == limit:
                    break
        if not results:
            raise ReasoningError('NO_MATCH', 'No extracted passage matches those terms.')
        self._publish(results)
        return tuple(results)

    def _grid(self, selection: TableSelection) -> _Grid:
        if not isinstance(selection, TableSelection):
            raise ReasoningError('INVALID_SELECTION', 'Select a source table or worksheet.')
        document = self._document(selection.source_id)
        cells = {}
        table = None
        if isinstance(document, Spreadsheet):
            if selection.page is not None or selection.table is not None:
                raise ReasoningError('INVALID_SELECTION', 'Select a worksheet by its exact name.')
            if selection.sheet is None and len(document.sheets) != 1:
                raise ReasoningError('AMBIGUOUS_TABLE', 'Select an exact worksheet name for this workbook.')
            try:
                sheet: Sheet = document.sheets[0] if selection.sheet is None else document.worksheet(selection.sheet)
            except ValueError:
                raise ReasoningError('WORKSHEET_UNAVAILABLE', 'The selected worksheet is unavailable.') from None
            rows = sheet.rows
            cells = {(cell.location.row, cell.location.column): cell for cell in sheet.cells}
            if len(cells) != len(sheet.cells):
                raise ReasoningError('TABLE_UNAVAILABLE', 'Worksheet coordinates are ambiguous.')
        else:
            if (selection.sheet is not None or (document.format != 'pdf' and selection.page is not None)
                    or any(index is not None and (type(index) is not int or index < 1)
                           for index in (selection.page, selection.table))):
                raise ReasoningError('INVALID_SELECTION', 'Select a valid original page or table position.')
            matching = [candidate for candidate in document.tables
                        if (selection.page is None or candidate.location.page == selection.page)
                        and (selection.table is None or candidate.location.table == selection.table)]
            if len(matching) > 1:
                raise ReasoningError('AMBIGUOUS_TABLE', 'Select an original table position for this source.')
            if not matching:
                raise ReasoningError('TABLE_UNAVAILABLE', 'No verified table exists at that source position.')
            table = matching[0]
            rows = table.rows
        header_row = selection.header_row
        if type(header_row) is not int or not 1 <= header_row <= len(rows):
            raise ReasoningError('HEADER_UNAVAILABLE', 'Select an existing header row.')
        if selection.rows is None:
            if len(rows) - header_row > MAX_ROWS:
                raise ReasoningError('ROW_LIMIT', 'Select at most 128 original data rows.')
            offsets = tuple(range(header_row + 1, len(rows) + 1))
        else:
            offsets = selection.rows
            if (not isinstance(offsets, tuple) or not 1 <= len(offsets) <= MAX_ROWS
                    or any(type(row) is not int or not header_row < row <= len(rows) for row in offsets)
                    or len(set(offsets)) != len(offsets)):
                raise ReasoningError('INVALID_SELECTION', 'Select unique original data rows after the header.')
        headers = rows[header_row - 1]
        while headers and headers[-1] is None:
            headers = headers[:-1]
        grid = _Grid(self._source(document), table, cells, headers, header_row, offsets)
        if (not headers or any(not isinstance(header, str) or not _header_key(header) for header in headers)
                or len({_header_key(header) for header in headers}) != len(headers)
                or any((cite := grid.citation(header_row, column)) is None or cite.data_type == 'f'
                       for column in range(1, len(headers) + 1))
                or any(any(value is not None for value in rows[row - 1][len(headers):]) for row in offsets)):
            raise ReasoningError('HEADER_UNAVAILABLE', 'Use a header row with unique text labels for the selected data.')
        return grid

    def _identifiers(self, table: _Grid, column: int) -> dict[str, tuple[int, Citation]]:
        identifiers = {}
        for row in table.rows:
            self._check()
            citation = table.citation(row, column)
            if (citation is None or citation.data_type == 'f' or not isinstance(citation.value, str)
                    or not citation.value.strip()):
                raise ReasoningError('IDENTIFIER_UNAVAILABLE', 'Each selected row needs a verified text identifier.')
            name = citation.value.strip().casefold()
            if name in identifiers:
                raise ReasoningError('AMBIGUOUS_IDENTIFIER', 'A selected identifier occurs in more than one row.')
            identifiers[name] = (row, citation)
        return identifiers

    def _calculate(self, table, operation, column, rows, headers, threshold=None):
        operands, values, invalid = {}, [], Counter()
        for row in rows:
            self._check()
            citation = table.citation(row, column)
            value, cause = _decimal(citation, table.unit(column))
            if value is None:
                invalid[cause] += 1
            else:
                operands[row] = citation
                values.append(value)
        if operation != 'count' and not values:
            code = 'FORMULA_ONLY' if invalid['formulas'] and not invalid['nonnumeric'] else 'NO_NUMERIC_EVIDENCE'
            raise ReasoningError(code, 'The selected cells supply no verified numeric values.')
        chosen = ()
        approximate = False
        with localcontext() as context:
            context.prec = 700  # Includes arithmetic spanning Excel's floating-point exponent range.
            if operation == 'average':
                result, approximate = _rounded_average(values)
            elif operation == 'difference':
                if len(values) != 2:
                    raise ReasoningError('NO_NUMERIC_EVIDENCE', 'A difference needs two verified numeric operands.')
                result = values[1] - values[0]
            elif operation == 'above':
                chosen = tuple(row for row, value in zip(operands, values) if value > threshold)
                result = len(chosen)
            elif operation in ('min', 'max'):
                result = min(values) if operation == 'min' else max(values)
                chosen = tuple(row for row, value in zip(operands, values) if value == result)
            else:
                result = len(values) if operation == 'count' else sum(values, Decimal(0))
        unit = None if operation in ('count', 'above') else table.unit(column)
        calculation = Calculation(result, unit, (*headers, *operands.values()), approximate,
                                  invalid['missing'], invalid['nonnumeric'], invalid['formulas'])
        return calculation, chosen, operands

    def analyze(self, selection: TableSelection, query: TableQuery) -> TableResult:
        return self._analyze(self._grid(selection), query)

    def columns(self, selection: TableSelection) -> tuple[str, ...]:
        """Return original headers from one unambiguous, validated table."""
        return self._grid(selection).headers

    def read_cell(self, selection: TableSelection, row: int, column: int) -> Citation:
        """Return an observed original cell. Formula cells retain their formula type."""
        table = self._grid(selection)
        if type(row) is not int or row not in table.rows or type(column) is not int or not 1 <= column <= len(table.headers):
            raise ReasoningError('INVALID_CELL', 'Select an original cell within the chosen data rows.')
        citation = table.citation(row, column)
        if citation is None or citation.value is None or citation.value == '':
            raise ReasoningError('CELL_UNAVAILABLE', 'No source value was extracted for that cell.')
        self._publish((citation,))
        return citation

    def _analyze(self, table: _Grid, query: TableQuery) -> TableResult:
        if (not isinstance(query, TableQuery) or not isinstance(query.operation, str)
                or query.operation not in _ALLOWED_OPERATIONS):
            raise ReasoningError('INVALID_QUERY', 'Select a supported numeric table operation.')
        column = table.column(query.column)
        headers = [table.citation(table.header_row, column)]
        column_unit = table.unit(column)
        if query.operation != 'count' and column_unit is None and _HEADER_SUFFIX.search(table.headers[column - 1]):
            raise ReasoningError('INCOMPATIBLE_UNITS', 'The selected column uses an unverified unit or qualifier.')
        specified_unit = query.unit or header_unit(query.column)
        if any(unit is not None and unit != column_unit for unit in (query.unit, header_unit(query.column))):
            raise ReasoningError('INCOMPATIBLE_UNITS', 'Use the verified column unit for this calculation.')
        if query.operation == 'above':
            if (type(query.threshold) not in (int, Decimal) or not Decimal(query.threshold).is_finite()
                    or Decimal(query.threshold).copy_abs() > Decimal('1e308')):
                raise ReasoningError('INVALID_QUERY', 'Supply a finite threshold in the selected column unit.')
            if column_unit is not None and specified_unit is None:
                raise ReasoningError('INCOMPATIBLE_UNITS', 'Specify the verified unit for this threshold.')
        rows = table.rows
        labels = {}
        if query.identifier is not None:
            label_column = table.column(query.identifier)
            labels = self._identifiers(table, label_column)
            headers.append(table.citation(table.header_row, label_column))
        if query.names:
            if (not isinstance(query.names, tuple) or len(query.names) > MAX_ROWS
                    or any(not isinstance(name, str) or len(name) > 256 for name in query.names)):
                raise ReasoningError('IDENTIFIER_UNAVAILABLE', 'Select unique identifiers from the table.')
            names = tuple(name.strip().casefold() for name in query.names)
            if len(set(names)) != len(names) or any(name not in labels for name in names):
                raise ReasoningError('IDENTIFIER_UNAVAILABLE', 'Select unique identifiers from the table.')
            rows = tuple(labels[name][0] for name in names)
        label_by_row = {row: citation for row, citation in labels.values()}
        group_results = []
        matches = ()
        calculation = None
        issued = []
        if query.group_by is None:
            if query.operation == 'difference' and len(rows) != 2:
                raise ReasoningError('INVALID_SELECTION', 'Select two original rows for a difference.')
            calculation, chosen, numeric = self._calculate(table, query.operation, column, rows, headers, query.threshold)
            row_labels = tuple(label_by_row[row] for row in numeric if row in label_by_row)
            if row_labels:
                calculation = replace(calculation, citations=(*calculation.citations, *row_labels))
            matches = tuple(label_by_row.get(row, numeric[row]) for row in chosen)
            issued.extend(calculation.citations)
        else:
            if query.operation not in ('sum', 'average', 'count') or query.identifier is not None:
                raise ReasoningError('INVALID_QUERY', 'Use sum, average or count over one explicit group column.')
            group_column = table.column(query.group_by)
            headers.append(table.citation(table.header_row, group_column))
            groups = {}
            for row in rows:
                group = table.citation(row, group_column)
                if group is None or group.data_type == 'f' or not isinstance(group.value, str) or not group.value.strip():
                    raise ReasoningError('IDENTIFIER_UNAVAILABLE', 'Each selected row needs a verified text group.')
                key = group.value.strip().casefold()
                groups.setdefault(key, []).append((row, group))
            for members in groups.values():
                result, _, _ = self._calculate(table, query.operation, column, [r for r, _ in members],
                                               [*headers, *(group for _, group in members)])
                group_results.append((members[0][1].value, result))
                issued.extend(result.citations)
        self._publish((*issued, *matches))
        return TableResult(query.operation, calculation, matches, tuple(group_results))

    def ask_table(self, selection: TableSelection, question: str) -> TableResult:
        """Translate a small set of deterministic questions into explicit table operations."""
        table = self._grid(selection)
        if not isinstance(question, str) or len(question) > 256:
            raise ReasoningError('INVALID_QUERY', 'Use a short supported table question.')
        prompt = question.strip().rstrip('?.')
        labels = [header for header in table.headers if _header_key(header) in _ROW_IDENTIFIERS]
        identifier = labels[0] if len(labels) == 1 else None
        if match := re.fullmatch(r'which (?:pump|equipment|item) has the (highest|lowest) (.+)', prompt, re.I):
            if identifier is None:
                raise ReasoningError('AMBIGUOUS_IDENTIFIER', 'Use an explicit identifier column for this table.')
            query = TableQuery('max' if match[1].casefold() == 'highest' else 'min', match[2], identifier)
        elif match := re.fullmatch(r'calculate (?:the )?(average|sum|count)(?: of)? (.+)', prompt, re.I):
            query = TableQuery(match[1].casefold(), match[2])
        elif match := re.fullmatch(r'which (?:readings|values)(?: for (.+?))? exceed ([-.\d]+)\s*(.*)', prompt, re.I):
            column, threshold, unit = match.groups()
            candidates = [header for header in table.headers if unit and header_unit(header) == unit]
            if column is None and len(candidates) != 1:
                raise ReasoningError('COLUMN_UNAVAILABLE', 'Name the measurement column for this threshold.')
            query = TableQuery('above', column or candidates[0], identifier, threshold=_number(threshold), unit=unit or None)
        elif match := re.fullmatch(r"how much higher is (.+?)['\u2019]s (.+?) than (.+)['\u2019]s", prompt, re.I):
            if identifier is None:
                raise ReasoningError('AMBIGUOUS_IDENTIFIER', 'Use an explicit identifier column for this table.')
            query = TableQuery('difference', match[2], identifier, (match[3], match[1]))
        else:
            raise ReasoningError('INVALID_QUERY', 'Use a supported table question or an explicit calculation.')
        return self._analyze(table, query)

    def compare(self, left: TableSelection, right: TableSelection, *, identifier: str,
                column: str | None = None, name: str | None = None) -> ComparisonResult:
        first, second = self._grid(left), self._grid(right)
        if first.source.source_id == second.source.source_id:
            raise ReasoningError('INVALID_SELECTION', 'Select two independently extracted documents.')
        label_a, label_b = first.column(identifier), second.column(identifier)
        a, b = self._identifiers(first, label_a), self._identifiers(second, label_b)
        shared = a.keys() & b.keys()
        if name is not None:
            if not isinstance(name, str) or not 1 <= len(name) <= 256:
                raise ReasoningError('INVALID_QUERY', 'Specify one exact record identifier.')
            key = name.strip().casefold()
            if key not in shared:
                raise ReasoningError('IDENTIFIER_UNAVAILABLE', 'The requested record was not verified in both sources.')
            shared = {key}
        headers = [first.citation(first.header_row, label_a), second.citation(second.header_row, label_b)]
        col_a = col_b = None
        unit_a = unit_b = None
        scale_a = scale_b = Decimal(1)
        if column is not None:
            col_a, col_b = first.column(column), second.column(column)
            unit_a, unit_b = first.unit(col_a), second.unit(col_b)
            if unit_a is None or unit_b is None or (header_unit(column) not in (None, unit_a)):
                raise ReasoningError('INCOMPATIBLE_UNITS', 'Both measurement columns need compatible explicit units.')
            if unit_a != unit_b:
                try:
                    scale_a, scale_b = unit_scale_factors(unit_a, unit_b)
                except ValueError:
                    raise ReasoningError('INCOMPATIBLE_UNITS', 'The measurement columns use incompatible units.') from None
            headers.extend((first.citation(first.header_row, col_a), second.citation(second.header_row, col_b)))
        rows = []
        for name in shared:
            self._check()
            row_a, label_cite_a = a[name]
            row_b, label_cite_b = b[name]
            citations = [label_cite_a, label_cite_b]
            difference = None
            if col_a is not None:
                cite_a, cite_b = first.citation(row_a, col_a), second.citation(row_b, col_b)
                value_a, _ = _decimal(cite_a, unit_a)
                value_b, _ = _decimal(cite_b, unit_b)
                if value_a is None or value_b is None:
                    raise ReasoningError('NO_NUMERIC_EVIDENCE', 'Both matched measurements need verified numeric values.')
                difference = Fraction(value_b) * Fraction(scale_b) / Fraction(scale_a) - Fraction(value_a)
                citations.extend((cite_a, cite_b))
            rows.append((row_a, Comparison(label_cite_a.value, tuple(citations), difference)))
        result = tuple(comparison for _, comparison in sorted(rows))
        self._publish((*headers, *(cite for comparison in result for cite in comparison.citations)))
        return ComparisonResult(result, unit_a, tuple(headers))


@contextmanager
def open_document_reasoning(files, *, owner_id: str, conversation_id: str,
                            allowed: Callable[[str, str], bool], cancelled: Callable[[], bool] | None = None):
    """Open an isolated byte-only request under the caller's authoritative scope check."""
    if (not all(isinstance(value, str) and value.strip() for value in (owner_id, conversation_id))
            or not callable(allowed) or (cancelled is not None and not callable(cancelled))):
        raise ReasoningError('AUTHORIZATION_UNAVAILABLE', 'Document request authorization is unavailable.')
    request = _DocumentRequest(owner_id, conversation_id, allowed, cancelled)
    try:
        request._check()
        request._load(files)
        del files
        yield request
    finally:
        request._close()
