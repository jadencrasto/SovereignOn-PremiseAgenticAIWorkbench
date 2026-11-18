"""Request-scoped document presentation for trusted in-process application callers."""

import asyncio
from collections.abc import Callable
from contextlib import aclosing
from uuid import uuid4

from starlette.requests import Request

from backend.auth.request_context import request_tools_enabled
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_source_policy import LocalSourcePolicy
from backend.multimodal.document_source_records import ServerSourceProvider, open_server_source_policy
from backend.multimodal.document_transport import SourceAuthority, _document_deadline, _error, open_document_transport
from backend.multimodal.workflow import MAX_INPUTS


# Match the R8.45 presentation contract. A plan has at most three steps,
# with one running and one completion/failure event per step.
_MAX_EVENTS = 9
_EVENT_FIELDS = {
    'task_started': ('status',),
    'plan_created': ('plan',),
    'plan_step': ('step_id', 'description', 'status'),
    'task_completed': ('answer',),
    'task_failed': ('error_code', 'error', 'step_id', 'operation', 'partial'),
    'task_cancelled': ('error_code', 'error', 'step_id', 'operation', 'partial'),
}
_TERMINALS = {'task_completed', 'task_failed', 'task_cancelled'}
_DELIVERY_ERRORS = {
    'AUTHORIZATION_UNAVAILABLE': 'Current document access could not be verified.',
    'INVALID_FORM': 'Supply the authorized files in a complete multipart request.',
    'INPUT_LIMIT': 'Document transport limits were exceeded.',
    'SOURCE_UNAVAILABLE': 'The supplied source did not match its authorization.',
    'EXTRACTION_FAILED': 'The supplied document structure could not be verified.',
    'REQUEST_CLOSED': 'The document request has closed.',
    'CANCELLED': 'The document request was cancelled.',
    'TIMEOUT': 'The document request exceeded its deadline.',
    'OPERATION_UNAVAILABLE': 'The document workflow could not be completed safely.',
}


def _delivery_failure(code: str, task_id: str) -> tuple[dict, ...]:
    if code not in _DELIVERY_ERRORS:
        code = 'OPERATION_UNAVAILABLE'
    return ({
        'type': 'task_cancelled' if code == 'CANCELLED' else 'task_failed',
        'task_id': task_id,
        'error_code': code,
        'error': _DELIVERY_ERRORS[code],
        'step_id': None,
        'operation': None,
        'partial': None,
    },)


def _check_delivery_deadline(events: tuple[dict, ...], deadline: float) -> None:
    terminal = events[-1]
    if ((terminal['type'] == 'task_completed' or terminal.get('partial') is not None)
            and asyncio.get_running_loop().time() >= deadline):
        raise _error('TIMEOUT')


def _presentation_event(event: dict) -> dict:
    kind = event['type']
    projected = {name: event[name] for name in ('type', 'task_id', *_EVENT_FIELDS[kind])}
    if kind == 'plan_created':
        plan = event['plan']
        if not 2 <= len(plan['steps']) <= 3:
            raise ValueError('Invalid document presentation plan')
        projected['plan'] = {
            'objective': plan['objective'],
            'steps': [{key: step[key] for key in ('id', 'description', 'status')} for step in plan['steps']],
        }
    return projected


async def run_document_workflow(
    request: Request,
    conversation_id: str,
    objective: str,
    *,
    source_authority: SourceAuthority,
    cancelled: Callable[[], bool] = lambda: False,
    deadline: float | None = None,
) -> tuple[dict, ...]:
    """Return one R8.45 event batch after authenticated transport exit.

    The caller must authenticate this Request and establish server-side tool
    permission. Use a trusted source authority independent of the uploaded body.
    Keep the batch in request memory. This interface has no streaming callback.
    """
    buffered: list[dict] = []
    task_id = uuid4().hex
    terminal = None
    try:
        if request_tools_enabled.get() is not True:
            raise ReasoningError('AUTHORIZATION_UNAVAILABLE', _DELIVERY_ERRORS['AUTHORIZATION_UNAVAILABLE'])
        async with open_document_transport(
            request, conversation_id, source_authority=source_authority, cancelled=cancelled, deadline=deadline,
        ) as lease:
            engine = request.app.state.engine
            async with aclosing(engine.run_document_task(lease, objective, handles=lease.sources(), present=True)) as events:
                async for event in events:
                    if terminal is not None or len(buffered) == _MAX_EVENTS:
                        raise ValueError('Invalid document presentation event sequence')
                    event = _presentation_event(event)
                    if not buffered:
                        task_id = event['task_id']
                    elif event['task_id'] != task_id:
                        raise ValueError('Mixed document presentation tasks')
                    buffered.append(event)
                    if event['type'] in _TERMINALS:
                        terminal = event
            if terminal is None:
                raise ValueError('Missing document presentation terminal')
            if terminal['type'] == 'task_completed' and not isinstance(terminal['answer'], dict):
                raise ValueError('Missing verified document presentation')
            if terminal['type'] == 'task_completed' or terminal.get('partial') is not None:
                if terminal['type'] == 'task_cancelled':
                    raise ValueError('Cancelled document presentation contains evidence')
                lease.require_buffered_agent_result()
    except ReasoningError as exc:
        code = exc.code
    except Exception:
        code = 'OPERATION_UNAVAILABLE'
    else:
        return tuple(buffered)

    return _delivery_failure(code, task_id)


async def run_local_document_workflow(
    request: Request,
    conversation_id: str,
    objective: str,
    *,
    source_policy: LocalSourcePolicy,
    sources: tuple[tuple[str, bytes], ...],
    cancelled: Callable[[], bool] = lambda: False,
    deadline: float | None = None,
) -> tuple[dict, ...]:
    """Register independently approved bytes and use the existing buffered workflow."""
    try:
        if type(source_policy) is not LocalSourcePolicy:
            raise ReasoningError('AUTHORIZATION_UNAVAILABLE', _DELIVERY_ERRORS['AUTHORIZATION_UNAVAILABLE'])
        _validate_source_batch(sources)
        deadline = _document_deadline(conversation_id, deadline)
        with source_policy.open_authority(request, conversation_id, cancelled=cancelled, deadline=deadline) as authority:
            for source in sources:
                authority.register(*source)
            events = await run_document_workflow(
                request, conversation_id, objective, source_authority=authority,
                cancelled=cancelled, deadline=deadline,
            )
        _check_delivery_deadline(events, deadline)
        return events
    except ReasoningError as exc:
        code = exc.code
    except Exception:
        code = 'OPERATION_UNAVAILABLE'
    return _delivery_failure(code, uuid4().hex)


def _validate_source_batch(sources: tuple[tuple[str, bytes], ...]) -> None:
    if type(sources) is not tuple or not 1 <= len(sources) <= MAX_INPUTS:
        raise ReasoningError('INPUT_LIMIT', _DELIVERY_ERRORS['INPUT_LIMIT'])
    if any(type(source) is not tuple or len(source) != 2 for source in sources):
        raise ReasoningError('SOURCE_UNAVAILABLE', _DELIVERY_ERRORS['SOURCE_UNAVAILABLE'])


async def run_server_source_document_workflow(
    request: Request,
    conversation_id: str,
    objective: str,
    *,
    sources: tuple[tuple[str, bytes], ...],
    provider: ServerSourceProvider | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    deadline: float | None = None,
) -> tuple[dict, ...]:
    """Compose approved server metadata with the existing internal document workflow."""
    try:
        _validate_source_batch(sources)
        deadline = _document_deadline(conversation_id, deadline)
        with open_server_source_policy(
            request, conversation_id, tuple(reference for reference, _ in sources),
            provider=provider, cancelled=cancelled, deadline=deadline,
        ) as policy:
            events = await run_local_document_workflow(
                request, conversation_id, objective, source_policy=policy, sources=sources,
                cancelled=cancelled, deadline=deadline,
            )
        _check_delivery_deadline(events, deadline)
        return events
    except ReasoningError as exc:
        code = exc.code
    except Exception:
        code = 'OPERATION_UNAVAILABLE'
    return _delivery_failure(code, uuid4().hex)
