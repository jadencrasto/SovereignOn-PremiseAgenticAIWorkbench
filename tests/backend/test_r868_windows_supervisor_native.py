"""Opt-in non-network Windows checks using disposable interpreter workers only."""

from contextlib import ExitStack
import os
from pathlib import Path
import sys
from time import monotonic, sleep

import pytest

from backend.multimodal import document_recovery_supervisor as supervisor
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal import document_windows_supervisor as windows
from backend.multimodal.document_reasoning import ReasoningError
from test_r863_source_maintenance_offline import authority  # noqa: F401


pytestmark = pytest.mark.skipif(
    os.name != 'nt' or os.environ.get('SOVEREIGN_R868_DISPOSABLE_NATIVE_TEST') != '1',
    reason='Requires an approved Windows disposable-process run',
)


@pytest.mark.parametrize('exit_path', ['graceful', 'last_job_handle_closed', 'shared_maintenance'])
def test_disposable_worker_private_admission_and_retained_handle(exit_path, tmp_path, monkeypatch, authority):
    root = Path(__file__).resolve().parents[2]
    ready = tmp_path / 'child-ready'
    outcome = tmp_path / 'child-error'
    entry = tmp_path / 'disposable_worker.py'
    app, database = authority if exit_path == 'shared_maintenance' else (None, tmp_path / 'unused.db')
    if app is None:
        workload = (
            '    if nonce != "a" * 32:\n'
            '        raise SystemExit(2)\n'
            '    record_ready()\n'
            '    raise SystemExit(0 if native.wait_process(stop, 12000) else 3)\n'
        )
    else:
        import starlette

        # Use the already installed, local dependencies only in this isolated fixture.
        site = Path(starlette.__file__).resolve().parents[1]
        workload = (
            f'    sys.path.append({str(site)!r})\n'
            '    import asyncio\n'
            '    import selectors\n'
            '    from types import SimpleNamespace\n'
            '    from starlette.applications import Starlette\n'
            '    from backend.multimodal import document_source_recovery as recovery\n'
            '    from backend.multimodal.document_recovery_supervisor import WorkerAdmission\n'
            '    class OfflineSelector(selectors.SelectSelector):\n'
            '        def select(self, timeout=None):\n'
            '            if self.get_map(): raise RuntimeError("I/O registration forbidden")\n'
            '            return []\n'
            '    class OfflineLoop(asyncio.SelectorEventLoop):\n'
            '        def __init__(self): super().__init__(OfflineSelector())\n'
            '        def _make_self_pipe(self): pass\n'
            '        def _close_self_pipe(self): pass\n'
            '    async def lifecycle():\n'
            f'        database = Path({str(database)!r})\n'
            '        app = Starlette()\n'
            '        app.state.settings = SimpleNamespace(tasks_db_path=database)\n'
            '        app.state.startup_complete = False\n'
            '        app.state.source_supervisor_admission = WorkerAdmission(database, nonce)\n'
            '        registration = recovery.register_recovery_worker(app)\n'
            '        record_ready()\n'
            '        if not native.wait_process(stop, 12000):\n'
            '            raise SystemExit(3)\n'
            '        recovery.acknowledge_recovery_worker_shutdown(app, registration)\n'
            '    with asyncio.Runner(loop_factory=OfflineLoop) as runner:\n'
            '        runner.run(lifecycle())\n'
        )
    entry.write_text(
        'import sys\n'
        'import os\n'
        'from pathlib import Path\n'
        'def deny_network(event, args):\n'
        '    if event.startswith("socket.") or event == "subprocess.Popen":\n'
        '        raise RuntimeError("Disposable native worker is offline")\n'
        'sys.addaudithook(deny_network)\n'
        f'sys.path.insert(0, {str(root)!r})\n'
        'from backend.multimodal.document_supervisor_worker import _bootstrap_handles, _read_nonce\n'
        'from backend.runtime.windows_recovery_worker import WindowsRecoveryWorkerAPI\n'
        'native = WindowsRecoveryWorkerAPI()\n'
        'read, stop = _bootstrap_handles(sys.argv[1:])\n'
        'def record_ready():\n'
        f'    path = Path({str(ready)!r})\n'
        '    temporary = path.with_suffix(".pending")\n'
        '    temporary.write_text(str(os.getpid()), encoding="ascii")\n'
        '    temporary.replace(path)\n'
        'try:\n'
        '    nonce = _read_nonce(native, read)\n'
        + workload +
        'except Exception as error:\n'
        '    import traceback\n'
        '    frames = [frame.name for frame in traceback.extract_tb(error.__traceback__)]\n'
        '    detail = (type(error).__name__, getattr(error, "winerror", None), frames)\n'
        f'    Path({str(outcome)!r}).write_text(repr(detail), encoding="ascii")\n'
        '    raise SystemExit(4)\n'
        'finally:\n'
        '    native.close(stop)\n', encoding='utf-8',
    )
    monkeypatch.setattr(windows, '_worker_command', lambda: (sys._base_executable, '-I', '-B', str(entry)))
    with ExitStack() as cleanup:
        boundary = windows._WindowsProcessBoundary(database)
        cleanup.callback(boundary.close)
        if app is None:
            identity = boundary.launch(supervisor.WorkerAdmission(database, 'a' * 32), timeout_seconds=5)
        else:
            # Native Job/IPC and registration stay real. Skip operator privilege and host ACL policy for the fixture.
            monkeypatch.setattr(supervisor, '_authorize_operator', lambda _: None)
            owner = supervisor.DeploymentSupervisor(database, boundary)
            identity = owner.start_worker()
        api = boundary._native
        handles = boundary._workers[identity]
        deadline = monotonic() + 5
        while not ready.is_file() and api.running(handles.process) and monotonic() < deadline:
            sleep(0.02)
        assert ready.is_file(), outcome.read_text(encoding='utf-8') if outcome.exists() else api.job_counts(handles.job)
        assert database.exists() == (app is not None)
        assert int(ready.read_text(encoding='ascii')) == identity.pid
        assert (api.running(handles.process), api.job_counts(handles.job), api.in_job(handles.process, handles.job)) == (
            True, (1, 1), True,
        )
        assert boundary.observe().workers[0].identity == identity
        assert identity.pid != os.getpid() and identity.created > 0
        current = api.k.GetCurrentProcess()
        observer = api.duplicate_observation_process(handles.process, current)
        cleanup.callback(api.close, observer)
        assert api.process_identity(observer) == (identity.pid, identity.created)
        if exit_path == 'graceful':
            boundary.shutdown(identity, timeout_seconds=3)
            assert not boundary.observe().workers[0].running
        elif exit_path == 'last_job_handle_closed':
            boundary.close()
        else:
            recovery.quarantine_source_authority(app)
            path = recovery._coordination_path(database)
            generation = recovery._read_shared_state(path).generation
            recovery.set_recovery_maintenance(database, generation, active=True)
            with pytest.raises(ReasoningError):
                owner.start_worker()
            owner.drain(generation, timeout_seconds=5)
            assert not boundary.observe().workers[0].running
            owner.release_maintenance(generation)
            released = recovery._read_shared_state(path)
            assert released.pending and released.generation != generation
        assert api.wait_process(observer, 3000)
        assert api.process_identity(observer) == (identity.pid, identity.created)
