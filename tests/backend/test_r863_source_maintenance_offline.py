"""Disposable SQLite tests for the local source-authorization operator tooling."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette

from backend.multimodal import document_source_maintenance as maintenance
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_source_approvals import DeploymentSourceApprovals
from backend.security.provenance_foundation import document_content_allowed
from backend.security.provenance_windows_native import TokenFacts


@pytest.fixture
def authority(tmp_path):
    deployment = tmp_path / 'deployment'
    deployment.mkdir()
    database = deployment / 'tasks.db'
    database.touch()
    app = Starlette()
    app.state.settings = SimpleNamespace(tasks_db_path=database)
    DeploymentSourceApprovals(app).source_provider()
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript('''CREATE TABLE unrelated (secret TEXT);
            INSERT INTO unrelated VALUES ('private synthetic evidence');
            INSERT INTO document_source_approvals VALUES (
                'ref', 'owner', 'conversation', 'generation', 'reviewer', 'revision',
                'verified_document_workflow', 'report.pdf', 16, 'source-digest', 'public',
                '2026-01-01T00:00:00+00:00', '2027-01-01T00:00:00+00:00', NULL
            );
            INSERT INTO approved_document_sources VALUES (
                'ref', 'owner', 'conversation', 'generation', 'revision',
                'report.pdf', 16, 'source-digest', 'public', 1, 3, NULL
            );''')
    return app, database


def _generation(database):
    return recovery._read_shared_state(recovery._coordination_path(database)).generation


def _bundle(database, directory):
    maintenance._backup(database, directory)
    return directory / maintenance._BACKUP


def _digest_file(directory, digest):
    path = directory / 'trusted.sha256'
    path.write_text(digest + '\n', encoding='ascii')
    return path


def test_wal_snapshot_contains_committed_withdrawal_and_only_catalogs(authority, tmp_path):
    app, database = authority
    with closing(sqlite3.connect(database)) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("UPDATE document_source_approvals SET withdrawn_at = 'withdrawn'")
        writer.execute('UPDATE approved_document_sources SET withdrawn_us = 2')
        writer.commit()
        assert database.with_name(database.name + '-wal').stat().st_size > 0
        digest = recovery.capture_source_checkpoint(app)
        copy = _bundle(database, tmp_path / 'backup')
    with closing(sqlite3.connect(copy)) as snapshot:
        assert snapshot.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert snapshot.execute('PRAGMA journal_mode').fetchone() == ('delete',)
        assert snapshot.execute('SELECT withdrawn_at FROM document_source_approvals').fetchone() == ('withdrawn',)
        assert snapshot.execute('SELECT withdrawn_us FROM approved_document_sources').fetchone() == (2,)
        assert snapshot.execute("SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name").fetchall() == [
            ('approved_document_sources',), ('document_source_approvals',), ('source_backup_manifest',),
        ]
        assert snapshot.execute('SELECT checkpoint FROM source_backup_manifest').fetchone() == (digest,)
    assert b'private synthetic evidence' not in copy.read_bytes()
    assert sorted(path.name for path in copy.parent.iterdir()) == [maintenance._BACKUP]
    assert not document_content_allowed()


def test_checkpoint_export_never_proves_custody_or_clears_quarantine(authority, tmp_path):
    app, database = authority
    copy = _bundle(database, tmp_path / 'backup')
    export = tmp_path / 'export'
    binding = recovery.SourceRestoreBinding(app, database)
    maintenance._export_checkpoint(copy, export)
    proof = export / maintenance._CHECKPOINT
    assert proof.read_bytes() == recovery.capture_source_checkpoint(app).encode('ascii') + b'\n'
    recovery.quarantine_source_authority(app)
    pending = _generation(database)
    before = database.read_bytes(), recovery._coordination_path(database).read_bytes()
    result = maintenance._preflight(database, copy, proof, pending)
    assert result == maintenance.Preflight('BLOCKED', (
        'BACKUP_INTEGRITY_AND_SCHEMA_VALID', 'SUPPLIED_CHECKPOINT_MATCHES', 'EXPECTED_QUARANTINE_GENERATION',
        'DESTINATION_INTEGRITY_VALID',
    ), ('CHECKPOINT_CUSTODY_UNVERIFIED', 'WORKER_QUIESCENCE_UNVERIFIED', 'SUPERVISOR_RESTART_UNVERIFIED',
        'SQLITE_CLIENT_INVENTORY_UNVERIFIED', 'MAINTENANCE_BARRIER_REQUIRED'))
    assert before == (database.read_bytes(), recovery._coordination_path(database).read_bytes())
    assert _generation(database) == pending
    with pytest.raises(ReasoningError):
        binding.check()


def test_old_backup_does_not_match_independent_post_withdrawal_checkpoint(authority, tmp_path):
    app, database = authority
    old = _bundle(database, tmp_path / 'old')
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE document_source_approvals SET withdrawn_at = 'withdrawn'")
    independently_retained = _digest_file(tmp_path, recovery.capture_source_checkpoint(app))
    recovery.quarantine_source_authority(app)
    pending = _generation(database)
    result = maintenance._preflight(database, old, independently_retained, pending)
    assert result.blockers == ('TRUSTED_CHECKPOINT_MISMATCH',)
    assert _generation(database) == pending
    assert recovery._read_shared_state(recovery._coordination_path(database)).pending


@pytest.mark.parametrize(('damage', 'expected'), [
    ('missing', 'DATABASE_UNREADABLE'), ('bad_sqlite_header', 'INVALID_STANDALONE_DATABASE'),
    ('schema', 'UNSUPPORTED_SCHEMA'), ('manifest', 'INVALID_BACKUP_METADATA'),
    ('metadata_row', 'BACKUP_CHECKPOINT_MISMATCH'), ('sidecar', 'SQLITE_SIDECAR_PRESENT'),
    ('unfinished', 'COMPLETED_BACKUP_REQUIRED'),
])
def test_preflight_rejects_bad_backup(authority, tmp_path, damage, expected):
    app, database = authority
    original = _bundle(database, tmp_path / 'backup')
    proof = _digest_file(tmp_path, recovery.capture_source_checkpoint(app))
    recovery.quarantine_source_authority(app)
    pending = _generation(database)
    if damage == 'missing':
        original.unlink()
    elif damage == 'bad_sqlite_header':
        original.write_bytes(b'not SQLite'.ljust(100, b'\x00'))
    elif damage == 'sidecar':
        original.with_name(original.name + '-journal').touch()
    elif damage == 'unfinished':
        original = original.rename(original.with_name('.incomplete'))
    else:
        with closing(sqlite3.connect(original)) as connection, connection:
            connection.execute({
                'schema': 'CREATE TABLE unrelated (value TEXT)',
                'manifest': 'UPDATE source_backup_manifest SET version=2',
                'metadata_row': "UPDATE approved_document_sources SET withdrawn_us=2",
            }[damage])
    assert maintenance._preflight(database, original, proof, pending).blockers == (expected,)
    assert _generation(database) == pending


def test_generation_and_possible_active_wal_are_reported(authority, tmp_path):
    app, database = authority
    copy = _bundle(database, tmp_path / 'backup')
    proof = _digest_file(tmp_path, recovery.capture_source_checkpoint(app))
    generation = _generation(database)
    assert maintenance._preflight(database, copy, proof, generation).blockers == ('QUARANTINE_REQUIRED',)
    recovery.quarantine_source_authority(app)
    assert maintenance._preflight(database, copy, proof, generation).blockers == ('RECOVERY_GENERATION_MISMATCH',)
    with closing(sqlite3.connect(database)) as active:
        active.execute('PRAGMA journal_mode=WAL')
        active.execute("INSERT INTO unrelated VALUES ('another synthetic row')")
        active.commit()
        result = maintenance._preflight(database, copy, proof, _generation(database))
        assert result.blockers == ('DESTINATION_SQLITE_ACTIVITY_POSSIBLE',)
    proof.write_bytes(b'a' * 67)
    assert maintenance._preflight(database, copy, proof, _generation(database)).blockers == ('INVALID_CHECKPOINT',)


def test_damaged_sqlite_source_is_rejected_before_output(authority, tmp_path):
    _, database = authority
    database.write_bytes(b'not sqlite')
    output = tmp_path / 'backup'
    with pytest.raises(sqlite3.DatabaseError):
        maintenance._backup(database, output)
    assert not output.exists()


def test_exclusive_output_and_keyboard_interrupt_cleanup(authority, tmp_path, monkeypatch):
    _, database = authority
    output = tmp_path / 'backup'
    output.mkdir()
    sentinel = output / 'existing.txt'
    sentinel.write_text('preserve', encoding='ascii')
    with pytest.raises(maintenance.MaintenanceError, match='^OUTPUT_EXISTS$'):
        maintenance._backup(database, output)
    assert sentinel.read_text(encoding='ascii') == 'preserve'

    def fail(path, deadline):
        assert path.is_file()
        raise KeyboardInterrupt

    monkeypatch.setattr(maintenance, '_inspect_backup', fail)
    interrupted = tmp_path / 'interrupted'
    with pytest.raises(KeyboardInterrupt):
        maintenance._backup(database, interrupted)
    assert not interrupted.exists()


def test_output_cannot_interfere_with_deployment_or_backup_bundle(authority, tmp_path):
    _, database = authority
    wal = database.with_name(database.name + '-wal')
    with pytest.raises(maintenance.MaintenanceError, match='^OUTPUT_IN_DEPLOYMENT_DIRECTORY$'):
        maintenance._backup(database, wal)
    assert not wal.exists()
    backup = _bundle(database, tmp_path / 'backup')
    with pytest.raises(maintenance.MaintenanceError, match='^OUTPUT_IN_BACKUP_BUNDLE$'):
        maintenance._export_checkpoint(backup, backup.parent / 'checkpoint-output')
    with pytest.raises(maintenance.MaintenanceError, match='^RESERVED_OUTPUT_NAME$'):
        maintenance._export_checkpoint(backup, tmp_path / 'unrelated.sqlite-wal')
    assert list(backup.parent.iterdir()) == [backup]


def test_quarantine_during_backup_aborts_publish(authority, tmp_path, monkeypatch):
    app, database = authority
    inspect = maintenance._inspect_backup

    def declare_after_copy(path, deadline):
        checkpoint = inspect(path, deadline)
        recovery.quarantine_source_authority(app)
        return checkpoint

    monkeypatch.setattr(maintenance, '_inspect_backup', declare_after_copy)
    output = tmp_path / 'backup'
    with pytest.raises(maintenance.MaintenanceError, match='^RECOVERY_GENERATION_CHANGED$'):
        maintenance._backup(database, output)
    assert not output.exists()
    assert recovery._read_shared_state(recovery._coordination_path(database)).pending


@pytest.mark.parametrize('path', [Path('relative'), Path('..') / 'relative'])
def test_refuses_relative_or_traversal_output(path):
    with pytest.raises(maintenance.MaintenanceError, match='^UNSAFE_PATH$'):
        maintenance._safe_path(path)


@pytest.mark.parametrize('attributes, links', [(0, 2), (stat.FILE_ATTRIBUTE_REPARSE_POINT, 1)])
def test_refuses_aliased_source(authority, tmp_path, monkeypatch, attributes, links):
    _, database = authority
    lstat = Path.lstat

    def source_stat(path, *args, **kwargs):
        info = lstat(path, *args, **kwargs)
        if path == database:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=attributes, st_nlink=links)
        return info

    monkeypatch.setattr(Path, 'lstat', source_stat)
    output = tmp_path / 'backup'
    with pytest.raises(maintenance.MaintenanceError, match='^UNSAFE_PATH$'):
        maintenance._backup(database, output)
    assert not output.exists()


def test_cli_blocks_unverified_operator_and_storage(authority, tmp_path, monkeypatch, capsys):
    _, database = authority
    command = ['backup', '--database', str(database), '--output-directory', str(tmp_path / 'backup')]

    def deny_operator(api):
        raise maintenance.MaintenanceError('LOCAL_OPERATOR_REQUIRED')

    def deny_storage(stack, api, path, owner, *, purpose):
        raise OSError('private diagnostic path')

    monkeypatch.setattr(maintenance, '_local_operator', deny_operator)
    assert maintenance.main(command) == 2
    assert json.loads(capsys.readouterr().out)['blockers'] == ['LOCAL_OPERATOR_REQUIRED']
    monkeypatch.setattr(maintenance, '_local_operator', lambda api: 'test SID')
    monkeypatch.setattr(maintenance, '_guard_location', deny_storage)
    assert maintenance.main(command) == 2
    assert json.loads(capsys.readouterr().out)['blockers'] == ['STORAGE_OR_COORDINATION_UNAVAILABLE']
    assert not (tmp_path / 'backup').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows token policy')
def test_operator_requires_primary_local_interactive_identity():
    sid = 'S-1-5-21-12-34-56-1001'
    api = SimpleNamespace(
        open_thread_token=lambda: None,
        open_process_token=lambda: 1,
        close=lambda handle: None,
        token_facts=lambda handle: TokenFacts(sid, True, sid, 2),
        elevated_administrator=lambda handle: True,
    )
    assert maintenance._local_operator(api) == sid
    api.token_facts = lambda handle: TokenFacts(sid, True, sid, 10)
    with pytest.raises(maintenance.MaintenanceError, match='^LOCAL_OPERATOR_REQUIRED$'):
        maintenance._local_operator(api)
    api.open_thread_token = lambda: 2
    with pytest.raises(maintenance.MaintenanceError, match='^LOCAL_OPERATOR_REQUIRED$'):
        maintenance._local_operator(api)
