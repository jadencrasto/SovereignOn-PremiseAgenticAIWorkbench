"""Native composition for an independently approved, immutable R5.5 guest.

The public fixture and application launchers remain gated. This module's
executable entry accepts only the two fixed worker roles, not controller runs.
"""
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from hashlib import file_digest, sha256
import json
from math import isfinite
import os
from pathlib import Path
import runpy
import secrets
import sys
import time

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
_FIXTURE_RELATIVE = 'tests/fixtures/i52_processes/non_network_workload.py'
if __name__ == '__main__':
    # The fixed -I worker command loads the reviewed tree, never the cwd.
    sys.path.insert(0, str(_REPO))

from backend.runtime.windows_job_driver import WindowsJobDriver, WindowsNativeAPI
from tools.process_test_supervisor.handoff import (
    AuthenticatedChannel, Binding, ControllerHandoff, HandoffError, ObserverHandoff, PeerIdentity,
    PeerPrincipal, _handle_slot, decode, encode, generation,
)
from tools.process_test_supervisor.outer_controller import OuterController, SCENARIOS
from tools.process_test_supervisor.windows_parentage import ParentageEvidence, WindowsProcessAudit
from tools.process_test_supervisor.windows_pipe import NativePipe, WindowsPipeAPI


_SOURCE_FILES = (
    'backend/runtime/windows_job_driver.py',
    'backend/security/provenance_windows_native.py',
    'tools/process_test_supervisor/handoff.py',
    'tools/process_test_supervisor/native_bootstrap.py',
    'tools/process_test_supervisor/outer_controller.py',
    'tools/process_test_supervisor/windows_parentage.py',
    'tools/process_test_supervisor/windows_pipe.py',
    _FIXTURE_RELATIVE,
)


@dataclass(frozen=True)
class FixtureManifest:
    """Pins critical inputs. Guest immutability and the import closure are external."""
    principal: PeerPrincipal
    hashes: dict[str, str]

    def verify(self):
        files = {name: _REPO / name for name in _SOURCE_FILES}
        files['python.exe'] = Path(sys.executable).resolve(strict=True)
        if type(self.principal) is not PeerPrincipal or set(self.hashes) != files.keys():
            raise HandoffError('Incomplete reviewed fixture manifest')
        pins = dict(self.hashes)
        for name, path in files.items():
            with path.open('rb') as stream:
                if file_digest(stream, 'sha256').hexdigest() != pins[name]:
                    raise HandoffError('Reviewed fixture hash mismatch')
        return sha256(encode({'principal': asdict(self.principal), 'hashes': dict(sorted(pins.items()))})).hexdigest()


def _identity(api, handle):
    return PeerIdentity(generation(api.process_identity(handle)), api.process_principal(handle),
                        api.process_image(handle))


def _require_fixture_identity(identity, principal):
    if (identity.principal != principal
            or os.path.normcase(identity.image) != os.path.normcase(str(Path(sys.executable).resolve()))):
        raise HandoffError('Fixture executable or principal mismatch')


def _profile(pipe, binding, controller, deadline):
    return encode({'pipe': pipe, 'binding': asdict(binding) if binding is not None else None,
                   'controller': asdict(controller), 'deadline': deadline})


def _read_profile(frame, *, observer=True):
    values = decode(frame)
    if values.keys() != {'pipe', 'binding', 'controller', 'deadline'}:
        raise HandoffError('Unexpected native bootstrap fields')
    if observer:
        binding = Binding(**values['binding'])
    elif values['binding'] is None:
        binding = None
    else:
        raise HandoffError('Workload binding must arrive over the authenticated pipe')
    identity = values['controller']
    if identity.keys() != {'process', 'principal', 'image'}:
        raise HandoffError('Unexpected native bootstrap identity')
    controller = PeerIdentity(generation(identity['process']), PeerPrincipal(**identity['principal']), identity['image'])
    deadline = values['deadline']
    if type(deadline) not in (int, float) or not isfinite(deadline) or not 0 < deadline - time.monotonic() <= 5:
        raise HandoffError('Native bootstrap deadline invalid')
    return values['pipe'], binding, controller, deadline


def _pipe_name():
    return '\\\\.\\pipe\\SovereignAI-R55-' + secrets.token_hex(32)


def _worker_command(role, *arguments):
    return [str(Path(sys.executable).resolve()), '-I', '-B', str(_HERE), role, *map(str, arguments)]


def _ordinary_command(scenario, op):
    return [str(Path(sys.executable).resolve()), '-I', '-B', str(_REPO / _FIXTURE_RELATIVE),
            '--scenario', scenario, '--operation-id', op]


def _close_observer(native, process, thread, job):
    try:
        if native.wait_process(process, 300) is not True:
            native.terminate_job(job)
            if native.wait_process(process, 1000) is not True:
                raise HandoffError('Observer termination unverified')
        if native.job_counts(job) != (1, 0):
            raise HandoffError('Observer job cleanup unverified')
    finally:
        try:
            native.close(thread)
        finally:
            native.close(process)


def _start_observer(resources, api, native, profile, expected_principal):
    job = native.create_kill_on_close_job()
    resources.callback(native.close, job)
    with ExitStack() as inherited:
        parent = native.inheritable_controller_query()
        inherited.callback(native.close, parent)
        reader = api.bootstrap_reader(profile)
        inherited.callback(api.close, reader)
        command = _worker_command('--observer', parent, reader)
        process, thread = native.create_suspended_observer(job, command, (parent, reader))
        resources.callback(_close_observer, native, process, thread, job)
    expected = _identity(api, process)
    _require_fixture_identity(expected, expected_principal)
    if native.in_job(process, job) is not True or native.running(process) is not True:
        raise HandoffError('Observer ownership unverified')
    native.resume(thread)
    return process, expected


def _server_channel(pipe, api, handle, expected, deadline):
    pipe.pin_liveness(lambda: api.running(handle))
    pipe.accept(deadline)
    channel = AuthenticatedChannel(pipe, api, handle, expected, remote='client')
    channel.check(deadline)
    return channel


def verified_fixture_handoff(handoff, native, command=None):
    """Guard the existing driver's native-only entry, separate from public CLI."""
    return (type(handoff) is ControllerHandoff and handoff._authenticated
            and handoff._native is native and type(handoff._observer._transport) is NativePipe
            and type(handoff._observer._native) is WindowsPipeAPI
            and handoff._observer._native.process is native
            and (handoff.binding.scenario != 'descendant' or type(handoff._creation_audit) is ParentageEvidence)
            and command is not None and tuple(command) == getattr(handoff, '_fixture_command', None))


def run_reviewed_fixture(manifest, scenario):
    """Controller entry for a separately authorized outer guest harness.

    The caller must enforce the independent VM deadline and immutable runtime.
    There is intentionally no command-line flag enabling this controller.
    """
    if type(manifest) is not FixtureManifest or scenario not in SCENARIOS:
        raise HandoffError('Unrecognized reviewed fixture')
    digest = manifest.verify()
    native = WindowsNativeAPI()
    api = WindowsPipeAPI(native)
    controller = _identity(api, api.current_process())
    _require_fixture_identity(controller, manifest.principal)
    with ExitStack() as resources:
        # OuterController supplies the operation and the one shared deadline.
        placeholder = _ordinary_command(scenario, '00000000-0000-0000-0000-000000000000')
        driver = WindowsJobDriver({'backend': placeholder, 'frontend': placeholder}, native)

        def build(operation, requested, deadline):
            binding = Binding.fresh(operation, requested, digest)
            root_channel = None
            audit = None
            if requested == 'descendant':
                audit = ParentageEvidence(WindowsProcessAudit(), native, controller.process, deadline)
                name = _pipe_name()
                root_pipe = NativePipe.listen(api, name, controller.principal.logon_sid)
                resources.callback(root_pipe.close)
                command = _worker_command('--cooperative', _profile(name, None, controller, deadline).hex())

                def root_channel(handle, identity):
                    expected = _identity(api, handle)
                    _require_fixture_identity(expected, manifest.principal)
                    if expected.process != identity:
                        raise HandoffError('Root generation changed before pipe accept')
                    channel = _server_channel(root_pipe, api, handle, expected, deadline)
                    greeting = binding.message('ROOT_HELLO', 0)
                    binding.fields(channel.exchange(greeting, deadline), 'ROOT_HELLO', 0, ())
                    return channel
            else:
                command = _ordinary_command(requested, operation)
            observer_name = _pipe_name()
            observer_pipe = NativePipe.listen(api, observer_name, controller.principal.logon_sid)
            # Close the connection before waiting on the observer, even on failure.
            try:
                observer, expected = _start_observer(
                    resources, api, native, _profile(observer_name, binding, controller, deadline), manifest.principal,
                )
            except BaseException:
                observer_pipe.close()
                raise
            resources.callback(observer_pipe.close)
            channel = _server_channel(observer_pipe, api, observer, expected, deadline)
            handoff = ControllerHandoff(binding, channel, native, deadline, root_channel=root_channel, creation_audit=audit)
            handoff._fixture_command = tuple(command)
            driver.commands['backend'] = command
            return handoff

        return OuterController(driver, scenario=scenario, handoff_factory=build).run(5)


def _worker_channel(resources, api, name, controller_handle, expected, deadline):
    if _identity(api, controller_handle) != expected:
        raise HandoffError('Controller bootstrap identity mismatch')
    _require_fixture_identity(_identity(api, api.current_process()), expected.principal)
    pipe = NativePipe.connect(api, name, deadline)
    resources.callback(pipe.close)
    pipe.pin_liveness(lambda: api.running(controller_handle))
    channel = AuthenticatedChannel(pipe, api, controller_handle, expected, remote='server')
    channel.check(deadline)
    return pipe, channel


def _observer_worker(parent, reader):
    if not all(_handle_slot(handle) for handle in (parent, reader)) or parent == reader:
        raise HandoffError('Observer bootstrap handle list invalid')
    native = WindowsNativeAPI()
    api = WindowsPipeAPI(native)
    with ExitStack() as resources:
        resources.callback(api.close, parent)
        try:
            name, binding, expected, deadline = _read_profile(api.read_bootstrap(reader))
        finally:
            api.close(reader)
        pipe, channel = _worker_channel(resources, api, name, parent, expected, deadline)
        audit = (ParentageEvidence(WindowsProcessAudit(), native, expected.process, deadline)
                 if binding.scenario == 'descendant' else None)
        observer = ObserverHandoff(binding, native, exact_job=True, creation_audit=audit, resume_deadline=deadline)
        resources.callback(observer.close)
        while observer.serve_once(channel, deadline + 4):
            pass
        # Keep the authenticated process alive until the controller receives
        # RELEASED and closes the pipe. The observer's job duplicate is closed.
        try:
            pipe.receive(deadline + 4)
        except (HandoffError, OSError):
            return 0
        raise HandoffError('Unexpected traffic after observer release')


def _cooperative_worker(profile):
    name, _, expected, deadline = _read_profile(profile, observer=False)
    native = WindowsNativeAPI()
    api = WindowsPipeAPI(native)
    if native.in_job(api.current_process(), None) is not True:
        raise HandoffError('Cooperative workload is outside a job')
    with ExitStack() as resources:
        # The workload inherits no handles. Open only the manifest-bound
        # controller with query/synchronize access and pin its full generation.
        parent = api.open_peer(expected.process[0])
        resources.callback(api.close, parent)
        pipe, channel = _worker_channel(resources, api, name, parent, expected, deadline)
        greeting = channel.receive(deadline)
        if greeting.get('scenario') != 'descendant':
            raise HandoffError('Cooperative worker scenario invalid')
        binding = Binding(*(greeting.get(field) for field in ('op', 'scenario', 'manifest', 'nonce', 'job_token')))
        binding.fields(greeting, 'ROOT_HELLO', 0, ())
        channel.send(greeting, deadline)
        fixture = runpy.run_path(str(_REPO / _FIXTURE_RELATIVE))
        fixture['cooperate'](binding.op, binding, channel, api.process_identity(api.current_process()), native, deadline)
        # Keep the root, original job membership and pipe client identity alive
        # while the controller rechecks evidence and resumes the child.
        try:
            pipe.receive(deadline + 2)
        except (HandoffError, OSError):
            return 0
        raise HandoffError('Unexpected traffic after thread release')


def _main(argv):
    try:
        if len(argv) == 3 and argv[0] == '--observer':
            return _observer_worker(int(argv[1]), int(argv[2]))
        if len(argv) == 2 and argv[0] == '--cooperative' and len(argv[1]) <= 8200:
            return _cooperative_worker(bytes.fromhex(argv[1]))
    except Exception:
        # Win32 error details and private bootstrap fields stay out of output.
        return 2
    return 2


if __name__ == '__main__':
    raise SystemExit(_main(sys.argv[1:]))
