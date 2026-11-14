"""Non-authorizing evidence validation for a future protected RAG boundary.

This module grants no document access and is not wired to production routes.
The caller must obtain and revalidate authoritative scope externally. Test-only
callbacks do not establish that such an authority exists in this application.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import uuid
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class Evidence:
    document_id: str
    filename: str
    chunk_id: str
    chunk_index: int
    text: str
    distance: float
    source_revision: int
    index_version: str
    embedding_model: str


@dataclass(frozen=True)
class GroundedAnswer:
    text: str
    citations: tuple[tuple[str, Evidence], ...]
    dependencies: tuple[tuple[str, int], ...]


def select_evidence(rows: Sequence[Mapping], *, allowed: Callable[[str, int], bool],
                    index_version: str, embedding_model: str, top_k: int,
                    max_distance: float = 0.385) -> tuple[Evidence, ...]:
    """Validate trusted-store candidate structure, filter scope and rank by cosine distance.

    Metadata is not an authorization decision. `allowed` must be an independent,
    current source authority, which production currently does not possess.
    """
    if not callable(allowed) or not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20:
        raise ValueError('Invalid retrieval scope or top-k')
    if not index_version or not embedding_model or not math.isfinite(max_distance) or not 0 <= max_distance <= 2:
        raise ValueError('Invalid index configuration')
    if not isinstance(rows, (list, tuple)) or len(rows) > 100:
        raise ValueError('Invalid or oversized retrieval result')
    accepted = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError('Malformed retrieval result')
        doc, chunk, filename, text = (row.get(k) for k in ('document_id', 'chunk_id', 'filename', 'text'))
        idx, revision, distance = (row.get(k) for k in ('chunk_index', 'source_revision', 'distance'))
        if (any(not isinstance(v, str) or not v.strip() or len(v) > 512 for v in (doc, chunk, filename))
            or not isinstance(text, str) or not text.strip() or len(text) > 10000
            or not isinstance(idx, int) or isinstance(idx, bool) or idx < 0
            or not isinstance(revision, int) or isinstance(revision, bool) or revision < 1
            or isinstance(distance, bool) or not isinstance(distance, (float, int))
            or not math.isfinite(distance) or not 0 <= distance <= 2
            or chunk != f'{doc}_chunk_{idx}'):
            raise ValueError('Malformed source or score')
        if row.get('index_version') != index_version or row.get('embedding_model') != embedding_model:
            raise ValueError('Incompatible index configuration')
        decision = allowed(doc, revision)
        if decision is False:
            continue
        if decision is not True:
            raise PermissionError('Evidence authorization unavailable')
        if distance > max_distance:
            continue
        item = Evidence(doc, filename, chunk, idx, text, float(distance), revision, index_version, embedding_model)
        previous = accepted.get(chunk)
        if previous is not None and (previous.document_id, previous.filename, previous.text, previous.source_revision) != (doc, filename, text, revision):
            raise ValueError('Conflicting duplicate chunk')
        if previous is None or item.distance < previous.distance:
            accepted[chunk] = item
    return tuple(sorted(accepted.values(), key=lambda x: (x.distance, x.document_id, x.chunk_index, x.chunk_id))[:top_k])


def build_context(evidence: Sequence[Evidence], *, allowed: Callable[[str, int], bool], max_chars: int = 12000):
    """Build bounded request-scoped citation IDs; never cite text markers as pages."""
    if not 1 <= max_chars <= 50000 or len(evidence) > 20:
        raise ValueError('Invalid context size')
    refs = {}
    parts = []
    used = 0
    scope = uuid.uuid4().hex
    for item in evidence:
        if not isinstance(item, Evidence) or allowed(item.document_id, item.source_revision) is not True:
            raise PermissionError('Evidence no longer available')
        label = f'C{scope}_{len(refs) + 1}'
        part = f'[{label}] {item.text}'
        if used + len(part) + 1 > max_chars:
            continue
        refs[label] = item
        parts.append(part)
        used += len(part) + 1
    return '\n'.join(parts), refs


def validate_answer(text: str, refs: Mapping[str, Evidence], *, allowed: Callable[[str, int], bool]) -> GroundedAnswer:
    """Validate references only; does not establish that claims follow from evidence."""
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise ValueError('Invalid answer')
    found = re.findall(r'\[([^\[\]]+)\]', text)
    if not found or any(label not in refs for label in found):
        raise ValueError('Answer has missing or unknown citations')
    labels = list(dict.fromkeys(found))
    cited = []
    for label in labels:
        item = refs[label]
        if not isinstance(item, Evidence) or allowed(item.document_id, item.source_revision) is not True:
            raise PermissionError('Evidence no longer available')
        cited.append((label, item))
    # All passages in the prompt are dependencies, including uncited ones.
    for item in refs.values():
        if not isinstance(item, Evidence) or allowed(item.document_id, item.source_revision) is not True:
            raise PermissionError('Evidence no longer available')
    deps = tuple(sorted({(item.document_id, item.source_revision) for item in refs.values()}))
    return GroundedAnswer(text, tuple(cited), deps)


async def answer_with_evidence(question: str, rows: Sequence[Mapping], *, allowed, index_version: str,
                               embedding_model: str, top_k: int, generate, max_chars: int = 12000,
                               verify_evidence=None):
    """Isolated fake-provider-ready orchestration; no production routes call this."""
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ValueError('Invalid question')
    if not callable(verify_evidence):
        raise ValueError('An evidence verifier is required')
    denied = False
    def checked_scope(document_id, revision):
        nonlocal denied
        decision = allowed(document_id, revision)
        if decision is False:
            denied = True
        return decision
    selected = select_evidence(rows, allowed=checked_scope, index_version=index_version,
                               embedding_model=embedding_model, top_k=top_k)
    for item in selected:
        if verify_evidence(item) is not True:
            raise PermissionError('Evidence integrity unavailable')
    context, refs = build_context(selected, allowed=allowed, max_chars=max_chars)
    if not refs:
        if denied:
            raise PermissionError('Evidence authorization unavailable')
        return None
    response = await generate(question, context)
    answer = validate_answer(response, refs, allowed=allowed)
    for item in refs.values():
        if verify_evidence(item) is not True:
            raise PermissionError('Evidence integrity unavailable')
    return answer
