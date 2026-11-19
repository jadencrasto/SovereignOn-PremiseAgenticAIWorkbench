"""Private entry for a fixed, single process backend launched by its Windows supervisor."""

import asyncio
from contextlib import suppress
import os
from pathlib import Path
import re
import sys


def _bootstrap_handles(args):
    # Only inherited handle slots are passed on the command line.
    if len(args) != 2 or any(not re.fullmatch('[1-9][0-9]{0,9}', arg) for arg in args):
        raise RuntimeError('Invalid supervised worker entry')
    reader, stop = map(int, args)
    if reader == stop or max(reader, stop) >= 0xFFFFFFFF:
        raise RuntimeError('Invalid supervised worker entry')
    return reader, stop


def _read_nonce(native, reader):
    try:
        data = native.read_ticket(reader)
        if not re.fullmatch(b'[0-9a-f]{32}', data):
            raise RuntimeError('Invalid worker admission')
        return data.decode('ascii')
    finally:
        native.close(reader)


async def _serve(server, native, stop):
    if native.wait_process(stop, 0):
        return False

    async def watch_stop():
        try:
            # The main loop checks a stop that arrives during startup. Do not
            # short-circuit Uvicorn's lifespan shutdown after a successful bind.
            while not server.startup_observed or not native.wait_process(stop, 0):
                await asyncio.sleep(0.05)
        finally:
            if server.startup_observed:
                server.should_exit = True

    watcher = asyncio.create_task(watch_stop())
    try:
        await server.serve()
        return server.startup_observed
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher


def _server(app, settings, native, stop):
    from backend.multimodal.document_reasoning import ReasoningError
    from backend.multimodal.document_recovery_supervisor import verify_worker_startup
    from uvicorn import Config, Server

    class SupervisedServer(Server):
        startup_observed = False

        async def main_loop(self):
            # Uvicorn reaches this hook only after lifespan startup and binding.
            # Returning leaves its normal listener and lifespan shutdown in charge.
            if self.should_exit or native.wait_process(stop, 0):
                return
            try:
                verify_worker_startup(app, publish=True)
            except ReasoningError:
                return
            self.startup_observed = True
            await super().main_loop()

    return SupervisedServer(Config(app, host=settings.backend_host, port=settings.backend_port, lifespan='on',
                                   workers=1, reload=False, timeout_graceful_shutdown=10, use_colors=False))


def _entry(args):
    if os.name != 'nt':
        raise RuntimeError('Supervised workers require Windows')
    reader, stop = _bootstrap_handles(args)
    from backend.runtime.windows_recovery_worker import WindowsRecoveryWorkerAPI

    native = WindowsRecoveryWorkerAPI()
    try:
        nonce = _read_nonce(native, reader)
        if native.wait_process(stop, 0):
            return 1
        from backend.config import settings
        from backend.multimodal.document_windows_supervisor import _require_supervised_profile

        _require_supervised_profile(settings)
        from backend.main import app
        from backend.multimodal.document_recovery_supervisor import WorkerAdmission

        # Uvicorn completes the existing lifespan admission before binding its socket.
        server = _server(app, settings, native, stop)
        app.state.source_supervisor_admission = WorkerAdmission(settings.tasks_db_path.resolve(strict=True), nonce)
        del nonce
        try:
            observed = asyncio.run(_serve(server, native, stop))
            return 0 if observed and app.state.source_recovery_registration is None else 1
        finally:
            if hasattr(app.state, 'source_supervisor_admission'):
                del app.state.source_supervisor_admission
    finally:
        native.close(stop)


if __name__ == '__main__':
    # -I discards caller Python paths. This is the fixed source tree of the launched script.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        result = _entry(sys.argv[1:])
    except Exception:
        result = 1  # Never print channel contents or an admission token on boot failure.
    raise SystemExit(result)
