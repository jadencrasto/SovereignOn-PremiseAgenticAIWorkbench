"""Conversation-scoped review, approval and download of local drafts."""

import asyncio
import base64
import hashlib
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from backend.api.local_chat import _require_local_viewer
from backend.auth.dependencies import get_current_user
from backend.tools.local_draft_export import MIME, generate_export
from backend.tools.local_draft_refinement import safe_wording
from backend.tools.local_drafts import (
    COLUMNS, HEADINGS, DraftConflict, DraftEdit, DraftLayout, DraftOperation, DraftRevision,
    check_evidence, draft_origin, draft_reply, export_bytes, revised_draft, revision_linked,
)

router = APIRouter(prefix='/api/chat/sessions/{session_id}/drafts')


@dataclass
class ExportJob:
    draft_id: str
    revision: int
    cancelled: bool = False


def _owned(request, user, session_id):
    state = _require_local_viewer(request, user)
    try:
        identity = state.engine._memory.stream_identity(session_id)
    except PermissionError:
        raise HTTPException(404, 'Conversation not found.') from None
    return state, identity


def _draft(request, user, session_id, draft_id, *, allow_pending=False):
    state, identity = _owned(request, user, session_id)
    row = state.local_drafts.records.get(draft_id)
    if (row is None or (row.session, row.generation) != (session_id, identity[1])
            or not draft_origin(state.engine._memory, session_id, row)):
        raise HTTPException(404, 'Draft not found.')
    if not allow_pending and not revision_linked(state, identity, row):
        raise HTTPException(409, 'Retry the interrupted refinement before changing this draft.')
    return state, identity, row


def _preview(state, identity, draft, *, historical=False, pending=False):
    stale = check_evidence(state, draft.session, identity, draft)
    approved = state.local_drafts.is_approved(draft)
    state_name = ('cancelled' if draft.cancelled else 'stale' if stale else 'draft' if not approved else
                  'exported' if (draft.exported or historical and draft.export_digest) else 'approved')
    job = state.local_export_job
    workflow = {}
    if not draft.evidence and not draft.baseline_replaced:
        reply = draft_reply(state.engine._memory, draft.session, draft)
        if reply is not None:
            workflow = {'workflow_steps': reply.extra_fields['local_workflow']['steps']}
    layout = draft.layout or DraftLayout()
    change = draft.change
    return {
        'id': draft.id, 'turn_id': draft.turn_id, 'format': draft.format, 'revision': draft.revision,
        'title': draft.title, 'summary': draft.summary, 'state': state_name,
        'export_digest': draft.export_digest if state_name == 'exported' else None,
        'exporting': bool(job and job.draft_id == draft.id and job.revision == draft.revision and not job.cancelled),
        'historical': historical, 'pending_revision': pending,
        'available_formats': list(MIME),
        'revision_turn_id': change.turn_id if change else None,
        'history': [row.revision for row in state.local_drafts.history.get(draft.id, []) if row.revision < draft.revision],
        'change': ({'parent': change.parent, 'operation': change.operation.kind, 'created_at': change.created_at,
                    'sections': [HEADINGS[section] for section in change.sections], 'added': change.added,
                    'removed': change.removed, 'unverified': change.unverified} if change else None),
        'layout': {'sections': [{'id': key, 'heading': layout.headings.get(key, HEADINGS[key]),
                                 'wording': (layout.notes.get(key) or None) if not stale else None} for key in layout.order],
                   'columns': [layout.headers.get(key, COLUMNS[key]) for key in layout.columns],
                   'comparison': layout.comparison, 'inline_references': layout.inline_references},
        **workflow,
        'findings': [
            {'topic': 'Prior evidence', 'text': 'Evidence has changed or was removed. Create a new draft to use current evidence.',
             'kind': 'stale', 'coverage': row.coverage, 'turn_id': row.turn_id}
            if row.turn_id in stale else row.model_dump(exclude_none=True)
            for row in draft.findings
        ],
    }


def _require_revision(row, revision):
    if row.revision != revision:
        raise HTTPException(409, 'Review the current draft revision.')


def _require_current(state, identity, draft):
    if draft.cancelled:
        raise HTTPException(409, 'This workflow draft was cancelled.')
    if check_evidence(state, draft.session, identity, draft):
        raise HTTPException(409, 'Draft evidence changed or was removed. Create a new draft.')


def _save(state, draft=None, *, remove=()):
    try:
        state.local_drafts.save(draft, remove=remove)
    except DraftConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except (OSError, ValueError):
        raise HTTPException(503, 'Local draft storage is unavailable.') from None


@router.get('')
async def list_drafts(session_id: str, request: Request, user=Depends(get_current_user)):
    state, identity = _owned(request, user, session_id)
    previews = []
    for draft in state.local_drafts.owned(session_id, identity):
        if not draft_origin(state.engine._memory, session_id, draft):
            continue
        if revision_linked(state, identity, draft):
            previews.append(_preview(state, identity, draft))
        else:
            archived = state.local_drafts.history.get(draft.id, [])
            if archived and revision_linked(state, identity, archived[-1]):
                previews.append(_preview(state, identity, archived[-1], historical=True, pending=True))
    return {'drafts': previews}


@router.get('/{draft_id}/revisions/{revision}')
async def read_revision(session_id: str, draft_id: str, revision: int, request: Request, user=Depends(get_current_user)):
    state, identity, current = _draft(request, user, session_id, draft_id)
    if revision == current.revision:
        return _preview(state, identity, current)
    for row in state.local_drafts.history.get(draft_id, []):
        if row.revision == revision and revision_linked(state, identity, row):
            return _preview(state, identity, row, historical=True)
    raise HTTPException(404, 'Draft revision is unavailable.')


@router.patch('/{draft_id}')
async def edit_draft(session_id: str, draft_id: str, body: DraftEdit, request: Request, user=Depends(get_current_user)):
    state, identity, draft = _draft(request, user, session_id, draft_id)
    _require_revision(draft, body.revision)
    _require_current(state, identity, draft)
    if (not body.title.strip() or any(index >= len(draft.findings) for index in body.wording)
            or any(not entry.strip() for entry in body.wording.values())):
        raise HTTPException(422, 'Enter a title and nonempty wording for existing findings.')
    text = [body.title, body.summary, *body.wording.values()]
    if not all(map(safe_wording, text)):
        raise HTTPException(422, 'Draft wording contains unsupported characters.')
    findings = [
        finding.model_copy(update={'text': body.wording[index], 'kind': 'user_edited'})
        if index in body.wording and body.wording[index] != finding.text else finding
        for index, finding in enumerate(draft.findings)
    ]
    if (draft.title, draft.summary, draft.findings) == (body.title, body.summary, findings):
        return _preview(state, identity, draft)
    try:
        updated = revised_draft(draft, DraftOperation(kind='manual'), title=body.title, summary=body.summary,
                               findings=findings, sections=['summary'] if body.summary != draft.summary else [])
    except DraftConflict as exc:
        raise HTTPException(409, str(exc)) from None
    _save(state, updated)
    return _preview(state, identity, updated)


@router.post('/{draft_id}/approve')
async def approve_draft(session_id: str, draft_id: str, body: DraftRevision, request: Request,
                        user=Depends(get_current_user)):
    state, identity, draft = _draft(request, user, session_id, draft_id)
    _require_revision(draft, body.revision)
    _require_current(state, identity, draft)
    if not state.local_drafts.is_approved(draft):
        draft = state.local_drafts.approve(draft, user.id)
        _save(state, draft)
    return _preview(state, identity, draft)


@router.post('/{draft_id}/export')
async def export_draft(session_id: str, draft_id: str, body: DraftRevision, request: Request,
                       user=Depends(get_current_user)):
    state, identity, draft = _draft(request, user, session_id, draft_id)
    _require_revision(draft, body.revision)
    _require_current(state, identity, draft)
    if not state.local_drafts.is_approved(draft):
        raise HTTPException(409, 'Explicitly approve this draft revision before export.')
    if draft.exported:
        return _preview(state, identity, draft)
    if state.local_export_job is not None:
        raise HTTPException(409, 'A local export is already in progress.')
    job = state.local_export_job = ExportJob(draft_id, body.revision)
    worker = None

    def release(future=None):
        if future is not None and not future.cancelled():
            future.exception()
        if state.local_export_job is job:
            state.local_export_job = None

    try:
        worker = asyncio.get_running_loop().run_in_executor(None, generate_export, draft)
        raw = await asyncio.shield(worker)
        disconnected = await request.is_disconnected()
        if disconnected or job.cancelled:
            raise HTTPException(409, 'The export was cancelled.')
        _, current_identity, current = _draft(request, user, session_id, draft_id)
        if current != draft or current_identity != identity:
            raise HTTPException(409, 'The approved revision changed during export.')
        _require_current(state, identity, current)
        exported = draft.model_copy(update={'exported': base64.b64encode(raw).decode('ascii'),
                                           'export_digest': hashlib.sha256(raw).hexdigest()})
        _save(state, exported)
    except DraftConflict as exc:
        raise HTTPException(422, str(exc)) from None
    except (OSError, ValueError):
        raise HTTPException(503, 'The local export could not be finalized.') from None
    finally:
        if worker is None or worker.done():
            release()
        else:
            job.cancelled = True
            worker.add_done_callback(release)
    return _preview(state, identity, exported)


@router.post('/{draft_id}/cancel-export')
async def cancel_export(session_id: str, draft_id: str, body: DraftRevision, request: Request,
                        user=Depends(get_current_user)):
    state, identity, draft = _draft(request, user, session_id, draft_id)
    _require_revision(draft, body.revision)
    job = state.local_export_job
    if job is not None and (job.draft_id, job.revision) == (draft_id, body.revision):
        job.cancelled = True
    return _preview(state, identity, draft)


@router.post('/{draft_id}/cancel-workflow')
async def cancel_draft_workflow(session_id: str, draft_id: str, body: DraftRevision, request: Request,
                                user=Depends(get_current_user)):
    state, identity, draft = _draft(request, user, session_id, draft_id)
    _require_revision(draft, body.revision)
    if draft.evidence or draft.exported:
        raise HTTPException(409, 'This workflow is not awaiting approval or export.')
    if not draft.cancelled:
        updated = draft.model_copy(update={'cancelled': True, 'approval': None})
        _save(state, updated)
        draft = updated
        job = state.local_export_job
        if job is not None and (job.draft_id, job.revision) == (draft.id, draft.revision):
            job.cancelled = True
    return _preview(state, identity, draft)


@router.get('/{draft_id}/download')
async def download_draft(session_id: str, draft_id: str, request: Request, revision: int = Query(ge=1),
                         user=Depends(get_current_user)):
    state, identity, draft = _draft(request, user, session_id, draft_id)
    _require_revision(draft, revision)
    _require_current(state, identity, draft)
    if not state.local_drafts.is_approved(draft) or not draft.exported:
        raise HTTPException(404, 'No finalized export for this revision.')
    try:
        raw = export_bytes(draft)
    except ValueError:
        raise HTTPException(503, 'The exported file could not be verified.') from None
    return Response(raw, media_type=MIME[draft.format], headers={
        'Content-Disposition': f'attachment; filename="evidence-report-r{revision}.{draft.format}"',
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store',
    })


@router.delete('/{draft_id}')
async def remove_draft(session_id: str, draft_id: str, request: Request, revision: int = Query(ge=1),
                       user=Depends(get_current_user)):
    state, identity, draft = _draft(request, user, session_id, draft_id, allow_pending=True)
    if revision_linked(state, identity, draft):
        _require_revision(draft, revision)
    elif not draft.change or draft.change.parent != revision:
        raise HTTPException(409, 'Review the current draft revision.')
    _save(state, remove=(draft.id,))
    return {'removed': True}
