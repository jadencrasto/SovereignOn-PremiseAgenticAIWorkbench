"""Request-scoped document presentation for trusted in-process application callers."""

from collections.abc import Callable
from contextlib import aclosing
from uuid import uuid4

from starlette.requests import Request

from backend.auth.request_context import request_tools_enabled
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_transport import SourceAuthority, open_document_transport


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
        code = exc.code if exc.code in _DELIVERY_ERRORS else 'OPERATION_UNAVAILABLE'
    except Exception:
        code = 'OPERATION_UNAVAILABLE'
    else:
        return tuple(buffered)

    return ({
        'type': 'task_cancelled' if code == 'CANCELLED' else 'task_failed',
        'task_id': task_id,
        'error_code': code,
        'error': _DELIVERY_ERRORS[code],
        'step_id': None,
        'operation': None,
        'partial': None,
    },)
