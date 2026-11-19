"""Disposable wheel preparation tests. No installer or backend is started."""

import hashlib
import json
import os
from zipfile import ZipFile

import pytest

from backend.runtime import windows_backend_readiness as runtime


@pytest.fixture
def approved_wheels(tmp_path):
    directory = tmp_path / 'wheels'
    directory.mkdir()
    lock = tmp_path / 'approved.json'
    output = tmp_path / 'requirements-offline.txt'
    for name, _, _ in runtime._STARTUP:
        _wheel(directory, name, '200.0')
    return directory, lock, output


def _wheel(directory, name, version, *, dependencies=(), tag='py3-none-any', metadata_name=None):
    stem = f"{name.lower().replace('-', '_')}-{version}"
    path = directory / f'{stem}-{tag}.whl'
    headers = ['Metadata-Version: 2.1', f'Name: {metadata_name or name}', f'Version: {version}']
    headers.extend(f'Requires-Dist: {dependency}' for dependency in dependencies)
    with ZipFile(path, 'w') as archive:
        archive.writestr(f'{stem}.dist-info/METADATA', '\n'.join(headers) + '\n\n')
    return path


def _pin(directory, lock):
    wheels = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()}
    raw = json.dumps({'schema': 1, 'target': 'cp313-win_amd64', 'wheels': wheels}, sort_keys=True).encode('ascii')
    lock.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _prepare(directory, lock, output, pin):
    return runtime.verify_wheelset(directory, lock, approved_lock_sha256=pin, install_requirements=output)


def test_prepares_only_pinned_no_index_binary_inputs(approved_wheels):
    directory, lock, output = approved_wheels
    _wheel(directory, 'fastapi', '200.0', dependencies=('Local-Test-Package>=1',))
    dependency = _wheel(directory, 'local-test-package', '1.0', tag='cp313-cp313-win_amd64')
    pin = _pin(directory, lock)
    result = _prepare(directory, lock, output, pin)
    assert result['status'] == 'INSTALL_REQUIREMENTS_WRITTEN', result
    assert result['dependency_resolution'] == 'REQUIRED'
    lines = output.read_text(encoding='ascii').splitlines()
    assert lines[:4] == [f'# Reviewed wheel lock SHA-256 {pin}', '--no-index',
                         '--only-binary :all:', '--require-hashes']
    assert len(lines[4:]) == len(runtime._STARTUP) + 1
    assert all(' --hash=sha256:' in line and '==' in line for line in lines[4:])
    digest = hashlib.sha256(dependency.read_bytes()).hexdigest()
    assert f'local-test-package==1.0 --hash=sha256:{digest}' in lines
    assert list(output.parent.glob('.offline-requirements-*')) == []


@pytest.mark.parametrize(('failure', 'reason'), [
    ('missing_dependency', 'WHEELSET_INCOMPLETE_DEPENDENCY_CLOSURE'),
    ('index_bypass', 'DIRECT_WHEEL_DEPENDENCY_FORBIDDEN'),
    ('identity_mismatch', 'INVALID_WHEEL_METADATA'),
    ('conflicting_versions', 'DUPLICATE_LOCKED_PROJECT'),
    ('wrong_tag', 'WHEELSET_INCOMPLETE_OR_UNREVIEWED'),
    ('missing_wheel', 'WHEELSET_INCOMPLETE_OR_UNREVIEWED'),
    ('tampered_wheel', 'WHEEL_DIGEST_MISMATCH'),
    ('unapproved_lock', 'WHEEL_LOCK_DIGEST_MISMATCH'),
])
def test_rejected_inputs_never_publish_requirements(approved_wheels, failure, reason):
    directory, lock, output = approved_wheels
    fastapi = directory / 'fastapi-200.0-py3-none-any.whl'
    if failure == 'missing_dependency':
        _wheel(directory, 'fastapi', '200.0', dependencies=('Fixture-Missing>=1',))
    elif failure == 'index_bypass':
        _wheel(directory, 'fastapi', '200.0',
               dependencies=('Fixture @ https://example.invalid/fixture.whl; python_version >= "3.0"',))
    elif failure == 'identity_mismatch':
        _wheel(directory, 'fastapi', '200.0', metadata_name='different-project')
    elif failure == 'conflicting_versions':
        _wheel(directory, 'fastapi', '201.0')
    elif failure == 'wrong_tag':
        fastapi.rename(directory / 'fastapi-200.0-cp313-cp313-win_arm64.whl')
    pin = _pin(directory, lock)
    if failure == 'missing_wheel':
        fastapi.unlink()
    elif failure == 'tampered_wheel':
        fastapi.write_bytes(b'modified after review')
    elif failure == 'unapproved_lock':
        pin = '0' * 64
    result = _prepare(directory, lock, output, pin)
    assert result['status'] == 'BLOCKED'
    assert result['blockers'] == [reason]
    if failure == 'missing_dependency':
        assert result['missing_unconditional_dependencies'] == ['fixture-missing']
    assert not output.exists()


@pytest.mark.parametrize('destination', ['inside_wheelhouse', 'relative', 'existing_output'])
def test_unsafe_install_paths_are_refused(approved_wheels, destination):
    directory, lock, output = approved_wheels
    pin = _pin(directory, lock)
    if destination == 'inside_wheelhouse':
        output = directory / output.name
        reason = 'UNSAFE_INSTALL_PREPARATION_PATH'
    elif destination == 'relative':
        output = type(output)(output.name)
        reason = 'LOCAL_ARTIFACT_PATH_REQUIRED'
    else:
        output.write_text('operator file', encoding='ascii')
        reason = 'INSTALL_REQUIREMENTS_ALREADY_EXISTS'
    result = _prepare(directory, lock, output, pin)
    assert result['status'] == 'BLOCKED' and result['blockers'] == [reason]
    if destination == 'existing_output':
        assert output.read_text(encoding='ascii') == 'operator file'
    else:
        assert not output.exists()
    assert list(lock.parent.glob('.offline-requirements-*')) == []


def test_cli_requires_a_separate_review_pin_even_when_only_preparing(approved_wheels, capsys):
    directory, lock, output = approved_wheels
    _pin(directory, lock)
    assert runtime.main(['--wheel-directory', str(directory), '--lock', str(lock),
                         '--write-install-requirements', str(output)]) == 2
    assert json.loads(capsys.readouterr().out)['blockers'] == ['REVIEWED_WHEELSET_UNPROVISIONED']
    assert not output.exists()


@pytest.mark.parametrize(('failure', 'reason'), [
    ('foreign_target', 'INVALID_WHEEL_LOCK'),
    ('superseded_pin', 'WHEEL_LOCK_DIGEST_MISMATCH'),
])
def test_install_handoff_refuses_foreign_scope_or_superseded_pin(approved_wheels, monkeypatch, failure, reason):
    directory, lock, output = approved_wheels
    prior_pin = _pin(directory, lock)
    if failure == 'foreign_target':
        record = json.loads(lock.read_bytes())
        record['target'] = 'cp313-win_arm64'
        raw = json.dumps(record, sort_keys=True).encode('ascii')
        lock.write_bytes(raw)
        pin = hashlib.sha256(raw).hexdigest()
    else:
        _wheel(directory, 'fastapi', '200.0', dependencies=('uvicorn',))
        assert _pin(directory, lock) != prior_pin
        pin = prior_pin
    monkeypatch.setattr(runtime, '_install_disposable', lambda *_: pytest.fail('installer started'))
    result = runtime.verify_wheelset(directory, lock, approved_lock_sha256=pin, install_destination=output)
    assert result['status'] == 'BLOCKED' and result['blockers'] == [reason]
    assert not output.exists()


def test_probe_reports_installer_overrides_without_leaking_values():
    environment = {'PIP_FIND_LINKS': 'unreviewed mirror', 'pip_extra_index_url': 'credential-bearing URL',
                   'PIP_PROXY': 'proxy override', 'PIP_CONFIG_FILE': 'site.ini'}
    assert runtime._unsafe_environment(environment) == [
        'PIP_CONFIG_FILE', 'PIP_EXTRA_INDEX_URL', 'PIP_FIND_LINKS', 'PIP_PROXY',
    ]
    assert runtime._unsafe_environment({'PIP_CONFIG_FILE': os.devnull}) == []
