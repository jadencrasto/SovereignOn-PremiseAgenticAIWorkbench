"""Operator policy with simulated Windows APIs and disposable app lifespans."""

import asyncio
from contextlib import ExitStack, aclosing, contextmanager
import ctypes
import json
import os
from pathlib import Path
import stat
import struct

import pytest
from starlette.applications import Starlette

from backend.agent.deployment_documents import get_deployment_documents, inspect_document_worker
from backend.health.capabilities import capability_report
from backend.multimodal import document_source_maintenance as maintenance
from backend.multimodal.document_source_recovery import quarantine_source_authority
from backend.security.provenance_windows_native import FILE_ALL_ACCESS, FileFacts, FileSecurity, TokenFacts, WindowsKeyStoreNative
from test_a22_async_offline import run_offline
from test_r848_document_agent_lease_offline import PASSAGE, denied
from test_r857_deployment_composition_offline import CONVERSATION, create_app, establish, prepare, tools_allowed  # noqa: F401
from test_r863_source_maintenance_offline import authority  # noqa: F401


_OPERATOR = 'S-1-5-21-10-20-30-1100'
_SYSTEM = 'S-1-5-18'
_OTHER = 'S-1-5-32-545'
windows = pytest.mark.skipif(os.name != 'nt', reason='Windows path and token policy')


class _TokenApi(WindowsKeyStoreNative):
    def __init__(self, elevation_type, elevated, member):
        self.elevation_type = elevation_type
        self.elevated = elevated
        self.member = member
        self.thread = None
        self.identity = TokenFacts(_OPERATOR, True, _OPERATOR, 2)
        self.closed = []

    def open_thread_token(self):
        return self.thread

    def open_process_token(self):
        return 1

    def token_facts(self, handle):
        return self.identity

    def close(self, handle):
        self.closed.append(handle)

    def _token_info(self, handle, info_class, output):
        assert handle == 1
        output.value = {18: self.elevation_type, 20: self.elevated}[info_class]
        return ctypes.sizeof(output)

    def _function(self, library, name, result, *args):
        if name == 'CreateWellKnownSid':
            def create(kind, domain, sid, length):
                assert kind == 26 and domain is None
                return 1
            return create
        assert name == 'CheckTokenMembership'

        def check(token, sid, member):
            assert token is None
            member._obj.value = self.member
            return 1

        return check


@windows
@pytest.mark.parametrize(('elevation_type', 'elevated', 'member', 'permitted'), [
    (1, 1, 1, True), (2, 1, 1, True), (3, 1, 1, False),
    (2, 0, 1, False), (2, 1, 0, False), (0, 1, 1, False),
])
def test_requires_token_elevation_and_enabled_admin_membership(elevation_type, elevated, member, permitted):
    api = _TokenApi(elevation_type, elevated, member)
    if permitted:
        assert maintenance._local_operator(api) == _OPERATOR
    else:
        with pytest.raises(maintenance.MaintenanceError, match='^LOCAL_OPERATOR_REQUIRED$'):
            maintenance._local_operator(api)
    assert api.closed == [1]


@windows
@pytest.mark.parametrize('context', ['impersonating', 'service', 'missing_identity', 'invalid_sid'])
def test_uncertain_identity_is_denied(context):
    api = _TokenApi(2, 1, 1)
    if context == 'impersonating':
        api.thread = 22
    elif context == 'service':
        api.identity = TokenFacts(_OPERATOR, True, _OPERATOR, 5)
    elif context == 'missing_identity':
        api.identity = None
    else:
        api.identity = TokenFacts('claim', True, 'claim', 2)
    with pytest.raises(maintenance.MaintenanceError, match='^LOCAL_OPERATOR_REQUIRED$'):
        maintenance._local_operator(api)
    assert api.closed == [22 if context == 'impersonating' else 1]


def _acl(*, owner=_OPERATOR, other=_SYSTEM, flags=0, control=0x1004):
    entries = []
    for sid in (_OPERATOR, other):
        _, revision, authority, *sub = sid.split('-')
        binary_sid = bytes((int(revision), len(sub))) + int(authority).to_bytes(6, 'big')
        binary_sid += struct.pack('<' + 'I' * len(sub), *map(int, sub))
        entries.append(struct.pack('<BBHI', 0, flags, 8 + len(binary_sid), FILE_ALL_ACCESS) + binary_sid)
    raw = struct.pack('<BBHHH', 2, 0, 8 + sum(map(len, entries)), 2, 0) + b''.join(entries)
    return FileSecurity(owner, control, raw)


class _Ntfs:
    def __init__(self):
        self.handles = {}
        self.acls = {}
        self.reparse = None
        self.counter = 0
        self.created = []

    def _open(self, path, directory):
        self.counter += 1
        self.handles[self.counter] = path, directory
        return self.counter

    def open_drive(self, path):
        return self._open(path, True)

    def volume_root(self, handle):
        assert handle in self.handles

    def open_child(self, parent, name, directory, *, inspect_acl):
        path = self.handles[parent][0] / name
        assert path.is_dir() if directory else path.is_file()
        return self._open(path, directory)

    def file_facts(self, handle):
        path, directory = self.handles[handle]
        attributes = stat.FILE_ATTRIBUTE_DIRECTORY if directory else 0
        if path == self.reparse:
            attributes |= stat.FILE_ATTRIBUTE_REPARSE_POINT
        return FileFacts(path, 1, attributes, 1, 0)

    def outside_cloud_root(self, handle):
        return True

    def security(self, handle):
        return self.acls.get(self.handles[handle][0], _acl())

    def close(self, handle):
        del self.handles[handle]

    @contextmanager
    def create_maintenance_item(self, parent, name, operator_sid, *, directory):
        assert operator_sid == _OPERATOR
        path = self.handles[parent][0] / name
        if directory:
            path.mkdir()
        else:
            path.touch(exist_ok=False)
        self.created.append(path)
        handle = self._open(path, directory)
        try:
            yield handle
        finally:
            self.close(handle)


@windows
@pytest.mark.parametrize('problem', ['untrusted_ace', 'inherited_untrusted_ace', 'wrong_owner', 'reparse', 'weak_parent'])
def test_guard_rejects_unsafe_catalog_permissions_or_path(authority, problem):
    _, database = authority
    api = _Ntfs()
    expected = 'STORAGE_ACCESS_UNVERIFIED'
    if problem == 'untrusted_ace':
        api.acls[database] = _acl(other=_OTHER)
    elif problem == 'inherited_untrusted_ace':
        api.acls[database] = _acl(other=_OTHER, flags=0x10, control=0x804)
    elif problem == 'wrong_owner':
        api.acls[database] = _acl(owner=_SYSTEM)
        expected = 'DESTINATION_OWNERSHIP_UNVERIFIED'
    elif problem == 'reparse':
        api.reparse = database.parent
        expected = 'UNSAFE_PATH'
    else:
        api.acls[database.parent] = _acl(other=_OTHER, flags=3)
    with pytest.raises(maintenance.MaintenanceError, match=f'^{expected}$'), ExitStack() as stack:
        maintenance._guard_location(stack, api, database, _OPERATOR, purpose='destination')
    assert not api.handles


@windows
def test_guarded_backup_uses_exclusive_private_staging_and_export(authority, tmp_path, monkeypatch, capsys):
    _, database = authority
    api = _Ntfs()
    api.acls[database] = _acl(flags=0x10, control=0x804)
    monkeypatch.setattr(maintenance, 'WindowsKeyStoreNative', lambda: api)
    monkeypatch.setattr(maintenance, '_local_operator', lambda found: _OPERATOR)
    output = tmp_path / 'backup'
    backup = ['backup', '--database', str(database), '--output-directory', str(output)]
    assert maintenance.main(backup) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'BACKUP_CREATED'
    artifact = output / maintenance._BACKUP
    original = artifact.read_bytes()
    assert api.created == [output, output / '.incomplete']
    assert b'private synthetic evidence' not in original
    assert maintenance.main(backup) == 2
    assert json.loads(capsys.readouterr().out)['blockers'] == ['OUTPUT_EXISTS']
    assert artifact.read_bytes() == original
    checkpoint = tmp_path / 'export'
    assert maintenance.main(['export-checkpoint', '--backup', str(artifact), '--output-directory', str(checkpoint)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'CHECKPOINT_EXPORTED_UNTRUSTED'
    assert (checkpoint / maintenance._CHECKPOINT).is_file() and not api.handles


def test_worker_state_missing_never_proves_quiescence():
    missing = inspect_document_worker(Starlette())
    assert missing.worker_id is None and missing.pid is None
    assert missing.blocker == 'WORKER_STATE_UNAVAILABLE' and missing.sqlite_activity == 'unknown'


def test_lifespan_observations_cannot_authorize_restore(create_app):
    app = create_app()
    routes = tuple(app.routes)

    async def run():
        assert inspect_document_worker(app).blocker == 'WORKER_STATE_UNAVAILABLE'
        async with app.router.lifespan_context(app):
            active = inspect_document_worker(app)
            assert active.pid == os.getpid() and active.phase == 'active'
            assert active.blocker == 'WORKER_ACTIVE' and active.sqlite_activity == 'unverified'
            assert active.observed_connections == 0
            capabilities = capability_report(
                environment='production', ready=True, planning=True, persistence=True, probe_allowed=False,
                configured_models=dict.fromkeys(('default_chat', 'vision', 'image_chat')), observation=None,
            )
            assert capabilities['document_intelligence']['direct_chat'] == 'blocked'
            assert capabilities['document_intelligence']['artifact_publication'] == 'blocked'
            assert capabilities['capabilities']['document_rag']['state'] == 'blocked'
            assert capabilities['deployment_verified'] is False
            assert tuple(app.routes) == routes
        stopped = inspect_document_worker(app)
        assert stopped.worker_id == active.worker_id and stopped.phase == 'stopped'
        assert stopped.blocker == 'WORKER_QUIESCENCE_UNVERIFIED'
        async with app.router.lifespan_context(app):
            assert inspect_document_worker(app).worker_id != active.worker_id

    run_offline(run())


def test_observes_catalog_work_and_pending_reasoning_without_persisting_results(create_app, tools_allowed, monkeypatch):
    app = create_app()

    async def run():
        async with app.router.lifespan_context(app):
            documents = get_deployment_documents(app)
            owner, reviewer, decisions = await prepare(app)
            approve = documents._approvals.approve
            check = documents._approvals._restore.check
            open_during_approval = []

            def inspect_connection():
                current = inspect_document_worker(app)
                if current.observed_connections:
                    assert current.sqlite_activity == 'tracked_open'
                    open_during_approval.append(current.observed_connections)
                return check()

            def inspect_approval(*args, **kwargs):
                current = inspect_document_worker(app)
                assert current.active_catalog_operations == 1 and current.sqlite_activity == 'possible'
                return approve(*args, **kwargs)

            monkeypatch.setattr(documents._approvals, 'approve', inspect_approval)
            monkeypatch.setattr(documents._approvals._restore, 'check', inspect_connection)
            await establish(documents, owner, reviewer, decisions)
            idle = inspect_document_worker(app)
            assert idle.active_catalog_operations == idle.observed_connections == 0
            assert open_during_approval and all(count == 1 for count in open_during_approval)
            execute = app.state.engine.run_document_task
            observed = []

            async def interrupt(lease, *args, **kwargs):
                observed.append(inspect_document_worker(app))
                async with aclosing(execute(lease, *args, **kwargs)) as stream:
                    async for event in stream:
                        yield event
                        if event['type'] == 'task_completed':
                            observed.append(inspect_document_worker(app))
                            quarantine_source_authority(app)

            monkeypatch.setattr(app.state.engine, 'run_document_task', interrupt)
            history_path: Path = app.state.settings.conversation_memory_path
            before = history_path.read_bytes()
            terminal, = await documents.run(
                owner.request, CONVERSATION, PASSAGE, sources=(('report', owner.files[0][1]),),
            )
            denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
            assert [state.active_reasoning for state in observed] == [1, 1]
            assert [state.pending_results for state in observed] == [0, 1]
            assert [state.sqlite_activity for state in observed] == ['possible', 'possible']
            after = inspect_document_worker(app)
            assert after.pending_results == after.active_reasoning == 0
            assert history_path.read_bytes() == before

    run_offline(run())
