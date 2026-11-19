"""Bounded lexical retrieval for the explicitly registered local_dev corpus."""

from dataclasses import dataclass
from decimal import Decimal
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
_MAX_SNAPSHOT = 28000
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
_STOP = set(('a an and are as at be by can compare contrast could did do does difference differences differ conflict '
             'disagree both each for from how in is it its me of on or should show so tell than that the their them '
             'there these this to use was were what when where which who why will with would my our your source '
             'sources note notes document documents say says about between according find please').split())
_NUM = re.compile(r'(?<!\w)[+-]?\d+(?:\.\d+)?(?!\w)')
_METRIC = re.compile(r'\b(?:limit|pressure|temperature|interval|inspect|recommend|capacity|tolerance)\w*\b', re.I)
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


class EvidenceCoverage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    state: Literal['supported', 'incomplete', 'absent', 'conflict']
    citations: list[Annotated[int, Field(ge=1, le=MAX_CONTEXT_PASSAGES)]] = Field(max_length=MAX_PASSAGES)


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
    if len(parts) == 1 and not _COMPARE.search(question):
        match = re.fullmatch(r'(what (?:is|are) )(.+?) and (.+)', parts[0], re.IGNORECASE)
        if match and len(_terms(match[2])) >= 2 and len(_terms(match[3])) >= 2:
            return (match[1] + match[2], match[1] + match[3])
    return parts


def _terms(text):
    return {'inspect' if word in {'inspection', 'inspections', 'inspecting'} else
            word.removesuffix('s') if len(word) > 4 and not word.endswith('ss') else word
            for word in re.findall(r'[^\W_]+', text.casefold()) if word not in _STOP}


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
    heading: str
    heading_line: int | None


def _passages(text):
    """Keep source lines and offsets. Oversized lines split on word boundaries."""
    heading, heading_line, offset, index = '', None, 0, 0
    for line_no, line in enumerate(text.split('\n'), 1):
        stripped = line.strip()
        if re.match(r'^#{1,6}\s+', stripped):
            heading, heading_line = stripped, line_no
        elif stripped:
            start = len(line) - len(line.lstrip())
            end = len(line.rstrip())
            while start < end:
                stop = min(start + 320, end)
                if stop < end:
                    boundary = line.rfind(' ', start, stop)
                    if boundary > start:
                        stop = boundary
                yield Passage(index, line_no, offset + start, offset + stop, line[start:stop], heading, heading_line)
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

    def reference(self):
        return {'source_id': self.id, 'revision': self.revision, 'digest': self.digest}

    def public(self):
        return {**self.reference(), 'label': self.label, 'bytes': len(self.text.encode('utf-8')),
                'passages': len(self.passages)}


def _source(source_id, label, revision, raw, media_type):
    ext = '.md' if label.lower().endswith('.md') else '.txt'
    text_filename(label, ext)
    if ext == '.md' and media_type.split(';', 1)[0].strip().lower() in ('text/markdown', 'text/x-markdown'):
        media_type = 'text/plain' + (';' + media_type.split(';', 1)[1] if ';' in media_type else '')
    text = decode_text_file(raw, media_type).replace('\r\n', '\n').replace('\r', '\n')
    passages = tuple(_passages(text))
    if not passages:
        raise TextFileError('Include some body text in the source.')
    return Source(source_id, label, revision, _digest(text), text, passages)


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
                {**source.reference(), 'label': source.label, 'text': source.text}
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
                'max_corpus_bytes': MAX_CORPUS_BYTES, 'sources': [s.public() for s in self._sources.values()]}

    def _existing(self, source_id, revision):
        source = self._sources[source_id]
        if source.revision != revision:
            raise KnowledgeConflict('The source has changed. Refresh the corpus and try again.')
        return source

    def register(self, owner, label, raw, media_type, *, source_id=None, revision=None):
        self._authorize(owner)
        old = self._existing(source_id, revision) if source_id else None
        if old and old.revision >= 2**31 - 1:
            raise KnowledgeConflict('The source has reached its revision limit.')
        source = _source(old.id if old else uuid4().hex, label, old.revision + 1 if old else 1, raw, media_type)
        if old and (source.label, source.digest) == (old.label, old.digest):
            return old.public()
        if any(current.id != source.id and (current.digest == source.digest
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

    def retrieve(self, owner, question, source_ids=None):
        self._authorize(owner)
        query = _terms(question)
        section_query = bool(_SECTION_QUERY.search(question))
        if section_query:
            query -= {'section', 'heading', 'describe', 'contain', 'cover', 'mention'}
        if not question or len(question) > MAX_QUESTION or not query or len(query) > 32:
            return ()
        required = max(min(2, len(query)), (len(query) + 1) // 2)
        ranked = []
        for position, source in enumerate(self._sources.values()):
            if source_ids is not None and source.id not in source_ids:
                continue
            matches = []
            for passage in source.passages:
                body = _terms(passage.text)
                heading = _terms(passage.heading)
                body_matches = len(query & body)
                overlap = len(query & (body | heading | _terms(source.label)))
                exact_heading = len(query) >= 2 and query <= heading
                if exact_heading or (overlap >= required and body_matches >= min(2, required)):
                    score = (body_matches, overlap)
                    matches.append((score, passage))
            matches.sort(key=lambda row: (-row[0][0], -row[0][1], row[1].index))
            for rank, (score, passage) in enumerate(matches):
                ranked.append((rank, -score[0], -score[1], position, source, passage))
        ranked.sort(key=lambda row: row[:4])
        result, seen = [], set()
        for _, _, _, _, source, passage in ranked:
            key = ' '.join(passage.text.casefold().split())
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
        return [{'id': label, 'source': item.filename, 'passage': item.text,
                 'heading': self._sources[item.document_id].passages[item.chunk_index].heading[:160]}
                for label, item in refs.items()]

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
            if passage.heading:
                citation.update(heading=passage.heading, heading_line=passage.heading_line)
            citations.append(citation)
        result = {'state': 'grounded' if citations else 'insufficient', 'citations': citations}
        if receipt.scope:
            result['scope'] = receipt.scope.model_dump()
            result['coverage'] = [part.model_dump() for part in receipt.coverage]
            if citations and any(part.state in ('absent', 'incomplete') for part in receipt.coverage):
                result['state'] = 'partial'
        return result

    def _select(self, owner, refs, proposed):
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
        headings = {label: self._sources[item.document_id].passages[item.chunk_index].heading
                    for label, item in refs.items()}
        conflicts = _conflicting_labels(refs, set(selected), headings)
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
        for question, refs, proposed in groups:
            self.check_context(owner, refs)
            for item in refs.values():
                deps[item.document_id] = self._sources[item.document_id].reference()
            passages, conflict = self._select(owner, refs, proposed)
            numbers, quotes = [], []
            if passages:
                comparison = bool(_COMPARE.search(question))
                incomplete = comparison and len({item.document_id for item in passages}) < 2
                state = 'conflict' if conflict else 'incomplete' if incomplete else 'supported'
                intro = ('The registered sources give conflicting values.' if conflict else
                         'Only one source supplies cited evidence for this comparison.' if incomplete else
                         'Compare these passages from the registered sources.' if comparison else
                         'The registered sources provide these passages.')
                for item in passages:
                    _, number = citations.setdefault(item.chunk_id, (
                        {**deps[item.document_id], 'passage': item.chunk_index, 'excerpt_digest': _digest(item.text)},
                        len(citations) + 1,
                    ))
                    numbers.append(number)
                    quotes.append(f'“{item.text}” [{number}]')
            else:
                state = 'incomplete' if refs else 'absent'
                intro = INSUFFICIENT
            coverage.append({'question': question, 'state': state, 'citations': numbers})
            sections.append('\n\n'.join([*([question] if len(groups) > 1 else []), intro, *quotes]))
        return '\n\n'.join(sections), {
            'corpus_id': self.id, 'index_version': _LOCATOR_VERSION, 'dependencies': list(deps.values()),
            'citations': [ref for ref, _ in citations.values()], 'scope': scope.model_dump(), 'coverage': coverage,
        }


def _conflicting_labels(refs, selected, headings):
    """Compare numeric claims with matching wording, unit and section headings."""
    groups = {}
    for label, item in refs.items():
        phrase = ' '.join(item.text.casefold().split())
        matches = list(_NUM.finditer(phrase))
        if len(matches) != 1 or not _METRIC.search(phrase):
            continue
        match = matches[0]
        heading = ' '.join(headings[label].lstrip('#').casefold().split())
        groups.setdefault((heading, phrase[:match.start()], phrase[match.end():]), []).append(
            (label, item.document_id, Decimal(match[0]))
        )
    return {label for entries in groups.values() if any(label in selected for label, _, _ in entries)
            for label, source, value in entries
            if any(other_source != source and other_value != value for _, other_source, other_value in entries)}
