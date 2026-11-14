"""Read-only handle observations; not an authenticated independent channel.

Handles must already be valid in this process. This module never duplicates,
opens, closes, launches or terminates a process and cannot establish job identity.
Native use requires separate authorization and an independently reviewed channel.
"""
from dataclasses import dataclass
from time import monotonic_ns
from uuid import UUID

from tools.process_test_supervisor.outer_controller import SCENARIOS


@dataclass(frozen=True)
class ProcessObservation:
    schema_version: int
    operation_id: str
    scenario: str
    observer: str
    observed_ns: int
    process_id: int | None
    creation_time: int | None
    state: str
    error: str | None


def windows_observation_api():
    """Reuse GetProcessId/GetProcessTimes and zero-time process-handle wait."""
    from backend.runtime.windows_job_driver import WindowsNativeAPI
    from backend.runtime.windows_listener import WindowsListenerAPI
    return WindowsListenerAPI(WindowsNativeAPI())


def observe_process(operation_id, scenario, handle, expected_identity, *, api=None, clock=monotonic_ns):
    """Observe one borrowed local process handle; caller owns its lifetime.

    Identity expectations bind observations but do not authenticate who supplied
    them or prove the process is the fixture. Job membership and descendant
    completeness require a separately verified identity and acquisition channel.
    """
    try:
        if not isinstance(operation_id, str) or str(UUID(operation_id)) != operation_id:
            raise ValueError('Invalid operation identity')
    except (ValueError, AttributeError):
        raise ValueError('Invalid operation identity') from None
    if scenario not in SCENARIOS:
        raise ValueError('Invalid scenario')
    if (not isinstance(expected_identity, tuple) or len(expected_identity) != 2 or
            any(type(part) is not int or part <= 0 for part in expected_identity)):
        raise ValueError('Invalid process identity')
    timestamp = clock()
    if type(timestamp) is not int or timestamp < 0:
        raise ValueError('Invalid observation time')
    pid = created = None
    state, error = 'unknown', 'observation_unavailable'
    try:
        if handle is None:
            raise ValueError('Missing handle')
        native = api if api is not None else windows_observation_api()
        first = native.process_identity(handle)
        if (not isinstance(first, tuple) or len(first) != 2 or
                any(type(part) is not int or part <= 0 for part in first) or first != expected_identity):
            state, error = 'unknown', 'identity_mismatch'
        else:
            running = native.running(handle)
            second = native.process_identity(handle)
            if (not isinstance(second, tuple) or len(second) != 2 or
                    any(type(part) is not int or part <= 0 for part in second) or
                    second != first or type(running) is not bool):
                state, error = 'unknown', 'identity_or_state_changed'
            else:
                pid, created = first
                state, error = ('running' if running else 'exited'), None
    except Exception:
        pass
    return ProcessObservation(1, operation_id, scenario, 'local_handle_query_unverified',
                              timestamp, pid, created, state, error)
