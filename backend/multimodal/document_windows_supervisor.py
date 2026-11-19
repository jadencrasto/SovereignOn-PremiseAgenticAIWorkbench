"""Opt-in Windows lifetime ownership for the fixed supervised backend worker."""

from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
import ipaddress
import math
import os
from pathlib import Path
import re
import sys
from threading import Lock
from time import monotonic

from backend.multimodal.document_recovery_supervisor import (
    DeploymentSupervisor, OwnedWorker, ProcessIdentity, SupervisorSnapshot, WorkerAdmission,
    _authorize_operator, _process_identity, _valid_identity,
)
from backend.runtime.windows_backend_readiness import require_supervised_interpreter
from backend.runtime.windows_recovery_worker import WindowsRecoveryWorkerAPI
from backend.security.provenance_windows_native import WindowsKeyStoreNative


@dataclass(frozen=True, slots=True)
class _OwnedHandles:
    job: int
    process: int
    stop: int


@dataclass(frozen=True, slots=True)
class _WorkerCredentials:
    token: int
    identity: tuple[str, int]


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _require_supervised_profile(settings) -> None:
    # Reuse the existing authenticated chat-only profile, where startup does
    # not initialize the document vector store or its embedding service.
    from backend.health.capabilities import loopback_tags_url

    if (not _loopback(settings.backend_host) or settings.auth_enabled is not True
            or settings.auth_dev_override is not False or settings.local_chat_only is not True
            or settings.app_env.lower() not in ('development', 'dev')):
        raise RuntimeError('Supervised startup requires the authenticated loopback chat-only profile')
    loopback_tags_url(settings.ollama_base_url)


def _worker_command() -> tuple[str, ...]:
    require_supervised_interpreter()
    executable = Path(sys.executable).resolve(strict=True)
    if not executable.samefile(sys._base_executable):
        # A Windows venv redirector may create a second process before executing Python.
        raise RuntimeError('A direct Windows Python interpreter is required')
    bootstrap = Path(__file__).with_name('document_supervisor_worker.py').resolve(strict=True)
    return (str(executable), '-I', '-B', str(bootstrap))


class _WindowsProcessBoundary:
    def __init__(self, database: Path, *, native=None, worker_credentials: _WorkerCredentials | None = None):
        self._database = database
        self._owner = _process_identity()
        self._native = WindowsRecoveryWorkerAPI() if native is None else native
        self._worker_credentials = worker_credentials
        self._command = _worker_command()
        self._workers: dict[ProcessIdentity, _OwnedHandles] = {}
        self._resources = ExitStack()
        self._lock = Lock()
        self._uncertain = False
        self._closed = False

    def _check_owner(self):
        if self._closed or self._uncertain or _process_identity() != self._owner:
            raise RuntimeError('Supervisor process ownership unavailable')

    def _worker_state(self, identity, worker):
        native = self._native
        if native.process_identity(worker.process) != (identity.pid, identity.created):
            raise RuntimeError('Worker process identity changed')
        # A legitimate process exit may race the first job accounting query.
        for _ in range(2):
            running = native.running(worker.process)
            total, active = native.job_counts(worker.job)
            if native.running(worker.process) == running and active == int(running):
                if total != 1 or (running and not native.in_job(worker.process, worker.job)):
                    break
                return running
        raise RuntimeError('Incomplete job ownership or unexpected descendant')

    def observe(self) -> SupervisorSnapshot:
        with self._lock:
            self._check_owner()
            workers = tuple(OwnedWorker(identity, self._worker_state(identity, handles))
                            for identity, handles in self._workers.items())
            return SupervisorSnapshot(self._database, self._owner, True, True, workers)

    def launch(self, admission: WorkerAdmission, *, timeout_seconds: float) -> ProcessIdentity:
        with self._lock:
            self._check_owner()
            if (type(admission) is not WorkerAdmission or admission.database != self._database
                    or type(admission.nonce) is not str or not re.fullmatch('[0-9a-f]{32}', admission.nonce)
                    or not 0 < timeout_seconds <= 5):
                raise RuntimeError('Invalid worker launch')
            try:
                return self._launch(admission.nonce.encode('ascii'), monotonic() + timeout_seconds)
            except BaseException:
                self._uncertain = True
                raise

    def _launch(self, data: bytes, deadline: float) -> ProcessIdentity:
        native = self._native
        with ExitStack() as owned, ExitStack() as handoff, ExitStack() as primary_thread:
            stop = native.stop_event()
            owned.callback(native.close, stop)
            read, write = native.pipe()
            handoff.callback(native.close, write)
            handoff.callback(native.close, read)
            reader = native.inherit_reader(read)
            handoff.callback(native.close, reader)
            stop_wait = native.inherit_stop_wait(stop)
            handoff.callback(native.close, stop_wait)
            job = native.create_single_worker_job()
            # Register job closure immediately, before native process creation can fail.
            owned.callback(native.close, job)
            if monotonic() >= deadline:
                raise RuntimeError('Worker creation deadline exceeded')
            command = (*self._command, str(reader), str(stop_wait))
            credentials = self._worker_credentials
            if credentials is None:
                process, thread = native.create_worker(job, command, reader, stop_wait)  # Offline fixture only.
            else:
                process, thread = native.create_worker_as(job, command, reader, stop_wait, credentials.token)
            owned.callback(native.close, process)
            primary_thread.callback(native.close, thread)
            identity = ProcessIdentity(*native.process_identity(process))
            handles = _OwnedHandles(job, process, stop)
            if (not _valid_identity(identity) or identity == self._owner or identity in self._workers
                    or not self._worker_state(identity, handles)
                    or native.thread_identity(thread)[0] != identity.pid):
                raise RuntimeError('Worker creation identity unavailable')
            if credentials is not None:
                api = WindowsKeyStoreNative()
                token = api.open_process_token_for(process)
                with ExitStack() as token_scope:
                    token_scope.callback(api.close, token)
                    if api.supervised_worker_token_identity(token) != credentials.identity:
                        raise RuntimeError('Worker token identity changed')
            native.write_ticket(write, data)
            # Close the writer and both inheritable copies before any worker code can execute.
            handoff.close()
            if monotonic() >= deadline:
                raise RuntimeError('Worker creation deadline exceeded')
            native.resume(thread)
            primary_thread.close()
            self._resources.enter_context(owned.pop_all())
            self._workers[identity] = handles
            return identity

    def shutdown(self, identity: ProcessIdentity, *, timeout_seconds: float) -> None:
        with self._lock:
            self._check_owner()
            worker = self._workers.get(identity)
            if worker is None or not 0 < timeout_seconds <= 30:
                raise RuntimeError('Invalid owned worker shutdown')
            deadline = monotonic() + timeout_seconds
            if self._worker_state(identity, worker):
                self._native.signal_stop(worker.stop)
                remaining_ms = max(0, math.floor((deadline - monotonic()) * 1000))
                if not self._native.wait_process(worker.process, remaining_ms):
                    raise RuntimeError('Owned worker shutdown timed out')
            if self._worker_state(identity, worker) or monotonic() >= deadline:
                raise RuntimeError('Owned worker exit unavailable')

    def close(self) -> None:
        with self._lock:
            # Last job-handle close limits crash cleanup to these owned workers.
            # It records no worker acknowledgment and cannot release quarantine.
            self._closed = True
            self._resources.close()


@contextmanager
def open_windows_deployment_supervisor(
    database: Path, *, worker_primary_token: int | None = None,
    token_provider: Callable[[], AbstractContextManager[int]] | None = None,
):
    """Borrow one externally provisioned handle. The supervisor owns only its duplicate."""
    from backend.config import settings

    if os.name != 'nt':
        raise RuntimeError('Native supervision requires a local Windows deployment')
    if worker_primary_token is None and token_provider is None:
        raise RuntimeError('Approved filtered worker token provider unavailable')
    if worker_primary_token is not None and token_provider is not None:
        raise RuntimeError('Supply exactly one filtered worker token source')
    _require_supervised_profile(settings)
    canonical = database.resolve(strict=True)
    if canonical != settings.tasks_db_path.resolve(strict=True):
        raise RuntimeError('Worker deployment does not match configuration')
    owner_sid = _authorize_operator(database)
    native, api = WindowsRecoveryWorkerAPI(), WindowsKeyStoreNative()
    with ExitStack() as resources:
        token = None
        scope = token_provider() if token_provider is not None else nullcontext(worker_primary_token)
        with scope as supplied:
            if type(supplied) is not int or supplied <= 0:
                raise RuntimeError('A filtered primary token handle is required')
            token = native.duplicate_worker_token(supplied)
            resources.callback(native.close, token)
        if token is None:
            raise RuntimeError('Filtered worker token acquisition failed')
        with ExitStack() as operator_token:
            current = api.open_process_token()
            operator_token.callback(api.close, current)
            expected = (owner_sid, api.token_session_id(current))
        if api.supervised_worker_token_identity(token) != expected:
            raise RuntimeError('Worker token must match the operator account and session')
        credentials = _WorkerCredentials(token, expected)
        boundary = _WindowsProcessBoundary(canonical, native=native, worker_credentials=credentials)
        resources.callback(boundary.close)
        yield DeploymentSupervisor(database, boundary)
