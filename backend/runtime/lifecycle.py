"""Lifecycle coordination prototype; experimental Windows driver is separate.

This module is not an authorization boundary and is not wired to the launcher.
The Windows driver is unverified and cannot establish listener identity;
PIDs alone are unsafe. The default adapter continues to fail closed.
"""
from enum import Enum
from math import isfinite
from typing import Protocol


class State(str, Enum):
    NEW = 'new'
    STARTING = 'starting'
    READY = 'ready'
    STOPPED = 'stopped'
    FAILED = 'failed'
    CLEANUP_UNVERIFIED = 'cleanup_unverified'


class ContainmentDriver(Protocol):
    """Adapter contract; native Windows behavior has not been verified."""
    def open_containment(self) -> object: ...
    def start_contained(self, containment: object, role: str) -> object: ...
    def is_running(self, root: object) -> bool: ...
    def listener_state(self, containment: object, root: object, role: str) -> str: ...
    def health_ready(self, role: str) -> bool: ...
    def stop_containment(self, containment: object) -> bool: ...


class UnavailableDriver:
    """Fail closed instead of starting or killing anything on an unsupported host."""
    def open_containment(self):
        raise RuntimeError('Verified process containment is unavailable')


class RuntimeCoordinator:
    """Explicitly polled state machine; no sockets, OS processes, or timers itself.

    A driver contract and supplied preflight result are assumptions, not OS
    ownership evidence. Use only with synthetic drivers until independently
    verified native containment and listener correlation are implemented.
    """
    def __init__(self, driver=None, timeout_seconds=30.0):
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
            raise ValueError('Invalid startup deadline')
        self.driver = driver if driver is not None else UnavailableDriver()
        self.timeout_seconds = timeout_seconds
        self.state = State.NEW
        self.failure = None
        self._job = None
        self._roots = {}
        self._deadline = None
        self._last_time = None
        self._stop_attempted = False

    def start(self, preflight: dict, now: float):
        if self.state != State.NEW:
            raise RuntimeError('Coordinator already used')
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not isfinite(now):
            raise ValueError('Invalid monotonic time')
        if preflight.get('ready') is not True:
            self.failure = 'preflight_not_ready'
            self.state = State.FAILED
            return self.state
        self._deadline = now + self.timeout_seconds
        self._last_time = now
        try:
            self._job = self.driver.open_containment()
            if self._job is None:
                raise RuntimeError('Ownership unavailable')
            self.state = State.STARTING
            for role in ('backend', 'frontend'):
                root = self.driver.start_contained(self._job, role)
                if root is None:
                    raise RuntimeError('Ownership unavailable')
                self._roots[role] = root
        except Exception:
            self.failure = 'containment_or_start_failed'
            self._cleanup(failed=True)
        except BaseException:
            self.failure = 'startup_interrupted'
            try:
                self._cleanup(failed=True)
            except BaseException:
                # Preserve the original interruption; cleanup already marked
                # its outcome uncertain and cannot be treated as successful.
                self.state = State.CLEANUP_UNVERIFIED
            raise
        return self.state

    def poll(self, now: float):
        if self.state not in (State.STARTING, State.READY):
            return self.state
        if (not isinstance(now, (int, float)) or isinstance(now, bool) or
                not isfinite(now) or now < self._last_time):
            self.failure = 'clock_unverified'
            return self._cleanup(failed=True)
        self._last_time = now
        if self.state == State.STARTING and now >= self._deadline:
            self.failure = 'startup_timeout'
            return self._cleanup(failed=True)
        try:
            # Check every retained root even if an earlier listener is absent;
            # an exited sibling must not be hidden until the startup deadline.
            for root in self._roots.values():
                if self.driver.is_running(root) is not True:
                    self.failure = 'child_exited_or_unknown'
                    return self._cleanup(failed=True)
            listener_pending = False
            for role, root in self._roots.items():
                listener = self.driver.listener_state(self._job, root, role)
                if listener == 'absent' and self.state == State.STARTING:
                    listener_pending = True
                elif listener != 'owned':
                    self.failure = 'listener_ownership_unverified'
                    return self._cleanup(failed=True)
            if listener_pending:
                return self.state

            healthy = True
            for role, root in self._roots.items():
                if self.driver.is_running(root) is not True or self.driver.listener_state(self._job, root, role) != 'owned':
                    self.failure = 'listener_ownership_changed'
                    return self._cleanup(failed=True)
                if self.driver.health_ready(role) is not True:
                    healthy = False
                    break
            for role, root in self._roots.items():
                if self.driver.is_running(root) is not True or self.driver.listener_state(self._job, root, role) != 'owned':
                    self.failure = 'listener_ownership_changed'
                    return self._cleanup(failed=True)
            if healthy:
                self.state = State.READY
            elif self.state == State.READY:
                self.failure = 'health_lost_or_unknown'
                return self._cleanup(failed=True)
        except Exception:
            self.failure = 'readiness_check_failed'
            return self._cleanup(failed=True)
        except BaseException:
            self.failure = 'readiness_interrupted'
            try:
                self._cleanup(failed=True)
            except BaseException:
                self.state = State.CLEANUP_UNVERIFIED
            raise
        return self.state

    def stop(self):
        if self.state in (State.STOPPED, State.FAILED, State.CLEANUP_UNVERIFIED):
            return self.state
        return self._cleanup(failed=False)

    def _cleanup(self, *, failed):
        if self._job is None:
            self.state = State.FAILED if failed else State.STOPPED
            return self.state
        if self._stop_attempted:
            return self.state
        self._stop_attempted = True
        try:
            confirmed = self.driver.stop_containment(self._job) is True
        except Exception:
            confirmed = False
        except BaseException:
            self.state = State.CLEANUP_UNVERIFIED
            raise
        self.state = (State.FAILED if failed else State.STOPPED) if confirmed else State.CLEANUP_UNVERIFIED
        return self.state
