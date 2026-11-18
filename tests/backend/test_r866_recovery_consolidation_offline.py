"""Integration regressions for cancelled windows, validation races and preflight."""

from contextlib import asynccontextmanager, closing
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette

from recovery_custody_fixture import restore_with_checkpoint

from backend.multimodal import document_source_maintenance as maintenance
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal import document_transport as transport
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_source_authority import LocalSourceAuthority
from test_a22_async_offline import run_offline
from test_r848_document_agent_lease_offline import Upload, host, app_engine  # noqa: F401
from test_r863_source_maintenance_offline import authority  # noqa: F401


def _state(database):
    return recovery._read_shared_state(recovery._coordination_path(database))


def test_delayed_cancel_cannot_end_a_new_maintenance_window(authority):
    app, database = authority
    recovery.quarantine_source_authority(app)
    first = _state(database)
    recovery.set_recovery_maintenance(database, first.generation, active=True)
    recovery.set_recovery_maintenance(database, first.generation, active=False)
    second = _state(database)
    assert second.pending
    recovery.set_recovery_maintenance(database, second.generation, active=True)

    with pytest.raises(ReasoningError):
        recovery.set_recovery_maintenance(database, first.generation, active=False)

    assert recovery.inspect_recovery_maintenance(database, second).active


def test_validator_cannot_commit_across_prepare_then_cancel(authority, monkeypatch):
    app, database = authority
    checkpoint = recovery.capture_source_checkpoint(app)
    recovery.quarantine_source_authority(app)
    first = _state(database)
    compute = recovery._scan_checkpoint

    def interrupt(connection, deadline):
        digest = compute(connection, deadline)
        recovery.set_recovery_maintenance(database, first.generation, active=True)
        recovery.set_recovery_maintenance(database, first.generation, active=False)
        return digest

    with monkeypatch.context() as scope:
        scope.setattr(recovery, '_scan_checkpoint', interrupt)
        with pytest.raises(ReasoningError):
            restore_with_checkpoint(app, trusted_checkpoint=checkpoint)

    assert _state(database).pending
    restore_with_checkpoint(app, trusted_checkpoint=checkpoint)
    assert not _state(database).pending


@pytest.mark.parametrize('register_during_digest', [False, True])
def test_checkpoint_validation_requires_registered_worker_shutdown(authority, monkeypatch, register_during_digest):
    maintenance_app, database = authority
    checkpoint = recovery.capture_source_checkpoint(maintenance_app)
    recovery.quarantine_source_authority(maintenance_app)
    pending = _state(database)
    worker = Starlette()
    worker.state.settings = SimpleNamespace(tasks_db_path=database)

    async def run():
        compute = recovery._scan_checkpoint

        def join(connection, deadline):
            digest = compute(connection, deadline)
            recovery.register_recovery_worker(worker)
            return digest

        with monkeypatch.context() as scope:
            if register_during_digest:
                scope.setattr(recovery, '_scan_checkpoint', join)
            else:
                recovery.register_recovery_worker(worker)
            with pytest.raises(ReasoningError):
                restore_with_checkpoint(maintenance_app, trusted_checkpoint=checkpoint)
        assert _state(database) == pending
        worker.state.startup_complete = False
        recovery.acknowledge_recovery_worker_shutdown(worker, worker.state.source_recovery_registration)
        restore_with_checkpoint(maintenance_app, trusted_checkpoint=checkpoint)
        assert not _state(database).pending

    run_offline(run())


@pytest.mark.parametrize('damage', ['not_sqlite', 'missing_catalog', 'clean_wal'])
def test_preflight_checks_destination_without_generating_sidecars(authority, tmp_path, damage):
    app, database = authority
    bundle = tmp_path / 'bundle'
    maintenance._backup(database, bundle)
    checkpoint = tmp_path / 'checkpoint.txt'
    checkpoint.write_text(recovery.capture_source_checkpoint(app), encoding='ascii')
    recovery.quarantine_source_authority(app)
    pending = _state(database)
    if damage == 'not_sqlite':
        database.write_bytes(b'invalid destination')
    else:
        with closing(sqlite3.connect(database)) as connection:
            if damage == 'missing_catalog':
                connection.execute('DROP TABLE approved_document_sources')
            else:
                assert connection.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
    before = {path.name: path.read_bytes() for path in database.parent.iterdir()}

    result = maintenance._preflight(database, bundle / maintenance._BACKUP, checkpoint, pending.generation)

    assert result.status == 'BLOCKED'
    if damage == 'clean_wal':
        assert 'DESTINATION_INTEGRITY_VALID' in result.checks
        assert 'SQLITE_CLIENT_INVENTORY_UNVERIFIED' in result.blockers
    elif damage == 'missing_catalog':
        assert result.blockers == ('DESTINATION_CATALOGS_UNVERIFIED',)
    else:
        assert result.blockers == ('DESTINATION_INTEGRITY_UNVERIFIED',)
    assert before == {path.name: path.read_bytes() for path in database.parent.iterdir()}


@pytest.mark.parametrize('shared', [True, False])
def test_transport_polls_shared_cancellation_once_and_preserves_distinct_cancellation(host, app_engine, monkeypatch, shared):
    upload = Upload(host)
    calls = []
    authority_cancelled = False

    def check_authority():
        calls.append('authority')
        return authority_cancelled

    def check_transport():
        calls.append('transport')
        return False

    @asynccontextmanager
    async def inspect_transport(request, conversation_id, source_authority, cancelled, deadline, issuer):
        nonlocal authority_cancelled
        authority_cancelled = not shared
        calls.clear()
        assert cancelled() is (not shared)
        assert calls == (['authority'] if shared else ['transport', 'authority'])
        yield SimpleNamespace(_evidence_released=False)

    async def run():
        await upload.authenticate()
        authority = LocalSourceAuthority(upload.request, 'a-conversation', lambda *_: None, check_authority, None)
        monkeypatch.setattr(transport, '_open_transport', inspect_transport)
        async with transport.open_document_transport(
            upload.request, 'a-conversation', source_authority=authority,
            cancelled=check_authority if shared else check_transport,
        ):
            pass
        assert authority._access is None

    run_offline(run())
