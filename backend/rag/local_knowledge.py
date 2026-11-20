"""Bounded lexical retrieval for the explicitly registered local_dev corpus."""

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from itertools import zip_longest
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing import Annotated, Literal

from backend.local_history import _safe_path, _strict_json, write_local_snapshot
from backend.multimodal.text_file import MAX_TEXT_FILE_BYTES, TextFileError, decode_text_file, text_filename
from backend.rag.grounding import Evidence, build_context, validate_answer
from backend.rag.local_documents import (
    MAX_DOCUMENT_BYTES, DocumentProjection, available_formats,
)
from backend.rag.local_relevance import (
    LineContext, RequestedFact, condition_matches, conditions, effective_conditions, entities, fact_evidence, interval_in, lexical_text,
    line_contexts, measurement_conditions, measurement_match, measurements, merge_conditions, quantities,
    requested_fact, resolve_entities, table_component, table_equipment,
)

MAX_SOURCES = 8
MAX_CORPUS_BYTES = 8192
MAX_QUESTION = 600
MAX_PASSAGES = 6
MAX_QUESTION_PARTS = 4
MAX_CONTEXT_PASSAGES = 8
RETRIEVAL_MODE = 'bounded_lexical'
KNOWLEDGE_FIELD = 'local_knowledge_v1'
KNOWLEDGE_PRODUCER = 'local-verified-knowledge'
INSUFFICIENT = 'The available local sources do not establish an answer to this question.'
_LOCATOR_VERSION = 'local-text-lines-v1'
_MAX_SNAPSHOT = 128 * 1024
_INTENT = re.compile(
    r'^\s*(?:according to|from|using|use)\s+(?:(?:my|the|our|your)\s+)?(?:registered\s+)?'
    r'local\s+(?:knowledge(?:\s+sources)?|sources)\b[\s,:-]*', re.IGNORECASE,
)
_COMPARE = re.compile(r'\b(?:compare|contrast|differences? between|differ|conflict|disagree|both sources)\b',
                      re.IGNORECASE)
_QUESTION_START = r'(?:what|which|where|when|who|why|how)\b'
_PART_BOUNDARY = re.compile(
    rf'(?:[?;,]\s*(?:and\s+)?|\s+and\s+)(?={_QUESTION_START})', re.IGNORECASE,
)
_SECTION_QUERY = re.compile(r'^(?:which|what|where)\b.*\b(?:section|heading)\b', re.IGNORECASE)
_OPERATING_STATES = {'normal', 'alarm', 'warning', 'startup', 'shutdown', 'steady'}
_CONDITION_COMPARISON = re.compile(
    r'^(?:compare|contrast) (?:the )?(normal|alarm|warning|startup|shutdown) and '
    r'(normal|alarm|warning|startup|shutdown) (.+)', re.IGNORECASE,
)
_TRAILING_STATE = re.compile(
    r'\b(?:under|during|in) (normal|alarm|warning|startup|shutdown|steady) (?:conditions?|operation|mode)$', re.I,
)
_STOP = set(('a an and are as at be by can compare contrast could did do does difference differences differ conflict '
             'disagree both each for from how in is it its me of on or should show so tell than that the their them '
             'there these this to use was were what when where which who why will with would my our your source '
             'sources note notes document documents say says about between according find please').split())
_METRIC = re.compile(
    r'\b(?:limit|pressure|temperature|interval|inspect|recommend|capacity|tolerance|flow|torque|speed|voltage|threshold)\w*\b',
    re.I,
)
_OPERATING_HEADINGS = {'operating envelope', 'operating limits'}
_DOCUMENT_HEADING = re.compile(r'\b(?:handbook|manual|amendment)$')
_SINGULAR_PUMP = re.compile(r'\b(?:the|this|that|a)\s+pump\b', re.I)
_EVIDENCE_ID = Annotated[str, StringConstraints(pattern=r'^C[0-9a-f]{32}_[1-6]$')]
_SOURCE_ID = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{32}$')]
KnowledgeSourceIds = Annotated[list[_SOURCE_ID], Field(min_length=1, max_length=MAX_SOURCES)]
_DIGEST = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
_TURN_ID = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')]


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_id: _SOURCE_ID
    revision: int = Field(ge=1, le=2**31 - 1)
    digest: _DIGEST


class Citation(Revision):
    passage: int = Field(ge=0, lt=1200)
    excerpt_digest: _DIGEST
    match_reasons: list[Literal['equipment', 'section', 'exact_measurement', 'equivalent_measurement', 'within_range']] | None = (
        Field(default=None, max_length=5)
    )


class ScopedSource(Revision):
    label: str = Field(min_length=1, max_length=180)


class KnowledgeScope(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    corpus_id: _SOURCE_ID
    mode: Literal['all', 'selected']
    sources: list[ScopedSource] = Field(max_length=MAX_SOURCES)

    @model_validator(mode='after')
    def validate_sources(self):
        ids = {source.source_id for source in self.sources}
        if len(ids) != len(self.sources) or (self.mode == 'selected' and not ids):
            raise ValueError('Invalid local source scope')
        return self

    def matches_selection(self, source_ids):
        return (source_ids is None if self.mode == 'all'
                else source_ids is not None and set(source_ids) == {source.source_id for source in self.sources})


class EvidenceField(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    citation: int = Field(ge=1, le=MAX_CONTEXT_PASSAGES)
    attribute: str = Field(pattern=r'^[a-z][a-z_]{0,29}$')
    start: int = Field(ge=0, lt=320)
    end: int = Field(ge=1, le=320)
    value_digest: _DIGEST


class EvidenceCoverage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    state: Literal['supported', 'incomplete', 'absent', 'conflict']
    citations: list[Annotated[int, Field(ge=1, le=MAX_CONTEXT_PASSAGES)]] = Field(max_length=MAX_PASSAGES)
    fields: list[EvidenceField] | None = Field(default=None, max_length=MAX_CONTEXT_PASSAGES * 4)


class KnowledgeReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    corpus_id: _SOURCE_ID
    index_version: str = Field(min_length=1, max_length=64)
    turn_id: _TURN_ID
    attempt: int = Field(ge=1, le=2**31 - 1)
    request_digest: _DIGEST
    dependencies: list[Revision] = Field(max_length=MAX_SOURCES)
    citations: list[Citation] = Field(max_length=MAX_CONTEXT_PASSAGES)
    scope: KnowledgeScope | None = None
    coverage: list[EvidenceCoverage] | None = Field(default=None, min_length=1, max_length=MAX_QUESTION_PARTS)

    @model_validator(mode='after')
    def validate_coverage(self):
        if self.scope is None and self.coverage is None:
            return self  # Previously signed L9 receipt.
        if self.scope is None or self.coverage is None or self.scope.corpus_id != self.corpus_id:
            raise ValueError('Incomplete local evidence receipt')
        scoped = {(s.source_id, s.revision, s.digest) for s in self.scope.sources}
        if any((d.source_id, d.revision, d.digest) not in scoped for d in self.dependencies):
            raise ValueError('Evidence outside the admitted scope')
        used = set()
        for part in self.coverage:
            numbers = set(part.citations)
            if (len(numbers) != len(part.citations) or any(n > len(self.citations) for n in numbers)
                    or (part.state == 'absent' and numbers)
                    or (part.state in ('supported', 'conflict') and not numbers)):
                raise ValueError('Invalid evidence coverage')
            fields = part.fields or ()
            if (any(field.citation not in numbers or field.start >= field.end for field in fields)
                    or len({(field.citation, field.start, field.end) for field in fields}) != len(fields)):
                raise ValueError('Invalid evidence field')
            used.update(numbers)
        if used != set(range(1, len(self.citations) + 1)):
            raise ValueError('Citations missing from evidence coverage')
        return self


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    passages: list[_EVIDENCE_ID] = Field(max_length=MAX_PASSAGES)


class KnowledgeConflict(ValueError):
    """A stale update or duplicate does not change the registered corpus."""


class KnowledgeQuestionError(ValueError):
    """Question exceeds the supported length or multipart budget."""


def knowledge_question(message: str) -> str | None:
    match = _INTENT.match(message)
    return message[match.end():].strip() if match else None


def question_parts(question):
    """Split explicit interrogative clauses or a simple two-topic 'what' request."""
    if not question or len(question) > MAX_QUESTION:
        raise KnowledgeQuestionError
    parts = tuple(part.strip(' ,;?') for part in _PART_BOUNDARY.split(question))
    if not all(parts) or len(parts) > MAX_QUESTION_PARTS:
        raise KnowledgeQuestionError
    if len(parts) == 1 and (comparison := _CONDITION_COMPARISON.fullmatch(parts[0])):
        return tuple(f'{state} {comparison[3]}' for state in (comparison[1], comparison[2]))
    if len(parts) == 1 and not _COMPARE.search(question):
        match = re.fullmatch(r'(what (?:is|are) )(.+?) and (.+)', parts[0], re.IGNORECASE)
        if match:
            left, right = match[2], match[3]
            facts = [requested_fact(f'{match[1]}{item}', measurements(item)) for item in (left, right)]
            typed = all(fact and fact.attribute != 'unrecognized' for fact in facts)
            if typed or (len(_terms(left)) >= 2 and len(_terms(right)) >= 2):
                tags = entities(f'{left} {right}')
                if typed and len(tags) == 1:
                    tag = next(iter(tags.values()))
                    left = left if entities(left) else f'{left} for {tag}'
                    right = right if entities(right) else f'{right} for {tag}'
                    if all(fact.attribute not in ('procedure', 'alarm') for fact in facts):
                        left_states = conditions(left) & _OPERATING_STATES
                        right_states = conditions(right)
                        if len(left_states) == 1 and not right_states:
                            right += f' under {next(iter(left_states))} conditions'
                        elif not conditions(left) and (shared := _TRAILING_STATE.search(match[3])):
                            left += f' under {shared[1]} conditions'
                return (match[1] + left, match[1] + right)
    return parts


def _terms(text):
    text = re.sub(r'\btightening moment\b', 'torque', text.casefold())
    return {'inspect' if word in {'inspection', 'inspections', 'inspecting'} else
            'interval' if word in {'frequency', 'frequencies', 'cadence'} else
            'normal' if word == 'normally' else
            word.removesuffix('s') if len(word) > 4 and not word.endswith('ss') else word
            for word in re.findall(r'[^\W_]+', text) if word not in _STOP}


def _encode(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Passage:
    index: int
    line: int
    start: int
    end: int
    text: str
    context: LineContext

    def equipment(self):
        if self.context.table_header:
            tagged = table_equipment(self.text, self.context.table_header)
            return entities(self.context.entity_context) if tagged is None else tagged
        return entities(self.text) or entities(self.context.entity_context)

    def values(self):
        return measurements(self.text, self.context.table_header,
                            topic=' '.join(section.title for section in self.context.sections))


def _passages(text, *, markdown):
    """Keep source lines and offsets. Oversized lines split on word boundaries."""
    offset, index = 0, 0
    contexts = line_contexts(text, markdown=markdown)
    for line_no, (line, context) in enumerate(zip(text.split('\n'), contexts), 1):
        stripped = line.strip()
        # Preserve the original passage ordinal and excerpt bytes for signed L9-L10 receipts.
        if stripped and not re.match(r'^#{1,6}\s+', stripped):
            start = len(line) - len(line.lstrip())
            end = len(line.rstrip())
            while start < end:
                stop = min(start + 320, end)
                if stop < end:
                    boundary = line.rfind(' ', start, stop)
                    if boundary > start:
                        stop = boundary
                yield Passage(index, line_no, offset + start, offset + stop, line[start:stop], context)
                index += 1
                start = stop
                while start < end and line[start].isspace():
                    start += 1
        offset += len(line) + 1


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    revision: int
    digest: str
    text: str
    passages: tuple[Passage, ...]
    document: DocumentProjection | None = None

    def reference(self):
        return {'source_id': self.id, 'revision': self.revision, 'digest': self.digest}

    def public(self):
        result = {**self.reference(), 'label': self.label, 'bytes': len(self.text.encode('utf-8')),
                  'passages': len(self.passages)}
        if self.document:
            result['document'] = self.document.public()
        return result

    def locator(self, line, column=None):
        if not self.document:
            return None
        loc = next((item for item in self.document.locators if item.line == line), None)
        return loc.public(self.document.format, column) if loc else None


@dataclass(frozen=True)
class RetrievalQuestion:
    terms: set[str]
    requested_entities: tuple[str, ...]
    clarification: str | None
    conditions: set[str]
    quantities: set[str]
    measurements: tuple
    fact: RequestedFact | None


def _match(question, passage, label):
    equipment = passage.equipment()
    if question.requested_entities and (len(equipment) != 1 or next(iter(equipment)) not in question.requested_entities):
        return None
    headings = tuple(section.title for section in passage.context.sections)
    path = ' '.join(headings)
    heading_conditions = merge_conditions(*(conditions(section.title) for section in passage.context.sections))
    body_conditions = conditions(passage.text)
    table = passage.context.table_header
    values = passage.values()
    qualified = [value for value in values
                 if (tags := effective_conditions(passage.text, value, table, headings)) is not None
                 and condition_matches(question.conditions, tags)]
    if question.conditions and not qualified:
        if values or not condition_matches(question.conditions, merge_conditions(heading_conditions, body_conditions)):
            return None
    components = frozenset()
    if question.fact:
        supported, qualified, components = fact_evidence(question.fact, passage.text, table, headings, values, qualified)
        if not supported:
            return None
    if question.quantities and values and not any(value.dimension in question.quantities for value in qualified):
        return None
    if question.quantities and any(value.dimension not in question.quantities for value in question.measurements):
        return None
    reasons = ['equipment'] if question.requested_entities else []
    for measure in question.measurements:
        match = next((reason for candidate in qualified if (reason := measurement_match(measure, candidate))), None)
        if match is None:
            return None
        if match not in reasons:
            reasons.append(match)
    if question.measurements:
        qualified = [value for value in qualified if any(
            measurement_match(measure, value) for measure in question.measurements)]
    # A broad query can return several explicitly separated readings, within a part-wide budget.
    if len(qualified) > 4:
        return None

    body = _terms(lexical_text(passage.text, values))
    context = _terms(path) | _terms(' '.join(table))
    query = question.terms
    body_hits = len(query & body)
    overlap = len(query & (body | context | _terms(label)))
    required = max(min(2, len(query)), (len(query) + 1) // 2)
    exact_section = len(query) >= 2 and query <= context
    technical = bool(question.requested_entities or question.measurements or question.fact)
    if query and not (exact_section or (overlap >= required and body_hits >= min(2, required))
                      or (technical and len(query & (body | context)) >= max(1, required - 1))):
        return None
    if query & context:
        reasons.append('section')
    leaf = passage.context.sections[-1].title if passage.context.sections else ''
    measurement_rank = 1 if 'within_range' in reasons else 2 if question.measurements else 0
    score = (measurement_rank, int(bool(question.conditions)),
             int(any(value.dimension in question.quantities for value in qualified)),
             int(exact_section), len(query & _terms(leaf)), body_hits, overlap)
    return score, reasons, components, qualified


def _source(source_id, label, revision, raw, media_type):
    ext = '.md' if label.lower().endswith('.md') else '.txt'
    text_filename(label, ext)
    if ext == '.md' and media_type.split(';', 1)[0].strip().lower() in ('text/markdown', 'text/x-markdown'):
        media_type = 'text/plain' + (';' + media_type.split(';', 1)[1] if ';' in media_type else '')
    text = decode_text_file(raw, media_type).replace('\r\n', '\n').replace('\r', '\n')
    passages = tuple(_passages(text, markdown=ext == '.md'))
    if not passages:
        raise TextFileError('Include some body text in the source.')
    return Source(source_id, label, revision, _digest(text), text, passages)


def _document_source(source_id, label, revision, document):
    text_filename(label, '.' + document.format)
    passages = tuple(_passages(document.text, markdown=True))
    locations = {item.line: item for item in document.locators}
    searchable = [part for part in passages if part.context.searchable]
    if not searchable:
        raise TextFileError('The document has no supported extractable evidence.')
    for part in searchable:
        location = locations.get(part.line)
        if (location is None or (location.width or 0) != len(part.context.table_header)):
            raise TextFileError('The document source positions could not be verified.')
    digest = hashlib.sha256(_encode(document.model_dump(exclude_none=True))).hexdigest()
    return Source(source_id, label, revision, digest, document.text, passages, document)


class LocalKnowledge:
    """Single-owner corpus attached only to the local application lifespan."""

    def __init__(self, owner, *, path=None, keys=None):
        self.owner = owner
        self.id = uuid5(NAMESPACE_URL, f'sovereignai-local-knowledge:{owner}').hex
        self.path = path
        self.keys = keys
        self._sources = {}
        if path is not None and _safe_path(path):
            self._sources = self._load()

    def _authorize(self, owner):
        if owner != self.owner:
            raise PermissionError('Local knowledge corpus unavailable')

    @staticmethod
    def _limits(sources):
        if len(sources) > MAX_SOURCES or sum(len(s.text.encode('utf-8')) for s in sources.values()) > MAX_CORPUS_BYTES:
            raise TextFileError('The local knowledge corpus is full.', status=413)

    def _mac(self, key, payload):
        return hmac.new(key, b'sovereignai-local-corpus-v1\0' + _encode(payload), hashlib.sha256).hexdigest()

    def _load(self):
        with self.path.open('rb') as file:
            raw = file.read(_MAX_SNAPSHOT + 1)
        try:
            if len(raw) > _MAX_SNAPSHOT:
                raise ValueError
            envelope = _strict_json(raw)
            payload = envelope['payload']
            key = self.keys.verification_key(envelope['key'])
            if (not key or not hmac.compare_digest(envelope['mac'], self._mac(key, payload))
                    or payload['owner'] != self.owner or payload['corpus'] != self.id):
                raise ValueError
            sources = {}
            for row in payload['sources']:
                ref = Revision.model_validate({key: row[key] for key in ('source_id', 'revision', 'digest')})
                if 'document' in row:
                    projection = DocumentProjection.model_validate(row['document'])
                    source = _document_source(ref.source_id, row['label'], ref.revision, projection)
                else:
                    source = _source(ref.source_id, row['label'], ref.revision, row['text'].encode('utf-8'), 'text/plain')
                if source.digest != ref.digest or source.id in sources:
                    raise ValueError
                sources[source.id] = source
            self._limits(sources)
            return sources
        except (KeyError, TypeError, ValueError, RecursionError):
            raise ValueError('Invalid durable local knowledge corpus') from None

    def _save(self, sources):
        self._limits(sources)
        if self.path is not None:
            payload = {'owner': self.owner, 'corpus': self.id, 'sources': [
                {**source.reference(), 'label': source.label, **(
                    {'document': source.document.model_dump(exclude_none=True)} if source.document
                    else {'text': source.text})}
                for source in sources.values()
            ]}
            key_id, key = self.keys.signing_key()
            encoded = _encode({'payload': payload, 'key': key_id, 'mac': self._mac(key, payload)})
            if len(encoded) > _MAX_SNAPSHOT:
                raise TextFileError('The local knowledge corpus is full.', status=413)
            write_local_snapshot(self.path, encoded)
        self._sources = sources

    def status(self, owner):
        self._authorize(owner)
        return {'mode': RETRIEVAL_MODE, 'retention': 'durable' if self.path else 'disposable',
                'max_sources': MAX_SOURCES, 'max_source_bytes': MAX_TEXT_FILE_BYTES,
                'document_formats': available_formats(), 'max_document_bytes': MAX_DOCUMENT_BYTES,
                'max_corpus_bytes': MAX_CORPUS_BYTES, 'sources': [s.public() for s in self._sources.values()]}

    def _existing(self, source_id, revision):
        source = self._sources[source_id]
        if source.revision != revision:
            raise KnowledgeConflict('The source has changed. Refresh the corpus and try again.')
        return source

    def register(self, owner, label, raw, media_type, *, source_id=None, revision=None):
        self._authorize(owner)
        return self._register(label, source_id, revision,
                              lambda sid, rev: _source(sid, label, rev, raw, media_type))

    def register_document(self, owner, label, projection, *, source_id=None, revision=None):
        self._authorize(owner)
        return self._register(label, source_id, revision,
                              lambda sid, rev: _document_source(sid, label, rev, projection))

    def _register(self, label, source_id, revision, build):
        old = self._existing(source_id, revision) if source_id else None
        if old and old.revision >= 2**31 - 1:
            raise KnowledgeConflict('The source has reached its revision limit.')
        source = build(old.id if old else uuid4().hex, old.revision + 1 if old else 1)
        if old and (source.label, source.digest) == (old.label, old.digest):
            return old.public()
        if any(current.id != source.id and (current.digest == source.digest
               or (current.document and source.document
                   and current.document.content_sha256 == source.document.content_sha256)
               or current.label.casefold() == source.label.casefold()) for current in self._sources.values()):
            raise KnowledgeConflict('This content or filename is already registered. Replace the existing source to reindex it.')
        self._save({**self._sources, source.id: source})
        return source.public()

    def remove(self, owner, source_id, revision):
        self._authorize(owner)
        self._existing(source_id, revision)
        self._save({key: source for key, source in self._sources.items() if key != source_id})

    def bind_scope(self, owner, source_ids):
        self._authorize(owner)
        if source_ids is not None and any(source_id not in self._sources for source_id in source_ids):
            raise KeyError('Local source unavailable')
        sources = [ScopedSource(**source.reference(), label=source.label) for source in self._sources.values()
                   if source_ids is None or source.id in source_ids]
        return KnowledgeScope(corpus_id=self.id, mode='all' if source_ids is None else 'selected', sources=sources)

    def check_scope(self, owner, scope):
        self._authorize(owner)
        if scope.corpus_id != self.id or any(not self._matches_revision(ref) for ref in scope.sources):
            raise KnowledgeConflict('Selected local sources changed. Submit a new question with current sources.')

    def _matches_revision(self, ref):
        source = self._sources.get(ref.source_id)
        return source is not None and source.revision == ref.revision and source.digest == ref.digest

    def workflow_pumps(self, owner, scope):
        self.check_scope(owner, scope)
        return sorted({tag for ref in scope.sources for passage in self._sources[ref.source_id].passages
                       if passage.context.searchable for tag in passage.equipment() if re.fullmatch(r'P[- ]?\d+', tag)})

    def _question(self, question, source_ids=None):
        known = {tag for source in self._sources.values() if source_ids is None or source.id in source_ids
                 for passage in source.passages if passage.context.searchable for tag in passage.equipment()}
        requested, ambiguous = resolve_entities(question, known)
        pumps = {tag.replace('-', '').replace(' ', '') for tag in known if re.match(r'^P[- ]?\d', tag)}
        clarification = None
        if ambiguous:
            clarification = 'Which equipment identifier spelling should I use? Enter the exact tag from the source.'
        elif not requested and _SINGULAR_PUMP.search(question) and len(pumps) > 1:
            clarification = 'Which pump should I use? Specify an equipment identifier from the selected sources.'
        values = measurements(question)
        if values and re.search(r'\bconvert\b', question, re.I) and any(not value.convertible for value in values):
            clarification = clarification or 'The registered converter cannot convert this measurement. Ask for the source value in its original unit.'
        query = _terms(lexical_text(question, values))
        navigation = bool(_SECTION_QUERY.search(question))
        if navigation:
            query -= {'section', 'heading', 'describe', 'contain', 'cover', 'mention'}
        if values:
            query -= {'equivalent', 'match', 'correspond', 'value', 'listed', 'specified', 'equal', 'equals'}
        fact = requested_fact(question, values, navigation=navigation)
        if fact and fact.attribute == 'requirements':
            query -= {'requirement', 'require', 'two', '2', 'policy', 'report', 'file', 'record',
                      'summarize', 'summarise', 'their', 'key', 'important', 'selected', 'registered'}
        return RetrievalQuestion(query, requested, clarification, conditions(question), quantities(question), values, fact)

    def retrieve(self, owner, question, source_ids=None):
        self._authorize(owner)
        if not question or len(question) > MAX_QUESTION:
            return ()
        target = self._question(question, source_ids)
        if (target.clarification or len(target.terms) > 32
                or not (target.terms or target.requested_entities or target.measurements
                        or target.fact and target.fact.attribute == 'requirements')):
            return ()
        ranked = []
        for position, source in enumerate(self._sources.values()):
            if source_ids is not None and source.id not in source_ids:
                continue
            matches = []
            for passage in source.passages:
                if passage.context.searchable and (match := _match(target, passage, source.label)):
                    score, _, _, _ = match
                    matches.append((score, passage))
            matches.sort(key=lambda row: (tuple(-value for value in row[0]), row[1].index))
            section_counts, block_counts = {}, {}
            for rank, (score, passage) in enumerate(matches):
                section_rank = section_counts.get(passage.context.sections, 0)
                section_counts[passage.context.sections] = section_rank + 1
                block_rank = block_counts.get(passage.context.block_line, 0)
                block_counts[passage.context.block_line] = block_rank + 1
                key = (-score[0], -score[1], -score[2], section_rank, block_rank, rank, tuple(-value for value in score[3:]),
                       position, passage.index)
                ranked.append((key, source, passage))
        ranked.sort(key=lambda row: row[0])
        if len(target.requested_entities) > 1:
            first = {tag: next((row for row in ranked if tag in row[2].equipment()), None)
                     for tag in target.requested_entities}
            reserved = [row for row in first.values() if row is not None]
            ranked = reserved + [row for row in ranked if row not in reserved]
        result, seen = [], set()
        for _, source, passage in ranked:
            key = (' '.join(passage.text.casefold().split()), tuple(passage.equipment()),
                   tuple(sorted(conditions(' '.join(s.title for s in passage.context.sections)))),
                   passage.context.table_header)
            if key in seen:
                continue
            seen.add(key)
            result.append(Evidence(source.id, source.label, f'{source.id}_chunk_{passage.index}', passage.index,
                                   passage.text, 0, source.revision, _LOCATOR_VERSION, RETRIEVAL_MODE))
            if len(result) == MAX_PASSAGES:
                break
        return tuple(result)

    def _allowed(self, owner, source_id, revision):
        self._authorize(owner)
        source = self._sources.get(source_id)
        return source is not None and source.revision == revision

    def contexts(self, owner, parts, scope):
        self.check_scope(owner, scope)
        selected = {ref.source_id for ref in scope.sources}
        ranked = [self.retrieve(owner, part, selected) for part in parts]
        allocated = [[] for _ in parts]
        remaining = MAX_CONTEXT_PASSAGES
        for row in zip_longest(*ranked):
            for group, passage in zip(allocated, row):
                if passage is not None and remaining:
                    group.append(passage)
                    remaining -= 1
            if not remaining:
                break
        return [build_context(group, allowed=lambda sid, rev: self._allowed(owner, sid, rev))[1]
                for group in allocated]

    def model_evidence(self, owner, refs):
        self.check_context(owner, refs)
        evidence = []
        for label, item in refs.items():
            passage = self._passage_for(item)
            evidence.append({'id': label, 'source': item.filename, 'passage': item.text,
                             'heading': ' / '.join(section.title for section in passage.context.sections)[:320],
                             'equipment': list(passage.equipment().values()),
                             'block_line': passage.context.block_line, 'block_kind': passage.context.block_kind,
                             'table_header': ' | '.join(passage.context.table_header)[:200]})
        return evidence

    def _passage_for(self, item):
        return self._sources[item.document_id].passages[item.chunk_index]

    def check_context(self, owner, refs):
        self._authorize(owner)
        for item in refs.values():
            if not self._allowed(owner, item.document_id, item.source_revision):
                raise KnowledgeConflict('Local sources changed while answering.')
            source = self._sources[item.document_id]
            if source.passages[item.chunk_index].text != item.text:
                raise KnowledgeConflict('Local sources changed while answering.')

    def public_receipt(self, owner, receipt):
        self._authorize(owner)
        receipt = KnowledgeReceipt.model_validate(receipt)
        if receipt.corpus_id != self.id or receipt.index_version != _LOCATOR_VERSION:
            return None
        if ((receipt.scope and not all(self._matches_revision(ref) for ref in receipt.scope.sources))
                or not all(self._matches_revision(dep) for dep in receipt.dependencies)):
            return None
        citations = []
        cited_passages = []
        deps = {(dep.source_id, dep.revision, dep.digest) for dep in receipt.dependencies}
        for number, ref in enumerate(receipt.citations, 1):
            if (ref.source_id, ref.revision, ref.digest) not in deps:
                return None
            source = self._sources[ref.source_id]
            if ref.passage >= len(source.passages):
                return None
            passage = source.passages[ref.passage]
            if ref.excerpt_digest != _digest(passage.text):
                return None
            citation = {**source.reference(), 'number': number, 'label': source.label,
                        'passage_id': f'{source.id}_chunk_{passage.index}', 'line': passage.line,
                        'start': passage.start, 'end': passage.end, 'excerpt': passage.text}
            citation['line_end'] = passage.line
            if source.document:
                locator = source.locator(passage.line)
                if locator is None:
                    return None
                citation['document_locator'] = locator
            if passage.context.sections:
                leaf = passage.context.sections[-1]
                citation.update(heading=source.text.split('\n')[leaf.line - 1].strip(), heading_line=leaf.line)
                citation['sections'] = [
                    {'id': f'{source.id}:r{source.revision}:line-{section.line}', 'title': section.title,
                     'line': section.line, 'level': section.level} for section in passage.context.sections
                ]
            if passage.context.table_header:
                citation['table_header'] = ' | '.join(passage.context.table_header)
            if equipment := passage.equipment():
                citation['equipment'] = list(equipment.values())
            if values := passage.values():
                citation['measurements'] = list(dict.fromkeys(value.raw for value in values))
            if ref.match_reasons:
                citation['match_reasons'] = ref.match_reasons
            citations.append(citation)
            cited_passages.append(passage)
        result = {'state': 'grounded' if citations else 'insufficient', 'citations': citations}
        if receipt.scope:
            result['scope'] = receipt.scope.model_dump()
            result['coverage'] = []
            for part in receipt.coverage:
                entry = part.model_dump(exclude={'fields'})
                if part.fields:
                    entry['fields'] = []
                    for field in part.fields:
                        passage = cited_passages[field.citation - 1]
                        value = next((item for item in passage.values()
                                      if (item.start, item.end) == (field.start, field.end)), None)
                        if value is None or _digest(passage.text[field.start:field.end]) != field.value_digest:
                            return None
                        evidence = _field_view(passage, value, field.attribute, field.citation)
                        if evidence is None:
                            return None
                        source = self._sources[receipt.citations[field.citation - 1].source_id]
                        if source.document:
                            locator = source.locator(passage.line, evidence.get('column'))
                            if locator is None:
                                return None
                            evidence['document_locator'] = locator
                        entry['fields'].append(evidence)
                result['coverage'].append(entry)
            if citations and any(part.state in ('absent', 'incomplete') for part in receipt.coverage):
                result['state'] = 'partial'
        return result

    def _select(self, owner, refs, proposed, target, matches):
        """Validate exact per-part membership before including passages in an answer."""
        try:
            if not isinstance(proposed, str) or len(proposed) > 2048:
                raise ValueError
            selected = Selection.model_validate(_strict_json(proposed)).passages
            if len(selected) != len(set(selected)) or any(label not in refs for label in selected):
                raise ValueError
        except (TypeError, ValueError, RecursionError):
            return (), False
        if not selected:
            return (), False
        matched = {label: self._passage_for(item) for label, item in refs.items()}
        conflicts = _conflicting_labels(
            matched, set(selected), target.fact,
            {label: matches[item.chunk_id][3] for label, item in refs.items()},
        )
        included = set(selected) | conflicts
        # Keep source ranking between documents and original line order within a document.
        order = {source: index for index, source in enumerate(dict.fromkeys(item.document_id for item in refs.values()))}
        items = sorted(((label, item) for label, item in refs.items() if label in included),
                       key=lambda entry: (order[entry[1].document_id], entry[1].chunk_index))
        result = validate_answer(' '.join(f'[{label}]' for label, _ in items), refs,
                                 allowed=lambda sid, rev: self._allowed(owner, sid, rev))
        return tuple(item for _, item in result.citations), bool(conflicts)

    def answer(self, owner, scope, groups):
        """Render exact passages and record what each question part can cite."""
        self.check_scope(owner, scope)
        deps, citations, coverage, sections = {}, {}, [], []
        source_ids = {ref.source_id for ref in scope.sources}
        for question, refs, proposed in groups:
            target = self._question(question, source_ids)
            self.check_context(owner, refs)
            eligible, matches = {}, {}
            for label, item in refs.items():
                passage = self._passage_for(item)
                if (item.document_id in source_ids and passage.context.searchable and not target.clarification
                        and (match := _match(target, passage, item.filename))):
                    eligible[label] = item
                    matches[item.chunk_id] = match
            for item in eligible.values():
                deps[item.document_id] = self._sources[item.document_id].reference()
            passages, conflict = self._select(owner, eligible, proposed, target, matches)
            numbers, quotes, fields = [], [], []
            if passages:
                comparison = bool(_COMPARE.search(question))
                cited_entities = {tag for item in passages for tag in self._passage_for(item).equipment()}
                missing_entities = not set(target.requested_entities) <= cited_entities
                cited_components = set().union(*(matches[item.chunk_id][2] for item in passages))
                missing_components = target.fact is not None and not target.fact.components <= cited_components
                incomplete = missing_entities or missing_components or (comparison and len(target.requested_entities) < 2
                                                                        and len({item.document_id for item in passages}) < 2)
                state = 'conflict' if conflict else 'incomplete' if incomplete else 'supported'
                intro = ('The registered sources give conflicting values.' if conflict else
                         'Cited evidence is missing for at least one requested equipment identifier.' if missing_entities else
                         'Cited evidence is missing for at least one requested component.' if missing_components else
                         'Only one source supplies cited evidence for this comparison.' if incomplete else
                         'Compare these passages from the registered sources.' if comparison else
                         'The registered sources provide these passages.')
                for item in passages:
                    evidence, number = citations.setdefault(item.chunk_id, (
                        {**deps[item.document_id], 'passage': item.chunk_index, 'excerpt_digest': _digest(item.text),
                         'match_reasons': []}, len(citations) + 1))
                    evidence['match_reasons'] = list(dict.fromkeys([*evidence['match_reasons'], *matches[item.chunk_id][1]]))
                    numbers.append(number)
                    passage = self._passage_for(item)
                    values = matches[item.chunk_id][3]
                    dense = passage.context.table_header or len(passage.values()) > 1
                    if target.fact and target.fact.attribute != 'procedure' and values:
                        for value in values:
                            attribute = target.fact.attribute
                            view = _field_view(passage, value, attribute, number)
                            fields.append({
                                'citation': number, 'attribute': attribute, 'start': value.start, 'end': value.end,
                                'value_digest': _digest(view['value']),
                            })
                            if dense:
                                label = ' / '.join([*view['equipment'], *view['conditions'],
                                                    view.get('header', attribute.replace('_', ' '))])
                                display_unit = '' if view['value'].endswith(view['unit']) else f' ({view["unit"]})'
                                quotes.append(f'{label}: "{view["value"]}"{display_unit} [{number}]')
                        if dense:
                            continue
                    if target.fact and target.fact.attribute == 'requirements':
                        quotes.append(f'{item.filename}: "{item.text}" [{number}]')
                    else:
                        quotes.append(f'“{item.text}” [{number}]')
            else:
                state = 'incomplete' if eligible or target.clarification else 'absent'
                intro = target.clarification or INSUFFICIENT
                if state == 'absent' and target.fact and target.fact.attribute not in ('unrecognized', 'limit'):
                    attribute = {'time': 'interval or frequency', 'alarm': 'alarm threshold'}.get(
                        target.fact.attribute, target.fact.attribute.replace('_', ' '))
                    intro += f' No supporting passage was found for the requested {attribute}.'
            coverage.append({'question': question, 'state': state, 'citations': numbers,
                             **({'fields': fields} if fields else {})})
            sections.append('\n\n'.join([*([question] if len(groups) > 1 else []), intro, *quotes]))
        return '\n\n'.join(sections), {
            'corpus_id': self.id, 'index_version': _LOCATOR_VERSION, 'dependencies': list(deps.values()),
            'citations': [ref for ref, _ in citations.values()], 'scope': scope.model_dump(), 'coverage': coverage,
        }


def _field_view(passage, value, attribute, number):
    headings = tuple(section.title for section in passage.context.sections)
    tags = effective_conditions(passage.text, value, passage.context.table_header, headings)
    if tags is None:
        return None
    view = {
        'citation': number, 'attribute': attribute, 'value': passage.text[value.start:value.end],
        'unit': value.source_unit, 'equipment': list(passage.equipment().values()), 'conditions': sorted(tags),
        'line': passage.line, 'start': passage.start + value.start, 'end': passage.start + value.end,
    }
    if value.column is not None:
        view.update(column=value.column + 1, header=passage.context.table_header[value.column],
                    header_line=passage.context.table_header_line)
    return view


def _conflicting_labels(passages, selected, fact, qualified):
    """Compare like claims only within the same entity, condition and section wording."""
    groups = {}
    for label, passage in passages.items():
        sections = passage.context.sections
        path = tuple(section.title.casefold() for section in sections)
        values = qualified[label]
        equipment = passage.equipment()
        if passage.context.table_header:
            if fact is None or fact.attribute == 'procedure' or len(equipment) != 1:
                continue
            component = ' '.join((table_component(passage.text, passage.context.table_header) or '').casefold().split())
            for value in values:
                view = _field_view(passage, value, fact.attribute, 1)
                header = re.sub(r'\s*\([^()]+\)\s*$', '', view['header']).casefold()
                key = ('table', tuple(equipment), fact.attribute, header, component,
                       tuple(view['conditions']), value.dimension)
                groups.setdefault(key, []).append((label, value))
            continue
        if len(values) != 1 or not _METRIC.search(f'{" / ".join(path)} {passage.text}'):
            continue
        value = values[0]
        body_conditions = measurement_conditions(passage.text, value, passage.context.table_header)
        # A heading that names this equipment anchors an explicitly qualified claim.
        # Document titles and generic operating wrappers can differ between sources.
        if (len(sections) > 1 and sections[0].level == 1 and _DOCUMENT_HEADING.search(path[0])
                and len(equipment) == 1 and body_conditions
                and path[1] in {tag.casefold() for tag in equipment}
                and not entities(path[0]) and not conditions(path[0]) and _METRIC.search(passage.text)):
            path = tuple(title for title in path[1:] if title not in _OPERATING_HEADINGS)
        statement = ' '.join((passage.text[:value.start] + ' <measurement> ' + passage.text[value.end:]).casefold().split())
        key = (path, tuple(equipment), passage.context.table_header, statement, value.dimension)
        groups.setdefault(key, []).append((label, value))
    conflicts = set()
    for entries in groups.values():
        if not any(label in selected for label, _ in entries):
            continue
        for label, value in entries:
            for other, candidate in entries:
                if other == label or (bounds := interval_in(candidate, value)) is None:
                    continue
                if bounds[1] < value.low or bounds[0] > value.high:
                    conflicts.update((label, other))
    return conflicts
