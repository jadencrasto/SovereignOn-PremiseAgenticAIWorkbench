"""Disposable installer and advisory SQLite identity regressions."""

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from backend.multimodal import document_source_maintenance as maintenance
from backend.multimodal import document_source_recovery as recovery
from backend.runtime import windows_backend_readiness as runtime
from backend.security.provenance_windows_native import WindowsKeyStoreNative
from test_r863_source_maintenance_offline import authority  # noqa: F401
from test_r867_deployment_supervisor_offline import prepare  # noqa: F401
from test_r875_offline_runtime_provisioning import _pin, approved_wheels  # noqa: F401


@pytest.mark.parametrize('failure', [None, 'resolver', 'installer', 'interrupt', 'version', 'missing', 'duplicate'])
def test_disposable_install_publication_and_cleanup(approved_wheels, monkeypatch, failure):
    directory, lock, _ = approved_wheels
    destination = directory.parent / 'disposable'
    pin = _pin(directory, lock)
    monkeypatch.setattr(runtime, '_interpreter_issues', lambda *_: [])
    monkeypatch.setenv('PIP_EXTRA_INDEX_URL', 'https://example.invalid/')
    invoked = []

    def run(command, *, cwd, env, check, timeout, stdin, stdout, stderr):
        invoked.append(command)
        assert env['PIP_CONFIG_FILE'] == runtime.os.devnull and 'PIP_EXTRA_INDEX_URL' not in env
        assert command[1:5] == ['-I', '-S', '-B', '-c']
        assert 'sys.addaudithook' in command[5]
        assert {'--no-index', '--only-binary=:all:', '--require-hashes', '--isolated'} <= set(command)
        assert Path(command[command.index('--find-links') + 1]).parent == cwd
        assert check and timeout == 120 and stdin == stdout == stderr == subprocess.DEVNULL
        assert not destination.exists()
        if '--dry-run' in command:
            if failure == 'resolver':
                raise subprocess.CalledProcessError(1, command)
            return
        assert '--no-deps' in command
        target = Path(command[-1])
        target.mkdir()
        if failure in ('installer', 'interrupt'):
            (target / 'partial.py').touch()
            if failure == 'interrupt':
                raise KeyboardInterrupt
            raise subprocess.CalledProcessError(1, command)
        for index, (name, _, _) in enumerate(runtime._STARTUP):
            if index == 0 and failure == 'missing':
                continue
            version = '201.0' if index == 0 and failure == 'version' else '200.0'
            info = target / f'{name.replace("-", "_")}-{version}.dist-info'
            info.mkdir()
            (info / 'METADATA').write_text(f'Name: {name}\nVersion: {version}\n', encoding='ascii')
        if failure == 'duplicate':
            info = target / 'copy-200.0.dist-info'
            info.mkdir()
            (info / 'METADATA').write_text('Name: fastapi\nVersion: 200.0\n', encoding='ascii')

    monkeypatch.setattr(runtime.subprocess, 'run', run)
    result = runtime.verify_wheelset(directory, lock, approved_lock_sha256=pin, install_destination=destination)
    assert list(destination.parent.glob('.offline-install-*')) == []
    assert len(invoked) == (1 if failure == 'resolver' else 2)
    if failure is None:
        assert result['status'] == 'DISPOSABLE_PACKAGES_INSTALLED'
        assert result['installed_distributions']['fastapi'] == '200.0'
        assert result['application_import'] == result['listener_acceptance'] == 'NOT_ATTEMPTED'
        assert (destination / 'requirements-offline.txt').is_file()
        assert (destination / 'site-packages').is_dir()
    else:
        reason = {'resolver': 'OFFLINE_INSTALLER_FAILED', 'installer': 'OFFLINE_INSTALLER_FAILED',
                  'interrupt': 'OPERATION_INTERRUPTED'}.get(failure, 'INSTALLED_DISTRIBUTION_MISMATCH')
        assert result['status'] == 'BLOCKED' and result['blockers'] == [reason]
        assert not destination.exists()


@pytest.mark.parametrize(('case', 'reason'), [
    ('missing_pin', 'REVIEWED_LOCK_DIGEST_REQUIRED'),
    ('inside_wheels', 'UNSAFE_INSTALL_PREPARATION_PATH'),
    ('existing', 'DISPOSABLE_INSTALL_DESTINATION_EXISTS'),
    ('nonisolated', 'DIRECT_ISOLATED_INSTALL_INTERPRETER_REQUIRED'),
])
def test_installer_refuses_before_starting_any_process(approved_wheels, monkeypatch, case, reason):
    directory, lock, _ = approved_wheels
    pin = _pin(directory, lock)
    destination = directory.parent / 'disposable'
    monkeypatch.setattr(runtime, '_interpreter_issues', lambda *_: ['missing flags'] if case == 'nonisolated' else [])
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *args, **kwargs: pytest.fail('installer started'))
    if case == 'missing_pin':
        pin = ''
    elif case == 'inside_wheels':
        destination = directory / 'packages'
    elif case == 'existing':
        destination.mkdir()
        (destination / 'preserve.txt').touch()
    result = runtime.verify_wheelset(directory, lock, approved_lock_sha256=pin, install_destination=destination)
    assert result['status'] == 'BLOCKED' and result['blockers'] == [reason]
    assert destination.exists() is (case == 'existing')
    assert list(lock.parent.glob('.offline-install-*')) == []


@pytest.mark.parametrize(('handle', 'created', 'wait', 'expected'), [
    (9, 345, 0x102, 'LIVE_IDENTITY_MATCH'),
    (9, 345, 0, 'MATCHED_PROCESS_EXITED'),
    (9, 346, 0x102, 'STALE_IDENTITY'),
    (9, 345, 0xFFFFFFFF, 'IDENTITY_UNOBSERVABLE'),
    (0, 345, 0, 'IDENTITY_UNOBSERVABLE'),
])
def test_native_identity_check_uses_read_only_access_and_closes_handle(handle, created, wait, expected):
    closed = []

    def open_process(rights, inherit, pid):
        assert (rights, inherit, pid) == (0x101000, False, 41)
        return handle

    functions = {'OpenProcess': open_process, 'WaitForSingleObject': lambda _, milliseconds: wait if milliseconds == 0 else -1}
    api = SimpleNamespace(_function=lambda _, name, *args: functions[name],
                          _process_creation_time=lambda _: created, close=closed.append)
    assert WindowsKeyStoreNative.process_identity_state(api, 41, 345) == expected
    assert closed == ([handle] if handle else [])


@pytest.mark.parametrize(('values', 'reason'), [
    (['1:100', '1:101'], 'AMBIGUOUS_EXTERNAL_CLIENT_IDENTITY'),
    (['4294967296:1'], 'INVALID_EXTERNAL_CLIENT_IDENTITY'),
    (['1:9223372036854775808'], 'INVALID_EXTERNAL_CLIENT_IDENTITY'),
    ([f'{pid}:1' for pid in range(1, 18)], 'EXTERNAL_CLIENT_DECLARATION_LIMIT'),
])
def test_external_identity_declarations_are_bounded(values, reason):
    with pytest.raises(maintenance.MaintenanceError, match=reason):
        maintenance._external_client_identities(values)


@pytest.mark.parametrize('command', ['status', 'preflight'])
def test_identity_match_still_cannot_claim_sqlite_client_inventory(authority, tmp_path, monkeypatch, capsys, command):
    app, database = authority
    bundle = tmp_path / 'bundle'
    maintenance._backup(database, bundle)
    evidence = tmp_path / 'checkpoint.txt'
    evidence.write_text(recovery.capture_source_checkpoint(app), encoding='ascii')
    pending = prepare(app, database)
    observations = {7: 'LIVE_IDENTITY_MATCH', 8: 'STALE_IDENTITY', 9: 'IDENTITY_UNOBSERVABLE'}
    monkeypatch.setattr(maintenance, 'WindowsKeyStoreNative',
                        lambda: SimpleNamespace(process_identity_state=lambda pid, _: observations[pid]))
    monkeypatch.setattr(maintenance, '_local_operator', lambda _: 'fixture-operator')
    monkeypatch.setattr(maintenance, '_guard_location', lambda *args, **kwargs: None)
    before = {item: item.read_bytes() for item in tmp_path.rglob('*') if item.is_file()}
    args = [command, '--database', str(database), '--external-client', '9:900',
            '--external-client', '7:700', '--external-client', '8:800']
    if command == 'preflight':
        args.extend(['--backup', str(bundle / maintenance._BACKUP), '--trusted-checkpoint', str(evidence),
                     '--expected-generation', pending.generation])
    assert maintenance.main(args) == (2 if command == 'preflight' else 0)
    result = json.loads(capsys.readouterr().out)
    report = result['sqlite_clients']
    if command == 'preflight':
        assert result['status'] == 'BLOCKED' and 'SQLITE_CLIENT_INVENTORY_UNVERIFIED' in result['blockers']
    else:
        assert result['status'] == 'COORDINATION_READ' and result['pending'] is True
    assert report['external_clients'] == 'UNKNOWN' and report['handle_coverage'] == 'NOT_ENUMERATED'
    assert report['catalog_sidecars_present'] == []
    assert [(entry['pid'], entry['process_identity'], entry['database_handles'])
            for entry in report['operator_declared_external_clients']] == [
                (pid, status, 'UNOBSERVABLE') for pid, status in observations.items()
            ]
    assert recovery._read_shared_state(recovery._coordination_path(database)) == pending
    assert {item: item.read_bytes() for item in tmp_path.rglob('*') if item.is_file()} == before
