"""Bounded cancellation/drain coordinator; no Windows handles created here.

The caller owns the buffer, OVERLAPPED, event and handle passed as `resources`.
This module is not wired to the disabled listener; backend contracts require
independent Windows verification before that can be safe.
"""
from dataclasses import dataclass
from enum import Enum


class Completion(Enum):
    COMPLETE = 'complete'
    PENDING = 'pending'


@dataclass(frozen=True)
class Started:
    state: Completion
    transferred: int | None = None


class UnsafePendingIO(RuntimeError):
    """The caller must terminate the isolated process without freeing resources."""


def run_operation(backend, resources, deadline_ms: int, drain_ms: int) -> int:
    """Run a single operation; return byte count only after completion.

    Backend is an internal test/adapter interface, never a client command. Its
    `start` must return a Started only after the OS initiation outcome is known.
    If initiation itself raises after issuing I/O, this interface is insufficient
    and must not be used without an adapter guaranteeing a definite outcome.
    """
    if type(deadline_ms) is not int or type(drain_ms) is not int or deadline_ms <= 0 or drain_ms <= 0:
        raise ValueError('positive bounded deadlines required')
    started = backend.start(resources)
    if not isinstance(started, Started):
        raise ValueError('invalid start outcome')
    if started.state is Completion.COMPLETE:
        count = started.transferred
    elif started.state is Completion.PENDING:
        try:
            if not backend.wait(resources, deadline_ms):
                raise TimeoutError('I/O deadline exceeded')
            count = backend.result(resources)
        except BaseException as original:
            # Cancellation is a request, not completion. Retain resources until
            # wait and result have confirmed completion, including cancellation.
            try:
                backend.cancel(resources)
                if not backend.wait(resources, drain_ms):
                    raise UnsafePendingIO('completion after cancellation unavailable')
                backend.drain(resources)
            except BaseException as exc:
                raise UnsafePendingIO('operation may still be pending; process must stop') from exc
            raise original
    else:
        raise ValueError('invalid completion state')
    if type(count) is not int or count < 0:
        raise ValueError('invalid transferred byte count')
    return count
