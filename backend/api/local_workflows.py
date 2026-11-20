"""Read-only workflow library and explicit preparation of a new task."""

import sqlite3
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from backend.agent.memory import LOCAL_PLAN_LIMIT, LOCAL_WORKFLOW_NONRETRYABLE
from backend.agent.workflow_intake import WorkflowIntake
from backend.api.local_chat import _require_local_viewer
from backend.auth.dependencies import get_current_user
from backend.multimodal.local_images import LocalImageRef
from backend.rag.local_knowledge import KnowledgeConflict, KnowledgeScope, KnowledgeSourceIds
from backend.tools.local_drafts import DraftScope, check_evidence, draft_origin, resolve_evidence, revision_linked

router = APIRouter(prefix='/api/chat')
Status = Literal['', 'running', 'waiting_clarification', 'completed', 'failed', 'interrupted', 'cancelled']


class WorkflowReuse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source_ids: KnowledgeSourceIds | None = None


def _response(data):
    return JSONResponse(data, headers={'Cache-Control': 'no-store'})


def _summary(row):
    session, turn, title, objective, state, created, updated = row
    return {'session_id': session, 'id': turn, 'conversation_title': title or 'Conversation',
            'objective': objective, 'state': state, 'created_at': created, 'updated_at': updated}


@router.get('/workflows')
async def list_workflows(request: Request, status: Status = '', q: str = Query('', max_length=120),
                         limit: int = Query(12, ge=1, le=30), offset: int = Query(0, ge=0, le=1000),
                         user=Depends(get_current_user)):
    state = _require_local_viewer(request, user)
    try:
        rows = state.engine._memory.local_workflow_page(user.id, status, q.strip().lower(), limit + 1, offset)
    except PermissionError:
        raise HTTPException(404, 'Workflow history is unavailable.') from None
    except (sqlite3.Error, OSError, ValueError):
        raise HTTPException(503, 'Workflow history could not be loaded.') from None

    # This badge reports a recorded export. Detail and download recheck live dependencies.
    drafts = set()
    artifacts = set()
    for draft in state.local_drafts.records.values():
        keys = {(draft.session, turn) for turn in _draft_turns(draft, state.local_drafts.history.get(draft.id, []))}
        drafts.update(keys)
        if draft.export_digest and draft.exported and not draft.cancelled and state.local_drafts.is_approved(draft):
            artifacts.update(keys)
    items = []
    for row in rows[:limit]:
        key = row[:2]
        items.append({**_summary(row), 'has_draft': key in drafts and row[4] == 'completed',
                      'export_recorded': key in artifacts and row[4] == 'completed'})
    return _response({'workflows': items, 'next_offset': offset + limit
                      if len(rows) > limit and offset + limit <= 1000 else None})


def _read(request, user, session_id, turn_id):
    state = _require_local_viewer(request, user)
    memory = state.engine._memory
    try:
        identity = memory.stream_identity(session_id)
        info, record, steps, latest = memory.read_local_workflow(session_id, identity, str(turn_id))
    except (KeyError, PermissionError):
        raise HTTPException(404, 'Workflow not found.') from None
    except (sqlite3.Error, OSError, ValueError):
        raise HTTPException(503, 'The workflow record could not be verified.') from None
    return state, identity, info, record, steps, latest


def _inputs(record):
    intake = WorkflowIntake.model_validate(record['workflow_intake']['intake']) if 'workflow_intake' in record else None
    scope = KnowledgeScope.model_validate(record['knowledge_scope']['scope']) if 'knowledge_scope' in record else None
    image = LocalImageRef.model_validate(record['image']['ref']) if 'image' in record else None
    draft = DraftScope.model_validate(record['draft_scope']['scope']) if 'draft_scope' in record else None
    if draft and draft.refinement:
        scope = draft.refinement.sources
    return intake, scope or (intake.scope if intake and not intake.choose_sources else None), image or (
        intake.image if intake else None), draft


def _source_status(state, owner, scope):
    if scope is None:
        return []
    result = []
    for ref in scope.sources:
        current = True
        reusable = False
        try:
            state.local_knowledge.check_scope(owner, scope.model_copy(update={'sources': [ref]}))
        except KnowledgeConflict:
            current = False
        if current:
            try:
                reusable = state.local_knowledge.bind_scope(owner, [ref.source_id]).sources == [ref]
            except KeyError:
                pass  # A clarification upload can stay scoped to its original turn.
        result.append({'id': ref.source_id, 'label': ref.label, 'revision': ref.revision,
                       'current': current, 'reusable': reusable})
    return result


def _related_drafts(state, identity, session, turn):
    memory = state.engine._memory
    result = []
    for draft in state.local_drafts.owned(session, identity):
        history = state.local_drafts.history.get(draft.id, [])
        if turn not in _draft_turns(draft, history):
            continue
        if not draft_origin(memory, session, draft):
            continue
        pending = not revision_linked(state, identity, draft)
        if pending:
            if not history or not revision_linked(state, identity, history[-1]):
                continue
            draft = history[-1]
        stale = bool(check_evidence(state, session, identity, draft))
        approved = state.local_drafts.is_approved(draft)
        result.append({'id': draft.id, 'title': draft.title, 'revision': draft.revision,
                       'format': draft.format, 'stale': stale, 'pending_revision': pending,
                       'cancelled': draft.cancelled, 'approved': approved,
                       'downloadable': bool(not (pending or stale or draft.cancelled) and approved
                                            and draft.exported and draft.export_digest),
                       'revisions': [row.revision for row in history if row.revision < draft.revision]})
    return result


def _draft_turns(draft, history):
    return {draft.turn_id, *(row.change.turn_id for row in [draft, *history] if row.change and row.change.turn_id)}


def _can_reuse(intake, draft, scope, sources, image):
    return bool((intake or draft and draft.workflow) and not (draft and draft.refinement)
                and (not image or scope and scope.sources)
                and (scope is None or any(source['reusable'] for source in sources)))


@router.get('/sessions/{session_id}/workflows/{turn_id}')
async def read_workflow(session_id: str, turn_id: UUID, request: Request, user=Depends(get_current_user)):
    state, identity, info, record, steps, latest = _read(request, user, session_id, turn_id)
    intake, scope, image, draft = _inputs(record)
    sources = _source_status(state, user.id, scope)
    image_current = True
    if image is not None:
        try:
            image_current = state.local_images.get(session_id, identity, image.image_id, turn_id=str(turn_id))[0] == image
        except KeyError:
            image_current = False
    related = _related_drafts(state, identity, session_id, str(turn_id))
    current = (all(source['current'] for source in sources) and image_current
               and not any(item['stale'] for item in related))
    retry_state = latest and record['state'] in {'failed', 'cancelled', 'interrupted'}
    if retry_state and draft and draft.evidence:
        current = current and len(resolve_evidence(state, session_id, identity, draft.evidence)) == len(draft.evidence)
    if retry_state and draft and draft.refinement:
        plan = draft.refinement
        target = next((row for row in state.local_drafts.owned(session_id, identity)
                       if row.id == plan.target.id), None)
        current = bool(current and target and not target.cancelled and
                       (target.revision == plan.target.revision or target.change
                        and target.change.turn_id == str(turn_id) and target.change.parent == plan.target.revision
                        and target.change.plan_digest == plan.digest()))
    error = record.get('error_code')
    return _response({
        **_summary(info), 'attempt': record['attempt'], 'plan_revision': intake.revision if intake else None,
        'steps': steps, 'last_error': error, 'sources': sources,
        'image': {'label': image.filename, 'current': image_current} if image else None,
        'dependencies_current': current, 'drafts': related,
        'clarification': intake.clarification() if record['state'] == 'waiting_clarification' else None,
        'can_resume': latest and current and record['state'] == 'waiting_clarification',
        'can_retry': (retry_state and current
                      and record['attempt'] < LOCAL_PLAN_LIMIT and error not in LOCAL_WORKFLOW_NONRETRYABLE),
        'can_reuse': _can_reuse(intake, draft, scope, sources, image),
    })


@router.post('/sessions/{session_id}/workflows/{turn_id}/reuse')
async def prepare_workflow_reuse(session_id: str, turn_id: UUID, body: WorkflowReuse, request: Request,
                                 user=Depends(get_current_user)):
    state, _, info, record, _, _ = _read(request, user, session_id, turn_id)
    intake, scope, image, draft = _inputs(record)
    sources = _source_status(state, user.id, scope)
    allowed = {source['id'] for source in sources if source['reusable']}
    selection = body.source_ids
    if (not _can_reuse(intake, draft, scope, sources, image) or scope is not None and not selection
            or selection and (len(set(selection)) != len(selection) or not set(selection) <= allowed)):
        raise HTTPException(409, 'Choose current, reusable sources from this workflow.')
    # This returns editable inputs for the existing task preview. No turn is
    # created and no evidence, approval, image ID or execution state is copied.
    return _response({'turn_id': str(uuid4()), 'message': info[3],
                      'knowledge_source_ids': selection,
                      'requires_image_selection': bool(image or intake and intake.needs_image)})
