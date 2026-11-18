"""Document intent and read-only plan actions for an authorized offline request."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.agent.document_presentation import display_number
from backend.agent.planner import PlanStep, StepBinding
from backend.multimodal.document_evidence import header_unit
from backend.multimodal.document_reasoning import Citation, ComparisonResult, ReasoningError, TableResult, TableSelection, _header_key
from backend.multimodal.file_analysis import _ROW_IDENTIFIERS
from backend.tools.registry import ToolDefinition, ToolResult


_PASSAGE = re.compile(
    r'^(?:(?:find|locate|show) (?:the )?(?:section|passage|paragraph) '
    r'(?:discussing|about|mentioning|containing)|what does the document say about|which paragraph contains) (.+)$',
    re.I,
)
_TABLE = re.compile(r'^(?:calculate |which (?:equipment|pump|item|readings|values) |how much higher )', re.I)
_CROSS_SOURCE = r'[1-4](?: table \d{1,4}(?: on page \d{1,4})?| worksheet "[^"]+")?'
_COMPARISON = re.compile(
    rf'^(?:compare (?P<name>.+?) across (?:these two reports|source (?P<left>{_CROSS_SOURCE}) and source (?P<right>{_CROSS_SOURCE}))'
    r'|summarize the verified differences)(?: using (?P<column>.+?))?(?: by (?P<identifier>.+))?$', re.I,
)
_SOURCE = re.compile(r' in (?:(source [0-9]+)|"([^"]+)"|([^ ]+\.(?:pdf|docx|xlsx)))$', re.I)
_TABLE_POSITION = re.compile(r' in (?:the )?table(?: (\d{1,4}))?(?: on page (\d{1,4}))?$', re.I)
_WORKSHEET = re.compile(r' in worksheet (?:"([^"]+)"|([^"\r\n]+))$', re.I)
_EVIDENCE_REQUEST = re.compile(r'^(?:show|include) the source evidence(?: for (?:this|these) findings?)?$', re.I)

_DESCRIPTIONS = {
    'document_passage': 'Find supporting passages',
    'document_table': 'Calculate from the selected table',
    'document_compare': 'Compare verified source readings',
    'document_synthesize': 'Explain the verified findings',
}


def document_plan_steps(objective: str) -> list[PlanStep]:
    if not isinstance(objective, str) or not 1 <= len(objective) <= 520 or '\n' in objective or '\r' in objective:
        raise ReasoningError('INVALID_QUERY', 'Provide one or two short document questions.')
    clauses = re.split(r'\s*[,;]\s*then\s+', objective.strip().rstrip('?.!'), flags=re.I)
    if len(clauses) > 1 and _EVIDENCE_REQUEST.fullmatch(clauses[-1]):
        clauses.pop()
    if len(clauses) > 2 or any(not 1 <= len(clause) <= 256 for clause in clauses):
        raise ReasoningError('INVALID_QUERY', 'Provide one or two short document questions.')

    steps = []
    for clause in clauses:
        if _PASSAGE.fullmatch(clause):
            tool = 'document_passage'
        elif _COMPARISON.fullmatch(clause):
            tool = 'document_compare'
        elif _TABLE.match(clause):
            tool = 'document_table'
        else:
            raise ReasoningError('UNSUPPORTED_INTENT', 'Ask for a passage, a table calculation or a supported comparison in this request.')
        steps.append(PlanStep(tool_name=tool, description=_DESCRIPTIONS[tool], arguments={'question': clause}))
    bindings = {key: StepBinding(step_id=step.id, field='handle')
                for key, step in zip(('finding', 'next_finding'), steps)}
    steps.append(PlanStep(tool_name='document_synthesize', description=_DESCRIPTIONS['document_synthesize'], bindings=bindings))
    return steps


class _Question(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    question: str = Field(min_length=1, max_length=256)


class _Receipt(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    handle: str = Field(pattern=r'^[0-9a-f]{32}$')


class _Findings(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    finding: str = Field(pattern=r'^[0-9a-f]{32}$')
    next_finding: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


@dataclass(frozen=True)
class DocumentAnswer:
    text: str = field(repr=False)
    citations: tuple[Citation, ...] = field(repr=False)
    findings: tuple[_Finding, ...] = field(repr=False)


@dataclass(frozen=True)
class _Finding:
    result: tuple[Citation, ...] | TableResult | ComparisonResult

    def citations(self) -> tuple[Citation, ...]:
        if isinstance(self.result, TableResult):
            result = self.result
            counts = (result.calculation,) if result.calculation else tuple(calc for _, calc in result.groups)
            return _unique((*result.matches, *(cite for calc in counts for cite in calc.citations)))
        if isinstance(self.result, ComparisonResult):
            return (*self.result.headers, *(cite for row in self.result.rows for cite in row.citations))
        return self.result


def _unique(citations):
    return tuple({id(citation): citation for citation in citations}.values())


def _where(citation: Citation) -> str:
    location = citation.location
    parts = [citation.filename]
    if location.page is not None:
        parts.append(f'page {location.page}')
    if location.paragraph is not None:
        parts.append(f'paragraph {location.paragraph}')
    if location.table is not None:
        parts.append(f'table {location.table}, row {location.row}, column {location.column}')
    if location.sheet is not None:
        parts.append(f'worksheet {location.sheet}, cell {location.cell}')
    return ', '.join(parts)


def _explain(finding: _Finding) -> str:
    result = finding.result
    if isinstance(result, tuple):
        return '\n'.join(['Verified supporting passages:', *(f'[{_where(cite)}] “{cite.value}”' for cite in result)])
    if isinstance(result, ComparisonResult):
        unit_a, unit_b = (header_unit(header.value) for header in result.headers[-2:])
        lines = [f'Compared {len(result.rows)} matched record(s). Output is the second reading minus the first.']
        lines.extend(f'[{_where(header)}] Measurement column: {header.value}' for header in result.headers[-2:])
        for row in result.rows[:8]:
            a, b = row.citations[-2:]
            # Text cells may already include their original unit.
            lines.append(f'{str(row.identifier)[:128]}: [{_where(a)}] {a.value!s} ({unit_a}); '
                         f'[{_where(b)}] {b.value!s} ({unit_b}); calculated difference {display_number(row.difference, result.unit)}.')
        if len(result.rows) > 8:
            lines.append('Showing the first eight comparisons. All supporting cells are available in the citations.')
        return '\n'.join(lines)

    calc = result.calculation
    label = {'max': 'Maximum', 'min': 'Minimum', 'count': 'Valid numeric count', 'above': 'Readings over the threshold'}.get(
        result.operation, result.operation.capitalize())
    prefix = 'approximately ' if calc.approximate else ''
    lines = [f'{label}: {prefix}{display_number(calc.value, calc.unit)}.']
    if result.matches:
        lines.extend(f'[{_where(cite)}] Matched record or cell: {str(cite.value)[:128]}' for cite in result.matches[:8])
    lines.append('Source cells used:')
    lines.extend(f'[{_where(cite)}] {str(cite.value)[:128]}' for cite in calc.citations[:8])
    if len(calc.citations) > 8:
        lines.append('Showing the first eight source cells. All supporting cells are available in the citations.')
    if calc.missing or calc.nonnumeric or calc.formulas:
        lines.append(f'Excluded cells: {calc.missing} missing, {calc.nonnumeric} nonnumeric, {calc.formulas} formula.')
    return '\n'.join(lines)


class DocumentActions:
    """Fixed plan contracts, backed by one R8.43 evidence scope and no global registration."""

    def __init__(self, scope):
        self.scope = scope
        self._query_sources = ()
        self._synthesis = None
        self._latest_handle = None
        self._receipts = {}
        self.contracts = {
            name: ToolDefinition(name, _DESCRIPTIONS[name], schema, handler, result_schema=_Receipt,
                                 result_verifier=self._verifier(name))
            for name, schema, handler in (
                ('document_passage', _Question, self._passage),
                ('document_table', _Question, self._table),
                ('document_compare', _Question, self._compare),
                ('document_synthesize', _Findings, self._synthesize),
            )
        }

    def close(self):
        self._receipts.clear()
        self.contracts.clear()
        self._synthesis = self._latest_handle = None

    async def execute(self, name, arguments):
        contract = self.contracts[name]
        self._query_sources = ()
        finding = await contract.execute_fn(contract.input_schema.model_validate(arguments))
        if name != 'document_synthesize':
            if not isinstance(finding, _Finding):
                raise ReasoningError('CITATION_UNVERIFIED', 'The document finding could not be verified.')
            self.scope.consume_finding(finding.result, *self._query_sources)
        elif self._synthesis is None or finding is not self._synthesis:
            raise ReasoningError('CITATION_UNVERIFIED', 'The document synthesis could not be verified.')
        self._synthesis = None
        citations = finding.citations() if isinstance(finding, _Finding) else finding.citations
        self.scope.verify_citations(*citations)
        handle = uuid4().hex
        self._receipts[handle] = (name, dict(arguments), finding)
        self._latest_handle = handle
        return ToolResult(name, True, {'handle': handle})

    def _read_receipt(self, handle):
        entry = self._receipts.get(handle)
        if entry is None:
            raise ReasoningError('DEPENDENCY_UNAVAILABLE', 'The required document finding could not be verified.')
        finding = entry[2]
        citations = finding.citations() if isinstance(finding, _Finding) else finding.citations
        self.scope.verify_citations(*citations)
        return entry

    def _verifier(self, name):
        def verify(arguments, output):
            entry = self._receipts.get(output['handle'])
            return output['handle'] == self._latest_handle and entry is not None and entry[0] == name and entry[1] == arguments
        return verify

    def answer(self, *handles):
        findings = [self._read_receipt(handle)[2] for handle in handles]
        if not all(isinstance(finding, _Finding) for finding in findings):
            raise ReasoningError('DEPENDENCY_UNAVAILABLE', 'The required document finding could not be verified.')
        citations = _unique(cite for finding in findings for cite in finding.citations())
        answer = DocumentAnswer('\n\n'.join(_explain(finding) for finding in findings), citations, tuple(findings))
        self.scope.verify_citations(*citations)
        return answer

    def completed_answer(self, handle):
        name, _, result = self._read_receipt(handle)
        if name != 'document_synthesize':
            raise ReasoningError('DEPENDENCY_UNAVAILABLE', 'A verified document explanation was unavailable.')
        return result

    async def _synthesize(self, args):
        handles = (args.finding, args.next_finding) if args.next_finding is not None else (args.finding,)
        self._synthesis = self.answer(*handles)
        return self._synthesis

    def _select(self, question, *, worksheet=None):
        sources = self.scope.sources()
        match = _SOURCE.search(question)
        if match:
            ordinal, quoted, unquoted = match.groups()
            if ordinal is not None:
                index = int(ordinal.split()[1]) - 1
                sources = sources[index:index + 1] if index >= 0 else ()
            else:
                name = (quoted or unquoted).casefold()
                sources = tuple(source for source in sources if source.filename.casefold() == name)
            question = question[:match.start()]
        elif worksheet is not None:
            sources = tuple(source for source in sources if source.format == 'xlsx')
        if len(sources) > 1:
            raise ReasoningError('AMBIGUOUS_SOURCE', 'Select a single original file or source number.')
        if not sources:
            raise ReasoningError('SOURCE_UNAVAILABLE', 'The selected source is unavailable in this request.')
        return sources[0], question

    async def _passage(self, args):
        source, question = self._select(args.question)
        terms = _PASSAGE.fullmatch(question)
        if terms is None:
            raise ReasoningError('INVALID_QUERY', 'Specify the subject of one supporting passage.')
        self._query_sources = (source.source_id,)
        return _Finding(self.scope.find_passages(source.source_id, terms[1]))

    async def _table(self, args):
        # Strip an explicit source first. Worksheet syntax alone can identify a
        # single workbook among other formats, but never one of two workbooks.
        suffix = _SOURCE.search(args.question)
        worksheet = _WORKSHEET.search(args.question[:suffix.start()] if suffix else args.question)
        source, query = self._select(args.question, worksheet=worksheet)
        position = _TABLE_POSITION.search(query)
        selection = TableSelection(source.source_id)
        if worksheet:
            if source.format != 'xlsx':
                raise ReasoningError('WORKSHEET_UNAVAILABLE', 'Select a workbook for a worksheet question.')
            selection = TableSelection(source.source_id, sheet=worksheet[1] or worksheet[2])
            query = query[:worksheet.start()]
        elif position:
            table, page = (int(value) if value is not None else None for value in position.groups())
            selection = TableSelection(source.source_id, page=page, table=table)
            query = query[:position.start()]
        if re.search(r'\b(?:highest|lowest) reading$', query, re.I):
            columns = self.scope.columns(selection)
            if all(_header_key(column) != 'reading' for column in columns):
                measurements = [column for column in columns if _header_key(column) not in _ROW_IDENTIFIERS]
                if len(measurements) != 1:
                    raise ReasoningError('COLUMN_UNAVAILABLE', 'Name one measurement column for the requested reading.')
                query = query[:-len('reading')] + measurements[0]
        self._query_sources = (source.source_id,)
        return _Finding(self.scope.ask_table(selection, query))

    async def _compare(self, args):
        match = _COMPARISON.fullmatch(args.question)
        if match is None:
            raise ReasoningError('INVALID_QUERY', 'Specify two documents and a supported comparison.')
        sources = self.scope.sources()
        if match['left'] is None and len(sources) != 2:
            raise ReasoningError('AMBIGUOUS_SOURCE', 'Select two sources for the comparison.')
        left, right = (self._comparison_source(sources, spec) for spec in (match['left'] or '1', match['right'] or '2'))
        if left.source_id == right.source_id:
            raise ReasoningError('INVALID_SELECTION', 'Select two independently extracted documents.')
        first = {_header_key(column): column for column in self.scope.columns(left)}
        shared = first.keys() & {_header_key(column) for column in self.scope.columns(right)}
        identifiers = shared & _ROW_IDENTIFIERS
        identifier = match['identifier']
        if identifier is None:
            if len(identifiers) != 1:
                raise ReasoningError('AMBIGUOUS_IDENTIFIER', 'Specify a shared text identifier column.')
            identifier, = identifiers
        column = match['column']
        if column is None:
            candidates = [first[key] for key in shared - identifiers - {_header_key(identifier)} if header_unit(first[key])]
            if len(candidates) != 1:
                raise ReasoningError('COLUMN_UNAVAILABLE', 'Specify one shared measurement column.')
            column = candidates[0]
        self._query_sources = (left.source_id, right.source_id)
        result = self.scope.compare(left, right, identifier=identifier, column=column, name=match['name'])
        if not result.rows:
            raise ReasoningError('IDENTIFIER_UNAVAILABLE', 'No records could be aligned by the specified identifier.')
        return _Finding(result)

    @staticmethod
    def _comparison_source(sources, clause):
        ordinal, selector = int(clause[0]), clause[1:]
        if ordinal > len(sources):
            raise ReasoningError('SOURCE_UNAVAILABLE', 'Select an available source number.')
        source_id = sources[ordinal - 1].source_id
        if worksheet := _WORKSHEET.fullmatch(' in' + selector):
            return TableSelection(source_id, sheet=worksheet[1])
        if position := _TABLE_POSITION.fullmatch(' in' + selector):
            table, page = (int(value) if value is not None else None for value in position.groups())
            return TableSelection(source_id, page=page, table=table)
        return TableSelection(source_id)
