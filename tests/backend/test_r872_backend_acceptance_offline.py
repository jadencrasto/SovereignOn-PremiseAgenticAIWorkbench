"""Bounded readiness and recovery diagnostics without host process enumeration."""

import builtins
from contextlib import closing, contextmanager
import ctypes
import json
import sqlite3
from types import SimpleNamespace

import pytest

from backend.multimodal import document_source_maintenance as maintenance
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal import document_supervisor_worker as worker
from backend.multimodal import document_windows_supervisor as windows
from backend.runtime import windows_backend_readiness as readiness
from test_a22_async_offline import run_offline
from test_r863_source_maintenance_offline import authority  # noqa: F401
from test_r867_deployment_supervisor_offline import admit, controlled, platform, prepare, state  # noqa: F401


def test_interpreter_requires_direct_isolated_windows_python_313_x64(monkeypatch):
    runtime = SimpleNamespace(version_info=(3, 13, 7), flags=SimpleNamespace(isolated=1, dont_write_bytecode=1),
                              executable='python', _base_executable='python', path=[])
    monkeypatch.setattr(readiness.Path, 'samefile', lambda self, other: True)
    assert readiness._interpreter_issues(runtime, 64, 'win32') == []
    assert 'WINDOWS_PYTHON_313_64_REQUIRED' in readiness._interpreter_issues(runtime, 32, 'win32')
    runtime.version_info = (3, 11, 1)
    runtime.flags.isolated = 0
    runtime.path = ['relative']
    monkeypatch.setattr(readiness.Path, 'samefile', lambda self, other: False)
    assert set(readiness._interpreter_issues(runtime, 64, 'win32')) == {
        'WINDOWS_PYTHON_313_64_REQUIRED', 'ISOLATED_NO_BYTECODE_REQUIRED', 'DIRECT_INTERPRETER_REQUIRED',
        'LOCAL_IMPORT_PATH_REQUIRED',
    }


def test_dependency_diagnostics_use_metadata_and_never_import_application(monkeypatch):
    versions = {dist: '200.0' for dist, _, _ in readiness._STARTUP}
    versions['fastapi'] = '0.114.9'
    versions['uvicorn'] = '0.30.0rc1'
    del versions['pydantic-settings']

    def version(dist):
        if dist not in versions:
            raise readiness.metadata.PackageNotFoundError(dist)
        return versions[dist]

    monkeypatch.setattr(readiness.metadata, 'version', version)
    monkeypatch.setattr(readiness.PathFinder, 'find_spec', lambda module: None if module == 'httpx' else object())
    monkeypatch.setattr(readiness.importlib, 'import_module', lambda _: pytest.fail('Dependency was imported'))
    missing, incompatible, unverified = readiness._dependency_issues()
    assert missing == ['pydantic-settings', 'httpx']
    assert incompatible == [{'package': 'fastapi', 'installed': '0.114.9', 'minimum': '0.115'}]
    assert unverified == ['uvicorn']


def test_native_probe_limits_dll_lookup_to_system32_and_reports_failed_extension(monkeypatch):
    def probe_dll(name, *, winmode):
        assert name in readiness._DLLS and winmode == 0x800
        if name == 'advapi32.dll':
            raise OSError('simulated missing DLL')

    def probe_extension(name):
        assert name in readiness._EXTENSIONS
        if name == '_sqlite3':
            raise ImportError('simulated native dependency failure')

    monkeypatch.setattr(ctypes, 'WinDLL', probe_dll, raising=False)
    monkeypatch.setattr(readiness.importlib, 'import_module', probe_extension)
    assert readiness._native_issues() == (['_sqlite3'], ['advapi32.dll'])


def test_standalone_readiness_exposes_no_secret_and_cannot_claim_token_or_launch(monkeypatch):
    monkeypatch.setattr(readiness, '_interpreter_issues', lambda *_: [])
    monkeypatch.setattr(readiness, '_dependency_issues', lambda: ([], [], []))
    monkeypatch.setattr(readiness, '_native_issues', lambda: ([], []))
    monkeypatch.setattr(readiness, 'os', SimpleNamespace(environ={
        'http_proxy': 'fixture-credential', 'AUTH_ENABLED': 'false',
        'OLLAMA_BASE_URL': 'invalid-url', 'UNRELATED_SECRET': 'secret',
    }))
    result = readiness.inspect_runtime()
    assert result['unsafe_environment_overrides'] == ['AUTH_ENABLED', 'HTTP_PROXY', 'OLLAMA_BASE_URL']
    assert result['status'] == 'BLOCKED' and result['filtered_token_provider'] == 'UNAVAILABLE_IN_STANDALONE_PROBE'
    assert result['application_import'] == result['listener_acceptance'] == 'NOT_ATTEMPTED'
    assert 'fixture-credential' not in json.dumps(result) and 'secret' not in json.dumps(result)


@pytest.mark.parametrize('case', ['missing', 'invalid', 'unauthorized', 'valid'])
def test_supplied_token_provider_is_scoped_and_cannot_bypass_token_validation(authority, monkeypatch, case):
    from backend import config

    _, database = authority
    settings = SimpleNamespace(tasks_db_path=database, backend_host='127.0.0.1', app_env='dev', auth_enabled=True,
                               auth_dev_override=False, local_chat_only=True, ollama_base_url='http://127.0.0.1:11434')
    monkeypatch.setattr(config, 'settings', settings)
    monkeypatch.setattr(windows, 'os', SimpleNamespace(name='nt'))
    calls = []

    @contextmanager
    def provider():
        calls.append('provider open')
        try:
            yield 20
        finally:
            calls.append('provider close')

    def operator(_):
        calls.append('operator')
        if case == 'unauthorized':
            raise RuntimeError('Operator refused')
        return 'operator-sid'

    def duplicate(supplied):
        assert supplied == 20
        calls.append('duplicate')
        return 21

    def validate(handle):
        assert handle == 21
        calls.append('qualify')
        return ('operator-sid' if case == 'valid' else 'wrong-sid'), 3

    api = SimpleNamespace(open_process_token=lambda: 10, token_session_id=lambda _: 3,
                          supervised_worker_token_identity=validate, close=lambda h: calls.append(('close', h)))
    kernel = SimpleNamespace(duplicate_worker_token=duplicate, close=lambda h: calls.append(('close', h)))
    boundary = SimpleNamespace(close=lambda: calls.append('boundary close'))
    monkeypatch.setattr(windows, '_authorize_operator', operator)
    monkeypatch.setattr(windows, 'WindowsKeyStoreNative', lambda: api)
    monkeypatch.setattr(windows, 'WindowsRecoveryWorkerAPI', lambda: kernel)
    monkeypatch.setattr(windows, '_WindowsProcessBoundary', lambda *_, **__: boundary)
    monkeypatch.setattr(windows, 'DeploymentSupervisor', lambda *args: 'supervisor')
    kwargs = {} if case == 'missing' else {'token_provider': provider}
    if case == 'valid':
        with windows.open_windows_deployment_supervisor(database, **kwargs) as owner:
            assert owner == 'supervisor' and calls[-1] == 'qualify'
        assert calls[-2:] == ['boundary close', ('close', 21)]
    else:
        with pytest.raises(RuntimeError):
            with windows.open_windows_deployment_supervisor(database, **kwargs):
                pytest.fail('Unverified provider entered supervisor scope')
    if case in ('invalid', 'valid'):
        assert calls[:4] == ['operator', 'provider open', 'duplicate', 'provider close']
        assert calls[-1] == ('close', 21)
    else:
        assert calls == (['operator'] if case == 'unauthorized' else [])


def test_failed_app_import_cannot_reach_server_and_closes_private_handles(monkeypatch):
    from backend.runtime import windows_recovery_worker

    closed = []
    api = SimpleNamespace(wait_process=lambda *_: False, read_ticket=lambda _: b'a' * 32, close=closed.append)
    monkeypatch.setattr(windows_recovery_worker, 'WindowsRecoveryWorkerAPI', lambda: api)
    monkeypatch.setattr(windows, '_require_supervised_profile', lambda _: None)
    monkeypatch.setattr(worker, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(worker, '_server', lambda *args: pytest.fail('Server constructed after failed import'))
    original_import = builtins.__import__

    def import_app(name, *args, **kwargs):
        if name == 'backend.main':
            raise ImportError('fixture backend dependency unavailable')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', import_app)
    with pytest.raises(ImportError):
        worker._entry(['15', '16'])
    assert closed == [15, 16]


def test_matched_supervised_roster_does_not_account_for_open_external_sqlite(controlled, platform):
    _, database, boundary, owner = controlled

    async def run():
        admit(database, boundary, owner, platform)
        with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)) as outside:
            outside.execute('BEGIN')
            outside.execute('SELECT COUNT(*) FROM sqlite_schema').fetchone()
            roster = recovery.inspect_recovery_maintenance(database, state(database))
            observed = maintenance._sqlite_clients(database, roster)
        assert observed['supervisor_matched_registrations'] == 1
        assert observed['other_registered_lifespans'] == observed['supervisor_records_without_registration'] == 0
        assert observed['external_clients'] == 'UNKNOWN' and observed['handle_coverage'] == 'NOT_ENUMERATED'
        assert observed['application_tracked_connections'] == 'NOT_OBSERVED_BY_OPERATOR'

    run_offline(run())


def test_pending_launch_record_is_not_reported_as_an_admitted_worker(controlled):
    _, database, _, owner = controlled
    owner.start_worker()
    observed = maintenance._sqlite_clients(database, recovery.inspect_recovery_maintenance(database, state(database)))
    assert observed['supervisor_matched_registrations'] == 0
    assert observed['supervisor_records_without_registration'] == 1
    assert observed['other_authorized_components'] == 'NOT_INVENTORIED'


def test_preflight_cli_diagnostics_remain_read_only_with_ambiguous_sidecar(authority, tmp_path, monkeypatch, capsys):
    app, database = authority
    bundle = tmp_path / 'bundle'
    maintenance._backup(database, bundle)
    evidence = tmp_path / 'checkpoint.txt'
    evidence.write_text(recovery.capture_source_checkpoint(app), encoding='ascii')
    pending = prepare(app, database)
    sidecar = database.with_name(database.name + '-journal')
    sidecar.write_bytes(b'fixture stale or active journal evidence')
    before = {item: item.read_bytes() for item in tmp_path.rglob('*') if item.is_file()}
    monkeypatch.setattr(maintenance, '_local_operator', lambda _: 'fixture-operator')
    monkeypatch.setattr(maintenance, '_guard_location', lambda *args, **kwargs: None)
    assert maintenance.main(['preflight', '--database', str(database), '--backup', str(bundle / maintenance._BACKUP),
                             '--trusted-checkpoint', str(evidence), '--expected-generation', pending.generation]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'BLOCKED' and 'DESTINATION_SQLITE_ACTIVITY_POSSIBLE' in result['blockers']
    assert result['sqlite_clients']['catalog_sidecars_present'] == ['journal']
    assert result['sqlite_clients']['external_clients'] == 'UNKNOWN'
    assert {item: item.read_bytes() for item in tmp_path.rglob('*') if item.is_file()} == before
