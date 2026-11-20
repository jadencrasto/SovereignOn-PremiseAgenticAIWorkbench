"""Owned evidence drafts and atomic local exports, separate from publication."""

import base64
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from backend.local_history import _safe_path, _strict_json, write_local_snapshot

DRAFT_TOOL = 'local_grounded_draft'
DRAFT_PRODUCER = 'local-grounded-draft'
MAX_EXPORT = 512 * 1024
MAX_DRAFTS = 8
MAX_FINDINGS = 48
_MAX_SNAPSHOT = 16 * 1024 * 1024
Format = Literal['docx', 'xlsx', 'pdf']
TurnId = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')]
Digest = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]
Revision = Annotated[int, Field(ge=1, le=2**31 - 1)]
EvidenceTurns = Annotated[list[TurnId], Field(max_length=5)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class DraftScope(StrictModel):
    format: Format
    selection: EvidenceTurns | None = None
    evidence: EvidenceTurns

    @model_validator(mode='after')
    def validate_selection(self):
        if (not self.evidence or len(self.evidence) != len(set(self.evidence))
                or (self.selection is not None and self.selection != self.evidence)):
            raise ValueError('Invalid draft evidence selection')
        return self


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
    kind: Literal['source_value', 'source_excerpt', 'visual_interpretation', 'missing', 'user_edited']
    coverage: Literal['supported', 'conflict', 'incomplete', 'absent', 'visual']
    source: dict | None = None
    field: dict | None = None
    image: dict | None = None


class DraftApproval(StrictModel):
    approver: str = Field(min_length=1, max_length=128)
    revision: Revision
    key_id: str = Field(min_length=1, max_length=128)
    mac: Digest


class LocalDraft(StrictModel):
    id: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')]
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
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=5)
    findings: list[Finding] = Field(min_length=1, max_length=MAX_FINDINGS)
    export_digest: Digest | None = None
    exported: str | None = Field(default=None, max_length=(MAX_EXPORT + 2) // 3 * 4)


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


def artifact_intent(prompt):
    match = re.match(r'^\s*(?:please\s+)?(?:create|draft|prepare|make|generate|write|build)\s+'
                     r'(?:(?:a|an|the|me)\s+){0,2}(?:(?:maintenance|inspection|comparison|evidence[- ]grounded)\s+){0,2}'
                     r'(report|spreadsheet|summary|document|docx|xlsx|pdf)\b', prompt, re.I)
    if not match:
        return None
    # Format hints select only an output type. The rest of the request has no
    # authority to name destinations, call tools or approve an export.
    word = match[1].lower()
    if word in ('spreadsheet', 'xlsx') or re.search(r'\b(?:as|in)\s+(?:an?\s+)?xlsx\b', prompt, re.I):
        return 'xlsx'
    if word == 'pdf' or re.search(r'\b(?:as|in)\s+(?:a\s+)?pdf\b', prompt, re.I):
        return 'pdf'
    return 'docx'


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('ascii')


def _mac(key, payload):
    return hmac.new(key, b'local-drafts-v1\0' + _json(payload), hashlib.sha256).hexdigest()


def _approval_mac(key, draft, approver, key_id):
    content = draft.model_dump(exclude={'approval', 'export_digest', 'exported'})
    payload = {'approver': approver, 'key_id': key_id, 'content': content}
    return hmac.new(key, b'local-draft-approval-v1\0' + _json(payload), hashlib.sha256).hexdigest()


class LocalDrafts:
    def __init__(self, owner, root: Path, keys):
        self.owner, self.root, self.keys = owner, root, keys
        root.mkdir(mode=0o700, exist_ok=True)
        _safe_path(root, directory=True)
        self.path = root / 'drafts-v1.json'
        self.records = {}
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

    def save(self, draft=None, *, remove=()):
        records = {key: val for key, val in self.records.items() if key not in remove}
        if draft is not None:
            records[draft.id] = draft
        if len(records) > MAX_DRAFTS:
            raise DraftConflict('Local draft storage is full. Remove a draft first.')
        _safe_path(self.root, directory=True)
        payload = {'owner': self.owner, 'drafts': [record.model_dump() for record in records.values()]}
        key_id, key = self.keys.signing_key()
        encoded = _json({'key': key_id, 'payload': payload, 'mac': _mac(key, payload)})
        if len(encoded) > _MAX_SNAPSHOT:
            raise DraftConflict('Local draft storage is full. Remove a draft first.')
        write_local_snapshot(self.path, encoded)
        self.records = records

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
    resolved = resolve_evidence(state, session, identity, [ref.turn_id for ref in draft.evidence])
    return {ref.turn_id for ref in draft.evidence if ref.turn_id not in resolved or resolved[ref.turn_id][0] != ref}


def draft_origin(memory, session, draft):
    from backend.agent.memory import LOCAL_TURN_FIELD

    history = memory.get_history(session)
    for turn, reply in zip(history, history[1:]):
        if turn.id == draft.turn_id:
            record = turn.extra_fields.get(LOCAL_TURN_FIELD, {})
            proof = reply.extra_fields.get('access_provenance_v1', {})
            return (record.get('state') == 'completed' and record.get('assistant_id') == reply.id
                    and isinstance(proof, dict) and proof.get('producer') == DRAFT_PRODUCER
                    and reply.extra_fields.get('local_draft_id') == draft.id
                    and memory.verify_local_reply(session, reply))
    return False


async def assemble_draft(args: DraftRequest, *, session_id, user_id, step_id):
    """Registered read-only projection. The owning agent commits it at finalization."""
    from backend.auth.request_context import local_draft_scope

    scope = local_draft_scope.get()
    if scope is None:
        raise PermissionError('Draft context unavailable')
    state, identity, admitted = scope
    if args != admitted or identity[0] != user_id or args.turn_id != step_id:
        raise PermissionError('Draft context does not match the admitted request')
    resolved = resolve_evidence(state, session_id, identity, args.scope.evidence)
    if len(resolved) != len(args.scope.evidence):
        raise DraftConflict('Selected evidence is unavailable or stale.')
    findings = [entry for ref in args.scope.evidence for entry in resolved[ref][1]]
    if not findings or len(findings) > MAX_FINDINGS:
        raise DraftConflict('Select a smaller set of evidence turns.')
    return LocalDraft(
        id=uuid4().hex, session=session_id, generation=identity[1], turn_id=args.turn_id,
        format=args.scope.format, title='Evidence comparison' if args.scope.format == 'xlsx' else 'Evidence report',
        summary='This draft presents source-attributed findings, available visual interpretations, and evidence gaps.',
        evidence=[resolved[ref][0] for ref in args.scope.evidence], findings=findings,
    ).model_dump()


def export_bytes(draft):
    raw = base64.b64decode(draft.exported or '', validate=True)
    if not raw or len(raw) > MAX_EXPORT or hashlib.sha256(raw).hexdigest() != draft.export_digest:
        raise DraftConflict('The exported file could not be verified.')
    return raw
