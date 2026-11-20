"""Owned evidence drafts and atomic local exports, separate from publication."""

import base64
import hashlib
import hmac
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from backend.local_history import _safe_path, _strict_json, write_local_snapshot
from backend.rag.local_knowledge import KnowledgeReceipt, KnowledgeScope

DRAFT_TOOL = 'local_grounded_draft'
DRAFT_PRODUCER = 'local-grounded-draft'
MAX_EXPORT = 512 * 1024
MAX_DRAFTS = 8
MAX_FINDINGS = 48
MAX_REVISIONS = 6
_MAX_SNAPSHOT = 16 * 1024 * 1024
Format = Literal['docx', 'xlsx', 'pdf']
TurnId = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')]
Digest = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]
Revision = Annotated[int, Field(ge=1, le=2**31 - 1)]
EvidenceTurns = Annotated[list[TurnId], Field(max_length=5)]
DraftId = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')]
Section = Literal['summary', 'conclusion', 'recommendations', 'references', 'visual']
Column = Literal['finding', 'value', 'unit', 'equipment', 'condition', 'status', 'reference']
HEADINGS = {'summary': 'Executive summary', 'conclusion': 'Conclusion', 'recommendations': 'Recommendations',
            'references': 'Source references', 'visual': 'Visual observations'}
COLUMNS = {'finding': 'Finding', 'value': 'Reported value or wording', 'unit': 'Unit', 'equipment': 'Equipment',
           'condition': 'Condition', 'status': 'Evidence status', 'reference': 'Reference'}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class DraftTarget(StrictModel):
    id: DraftId
    revision: Revision


class DraftOperation(StrictModel):
    kind: Literal['shorten', 'expand', 'rewrite', 'tone', 'add_section', 'remove_section', 'rename_section',
                  'move_section', 'rename_column', 'move_column', 'table', 'inline_references', 'replace_finding',
                  'remove_finding', 'move_finding', 'explain_finding', 'format', 'add_evidence', 'query_evidence', 'add_visual',
                  'remove_visual', 'regenerate', 'manual']
    section: Section | None = None
    column: Column | None = None
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    position: Literal['first', 'last'] | None = None
    index: int | None = Field(default=None, ge=0, lt=MAX_FINDINGS)
    format: Format | None = None


class DraftRefinement(StrictModel):
    target: DraftTarget
    operation: DraftOperation
    sources: KnowledgeScope | None = None

    def digest(self):
        return hashlib.sha256(_json(self.model_dump())).hexdigest()


class DraftScope(StrictModel):
    format: Format
    selection: EvidenceTurns | None = None
    evidence: EvidenceTurns
    workflow: bool = False
    refinement: DraftRefinement | None = None

    @model_validator(mode='after')
    def validate_selection(self):
        if self.refinement:
            if (self.workflow or (self.selection or []) != self.evidence
                    or len(self.evidence) != len(set(self.evidence))):
                raise ValueError('Invalid refinement evidence selection')
            return self
        if self.workflow and self.selection is None and not self.evidence:
            return self
        if (not self.evidence or len(self.evidence) != len(set(self.evidence))
                or self.workflow or (self.selection is not None and self.selection != self.evidence)):
            raise ValueError('Invalid draft evidence selection')
        return self

    def binding(self):
        exclude = set() if self.workflow else {'workflow'}
        if self.refinement is None:
            exclude.add('refinement')
        return self.model_dump(exclude=exclude)


class DraftRequest(StrictModel):
    turn_id: TurnId
    attempt: Revision
    scope: DraftScope


class EvidenceRef(StrictModel):
    turn_id: TurnId
    attempt: Revision
    assistant_id: str
    proof: Digest


class Finding(StrictModel):
    turn_id: TurnId
    topic: str = Field(max_length=600)
    text: str = Field(min_length=1, max_length=8192)
    kind: Literal['source_value', 'source_excerpt', 'visual_interpretation', 'comparison', 'missing', 'user_edited']
    coverage: Literal['supported', 'conflict', 'incomplete', 'absent', 'visual']
    source: dict | None = None
    field: dict | None = None
    image: dict | None = None


class DraftApproval(StrictModel):
    approver: str = Field(min_length=1, max_length=128)
    revision: Revision
    key_id: str = Field(min_length=1, max_length=128)
    mac: Digest


class DraftLayout(StrictModel):
    order: list[Section] = Field(default_factory=lambda: ['summary', 'references'], max_length=len(HEADINGS))
    headings: dict[Section, Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=dict)
    notes: dict[Literal['conclusion', 'recommendations'], Annotated[str, Field(max_length=2000)]] = Field(default_factory=dict)
    columns: list[Column] = Field(default_factory=lambda: list(COLUMNS), min_length=len(COLUMNS), max_length=len(COLUMNS))
    headers: dict[Column, Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=dict)
    comparison: bool = False
    comparison_attribute: str | None = Field(default=None, max_length=30)
    inline_references: bool = False

    @model_validator(mode='after')
    def validate_order(self):
        if (len(set(self.order)) != len(self.order) or set(self.columns) != COLUMNS.keys()
                or 'references' not in self.order):
            raise ValueError('Invalid draft layout')
        return self


class RevisionInfo(StrictModel):
    parent: Revision
    operation: DraftOperation
    created_at: str = Field(min_length=1, max_length=40)
    turn_id: TurnId | None = None
    plan_digest: Digest | None = None
    sections: list[Section] = Field(default_factory=list, max_length=len(HEADINGS))
    added: int = Field(default=0, ge=0, le=MAX_FINDINGS)
    removed: int = Field(default=0, ge=0, le=MAX_FINDINGS)
    unverified: int = Field(default=0, ge=0, le=MAX_FINDINGS)


class LocalDraft(StrictModel):
    id: DraftId
    session: str
    generation: str
    turn_id: TurnId
    format: Format
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(max_length=2000)
    revision: Revision = 1
    # Recognize interrupted-L17 snapshots. Only the signed receipt grants approval.
    approved_revision: Revision | None = Field(default=None, exclude=True)
    approval: DraftApproval | None = None
    evidence: list[EvidenceRef] = Field(max_length=5)
    findings: list[Finding] = Field(min_length=1, max_length=MAX_FINDINGS)
    export_digest: Digest | None = None
    exported: str | None = Field(default=None, max_length=(MAX_EXPORT + 2) // 3 * 4)
    cancelled: bool = False
    layout: DraftLayout | None = None
    change: RevisionInfo | None = None
    added_evidence: list[EvidenceRef] | None = Field(default=None, max_length=5)
    regenerated: list[KnowledgeReceipt] | None = Field(default=None, max_length=5)
    baseline_replaced: Literal[True] | None = None

    @model_validator(mode='after')
    def validate_revision(self):
        if self.change and (self.change.parent != self.revision - 1
                            or (self.change.turn_id is None) != (self.change.plan_digest is None)):
            raise ValueError('Invalid draft revision lineage')
        if self.baseline_replaced and (not self.change or not self.regenerated):
            raise ValueError('A replacement revision requires verified source evidence')
        return self


class DraftRevision(StrictModel):
    revision: Revision


class DraftEdit(DraftRevision):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(max_length=2000)
    wording: dict[Annotated[int, Field(ge=0, lt=MAX_FINDINGS)], Annotated[str, Field(min_length=1, max_length=2000)]] = (
        Field(default_factory=dict, max_length=MAX_FINDINGS)
    )

    # JSON object keys are strings on the wire.
    @field_validator('wording', mode='before')
    @classmethod
    def numeric_keys(cls, value):
        if not isinstance(value, dict) or any(not isinstance(k, str) or not re.fullmatch(r'0|[1-9][0-9]?', k) for k in value):
            raise ValueError('Invalid finding index')
        return {int(index): entry for index, entry in value.items()}


class DraftConflict(ValueError):
    pass


_ARTIFACT_CLAUSE = re.compile(r'^\s*(?:please\s+)?(?:create|draft|prepare|make|generate|write|build)\s+'
                     r'(?:(?:a|an|the|me)\s+){0,2}(?:(?:cited|short|observation|maintenance|inspection|comparison|evidence[- ]grounded)\s+){0,3}'
                     r'(report|spreadsheet|summary|document|docx|xlsx|pdf)\b', re.I)
_ARTIFACT_CONTINUATION = re.compile(
    r'(?:,?\s+and\s+(?:then\s+)?|[.;]\s+|,\s+then\s+)(?=(?:create|draft|prepare|make|generate|write|build)\b)',
    re.I,
)


def artifact_clauses(prompt):
    prompt = prompt.strip()
    scoped = re.match(r'^(?:use|using)\s+(?:my|the)\s+(?:selected|registered)\s+'
                      r'(?:documents|local sources)\s+to\s+(.+)$', prompt, re.I)
    if scoped:
        return ['', scoped[1]]
    combined = re.match(r'^(?:use|using)\s+([^\n]{1,300}?)\s+to\s+(.+)$', prompt, re.I)
    if combined and re.search(r'\b(?:image|photo|picture|documents?|sources?)\b', combined[1], re.I):
        return [combined[1], combined[2]]
    return _ARTIFACT_CONTINUATION.split(prompt, maxsplit=1)


def artifact_intent(prompt):
    clause = artifact_clauses(prompt)[-1]
    match = _ARTIFACT_CLAUSE.match(clause)
    if not match:
        return None
    # Format hints select only an output type. The rest of the request has no
    # authority to name destinations, call tools or approve an export.
    word = match[1].lower()
    if word in ('spreadsheet', 'xlsx') or re.search(r'\b(?:as|in)\s+(?:an?\s+)?xlsx\b', clause, re.I):
        return 'xlsx'
    if word == 'pdf' or re.search(r'\b(?:as|in)\s+(?:a\s+)?pdf\b', clause, re.I):
        return 'pdf'
    return 'docx'


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('ascii')


def _mac(key, payload):
    return hmac.new(key, b'local-drafts-v1\0' + _json(payload), hashlib.sha256).hexdigest()


def _approval_mac(key, draft, approver, key_id):
    exclude = {'approval', 'export_digest', 'exported', 'cancelled'}
    exclude.update(field for field in ('layout', 'change', 'added_evidence', 'regenerated', 'baseline_replaced')
                   if getattr(draft, field) is None)
    content = draft.model_dump(exclude=exclude)
    payload = {'approver': approver, 'key_id': key_id, 'content': content}
    return hmac.new(key, b'local-draft-approval-v1\0' + _json(payload), hashlib.sha256).hexdigest()


class LocalDrafts:
    def __init__(self, owner, root: Path, keys):
        self.owner, self.root, self.keys = owner, root, keys
        root.mkdir(mode=0o700, exist_ok=True)
        _safe_path(root, directory=True)
        self.path = root / 'drafts-v1.json'
        self.records = {}
        self.history = {}
        if _safe_path(self.path):
            with self.path.open('rb') as stream:
                raw = stream.read(_MAX_SNAPSHOT + 1)
            if len(raw) > _MAX_SNAPSHOT:
                raise ValueError('Local drafts exceed the storage limit')
            envelope = _strict_json(raw)
            payload = envelope['payload']
            key = keys.verification_key(envelope['key'])
            if (key is None or not hmac.compare_digest(_mac(key, payload), envelope['mac'])
                    or payload['owner'] != owner or len(payload['drafts']) > MAX_DRAFTS):
                raise ValueError('Invalid local draft storage')
            self.records = {row['id']: LocalDraft.model_validate(row) for row in payload['drafts']}
            if len(self.records) != len(payload['drafts']):
                raise ValueError('Duplicated local draft identity')
            for draft_id, rows in payload.get('history', {}).items():
                head = self.records[draft_id]
                revisions = [LocalDraft.model_validate(row) for row in rows]
                if (not 1 <= len(revisions) < MAX_REVISIONS
                        or [row.revision for row in revisions] != list(range(head.revision - len(revisions), head.revision))
                        or any(row.exported is not None or (row.id, row.session, row.generation, row.turn_id) !=
                               (head.id, head.session, head.generation, head.turn_id) for row in revisions)):
                    raise ValueError('Invalid local draft revision history')
                self.history[draft_id] = revisions

    def save(self, draft=None, *, remove=()):
        records = {key: val for key, val in self.records.items() if key not in remove}
        history = {key: val for key, val in self.history.items() if key not in remove}
        if draft is not None:
            previous = records.get(draft.id)
            if previous and draft.revision != previous.revision:
                if draft.revision != previous.revision + 1:
                    raise DraftConflict('Review the current draft revision.')
                archived = previous.model_copy(update={'exported': None})
                history[draft.id] = [*history.get(draft.id, []), archived][-(MAX_REVISIONS - 1):]
            records[draft.id] = draft
        if len(records) > MAX_DRAFTS:
            raise DraftConflict('Local draft storage is full. Remove a draft first.')
        _safe_path(self.root, directory=True)
        payload = {'owner': self.owner, 'drafts': [record.model_dump() for record in records.values()],
                   'history': {key: [row.model_dump() for row in rows] for key, rows in history.items()}}
        key_id, key = self.keys.signing_key()
        encoded = _json({'key': key_id, 'payload': payload, 'mac': _mac(key, payload)})
        if len(encoded) > _MAX_SNAPSHOT:
            raise DraftConflict('Local draft storage is full. Remove a draft first.')
        write_local_snapshot(self.path, encoded)
        self.records = records
        self.history = history

    def owned(self, session, identity):
        if identity[0] != self.owner:
            raise PermissionError('Draft access denied')
        return [row for row in self.records.values() if (row.session, row.generation) == (session, identity[1])]

    def approve(self, draft, approver):
        if approver != self.owner:
            raise PermissionError('Draft approval denied')
        key_id, key = self.keys.signing_key()
        receipt = DraftApproval(approver=approver, revision=draft.revision, key_id=key_id,
                                mac=_approval_mac(key, draft, approver, key_id))
        return draft.model_copy(update={'approval': receipt, 'exported': None, 'export_digest': None})

    def is_approved(self, draft):
        receipt = draft.approval
        if receipt is None or receipt.approver != self.owner or receipt.revision != draft.revision:
            return False
        key = self.keys.verification_key(receipt.key_id)
        return key is not None and hmac.compare_digest(
            receipt.mac, _approval_mac(key, draft, receipt.approver, receipt.key_id),
        )

    def remove_session(self, session):
        removed = [row.id for row in self.records.values() if row.session == session]
        if removed:
            self.save(remove=removed)


def evidence_messages(memory, session):
    """Locate candidate completed turn/reply pairs. Callers still verify each proof."""
    from backend.agent.memory import LOCAL_TURN_FIELD, LOCAL_VISION_PRODUCER
    from backend.rag.local_knowledge import KNOWLEDGE_FIELD

    history = memory.get_history(session)
    for turn, reply in zip(history, history[1:]):
        record = turn.extra_fields.get(LOCAL_TURN_FIELD, {})
        proof = reply.extra_fields.get('access_provenance_v1', {})
        visual = isinstance(proof, dict) and proof.get('producer') == LOCAL_VISION_PRODUCER
        if (turn.role == 'user' and reply.role == 'assistant' and record.get('state') == 'completed'
                and record.get('assistant_id') == reply.id and (KNOWLEDGE_FIELD in reply.extra_fields or visual)):
            yield turn, reply, record, visual


def resolve_evidence(state, session, identity, ids):
    from backend.multimodal.local_images import LocalImageRef
    from backend.rag.local_knowledge import KNOWLEDGE_FIELD

    memory = state.engine._memory
    matches = {}
    for turn, reply, record, visual in evidence_messages(memory, session):
        if turn.id not in ids or not memory.verify_local_reply(session, reply):
            continue
        reference = EvidenceRef(turn_id=turn.id, attempt=record['attempt'], assistant_id=reply.id,
                                proof=reply.extra_fields['access_provenance_v1']['mac'])
        if visual:
            ref = LocalImageRef.model_validate(record['image']['ref'])
            try:
                current, _ = state.local_images.get(session, identity, ref.image_id)
            except KeyError:
                continue
            if current == ref:
                observation = reply.content.removeprefix('Model-generated image observations\n\n')
                matches[turn.id] = (reference, [Finding(
                    turn_id=turn.id, topic='Image analysis', text=observation, kind='visual_interpretation',
                    coverage='visual', image={**ref.attachment(True), 'model': record['model'],
                                              'processing': 'completed'},
                )])
        else:
            receipt = state.local_knowledge.public_receipt(identity[0], reply.extra_fields[KNOWLEDGE_FIELD])
            if receipt is not None:
                matches[turn.id] = (reference, _source_findings(turn.id, receipt))
    return matches


def _source_findings(turn_id, receipt):
    findings = []
    citations = {ref['number']: ref for ref in receipt['citations']}
    parts = receipt.get('coverage') or [{'question': 'Source findings', 'state': 'supported' if citations else 'absent',
                                         'citations': list(citations)}]
    for part in parts:
        topic, coverage = part['question'], part['state']
        fields = part.get('fields', ())
        for field in fields:
            source = citations[field['citation']]
            field = {**field, 'value_digest': hashlib.sha256(field['value'].encode('utf-8')).hexdigest()}
            findings.append(Finding(turn_id=turn_id, topic=topic, text=field['value'], kind='source_value',
                                    coverage=coverage, source=source, field=field))
        covered = {field['citation'] for field in fields}
        for number in part['citations']:
            if number not in covered:
                source = citations[number]
                findings.append(Finding(turn_id=turn_id, topic=topic, text=source['excerpt'], kind='source_excerpt',
                                        coverage=coverage, source=source))
        if coverage in ('absent', 'incomplete'):
            findings.append(Finding(turn_id=turn_id, topic=topic, kind='missing', coverage=coverage,
                                    text='Insufficient evidence to establish this finding.'))
    return findings


def check_evidence(state, session, identity, draft):
    services = state.local_knowledge
    stale = set()
    if not draft.evidence and not draft.baseline_replaced:
        from backend.agent.local_workflow import current_workflow_draft

        if not current_workflow_draft(state, session, identity, draft):
            stale.add(draft.turn_id)
    refs = [*([] if draft.baseline_replaced else draft.evidence), *(draft.added_evidence or [])]
    resolved = resolve_evidence(state, session, identity, [ref.turn_id for ref in refs]) if refs else {}
    stale.update(ref.turn_id for ref in refs if ref.turn_id not in resolved or resolved[ref.turn_id][0] != ref)
    if draft.change is None:
        return stale
    candidates = {key: rows for key, (_, rows) in resolved.items()}
    for receipt in draft.regenerated or []:
        public = services.public_receipt(identity[0], receipt)
        if public is None:
            stale.add(receipt.turn_id)
        else:
            candidates[receipt.turn_id] = _source_findings(receipt.turn_id, public)
    for turn_id in {row.turn_id for row in draft.findings} - stale:
        if turn_id == draft.turn_id and not draft.evidence and not draft.baseline_replaced:
            continue  # The signed workflow receipt validates these rows.
        from backend.agent.local_workflow import finding_digest, matches_findings

        originals = candidates.get(turn_id, ())
        proofs = ((finding_digest(row), finding_digest(row, reference=True)) for row in originals)
        if not matches_findings((row for row in draft.findings if row.turn_id == turn_id), proofs):
            stale.add(turn_id)
    return stale


def draft_reply(memory, session, draft, *, refinement_turn=None):
    from backend.agent.memory import LOCAL_TURN_FIELD

    history = memory.get_history(session)
    for turn, reply in zip(history, history[1:]):
        if turn.id == (refinement_turn or draft.turn_id):
            record = turn.extra_fields.get(LOCAL_TURN_FIELD, {})
            proof = reply.extra_fields.get('access_provenance_v1', {})
            if (record.get('state') == 'completed' and record.get('assistant_id') == reply.id
                    and isinstance(proof, dict) and proof.get('producer') == DRAFT_PRODUCER
                    and reply.extra_fields.get('local_draft_id') == draft.id
                    and ('local_workflow' in reply.extra_fields) == (not draft.evidence and refinement_turn is None)
                    and memory.verify_local_reply(session, reply)):
                return reply
            break
    return None


def draft_origin(memory, session, draft):
    return draft_reply(memory, session, draft) is not None


def revision_linked(state, identity, draft):
    change = draft.change
    if change is None or change.turn_id is None:
        return True
    memory = state.engine._memory
    try:
        record = memory.read_local_turn(draft.session, identity, change.turn_id)
        plan = DraftScope.model_validate(record['draft_scope']['scope']).refinement
        return (plan is not None and plan.target == DraftTarget(id=draft.id, revision=change.parent)
                and plan.digest() == change.plan_digest
                and draft_reply(memory, draft.session, draft, refinement_turn=change.turn_id) is not None)
    except (KeyError, PermissionError, ValueError):
        return False


def revised_draft(draft, operation, *, plan=None, turn_id=None, sections=(), **changes):
    if draft.revision == 2**31 - 1:
        raise DraftConflict('This draft has reached its revision limit.')
    findings = changes.get('findings', draft.findings)
    before = Counter(_json(row.model_dump()) for row in draft.findings)
    after = Counter(_json(row.model_dump()) for row in findings)
    info = RevisionInfo(
        parent=draft.revision, operation=operation, created_at=datetime.now(timezone.utc).isoformat(),
        turn_id=turn_id, plan_digest=plan.digest() if plan else None, sections=list(sections),
        added=sum((after - before).values()), removed=sum((before - after).values()),
        unverified=sum(row.kind == 'user_edited' for row in findings),
    )
    return LocalDraft.model_validate({**draft.model_dump(), **changes, 'revision': draft.revision + 1,
                                     'change': info.model_dump(), 'approval': None, 'exported': None,
                                     'export_digest': None})


async def assemble_draft(args: DraftRequest, *, session_id, user_id, step_id):
    """Registered read-only projection. The owning agent commits it at finalization."""
    from backend.auth.request_context import local_draft_scope

    scope = local_draft_scope.get()
    if scope is None:
        raise PermissionError('Draft context unavailable')
    state, identity, admitted, *workflow = scope
    if args != admitted or identity[0] != user_id or args.turn_id != step_id:
        raise PermissionError('Draft context does not match the admitted request')
    if args.scope.workflow:
        from backend.agent.local_workflow import WorkflowEvidence

        if len(workflow) != 1 or not isinstance(workflow[0], WorkflowEvidence):
            raise PermissionError('Verified workflow evidence is unavailable')
        findings = list(workflow[0].findings)
        evidence = []
    else:
        if workflow:
            raise PermissionError('Unexpected workflow input')
        resolved = resolve_evidence(state, session_id, identity, args.scope.evidence)
        if len(resolved) != len(args.scope.evidence):
            raise DraftConflict('Selected evidence is unavailable or stale.')
        findings = [entry for ref in args.scope.evidence for entry in resolved[ref][1]]
        evidence = [resolved[ref][0] for ref in args.scope.evidence]
    if not findings or len(findings) > MAX_FINDINGS:
        raise DraftConflict('Select a smaller set of evidence turns.')
    return LocalDraft(
        id=uuid4().hex, session=session_id, generation=identity[1], turn_id=args.turn_id,
        format=args.scope.format, title='Evidence comparison' if args.scope.format == 'xlsx' else 'Evidence report',
        summary='This draft presents source-attributed findings, available visual interpretations, and evidence gaps.',
        evidence=evidence, findings=findings,
    ).model_dump()


def export_bytes(draft):
    raw = base64.b64decode(draft.exported or '', validate=True)
    if not raw or len(raw) > MAX_EXPORT or hashlib.sha256(raw).hexdigest() != draft.export_digest:
        raise DraftConflict('The exported file could not be verified.')
    return raw
