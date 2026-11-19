"""Dependency metadata checks for the existing offline installer-input generator."""

from zipfile import ZipFile

import pytest

from backend.runtime import windows_backend_readiness as runtime
from test_r875_offline_runtime_provisioning import _pin, _prepare, _wheel, approved_wheels  # noqa: F401


def _metadata_wheel(directory, name, *headers, version='200.0', description=''):
    stem = f"{name.replace('-', '_')}-{version}"
    path = directory / f'{stem}-py3-none-any.whl'
    with ZipFile(path, 'w') as archive:
        fields = ['Metadata-Version: 2.3', f'Name: {name}', f'Version: {version}', *headers]
        archive.writestr(f'{stem}.dist-info/METADATA', '\n'.join(fields) + '\n\n' + description)


def test_target_closure_follows_requested_extras_and_ignores_inactive_edges(approved_wheels):
    directory, lock, output = approved_wheels
    _wheel(directory, 'fastapi', '200.0', dependencies=(
        'fixture-client[Local_IO]>=2; sys_platform == "win32" and python_version == "3.13"',
        'fixture-posix; os_name != "nt"',
    ))
    _metadata_wheel(
        directory, 'fixture-client',
        'Requires-Python: >=3.13,<3.14',
        'Provides-Extra: local-io',
        'Provides-Extra: development',
        'Requires-Dist: fixture-native~=1.1; extra == "local-io"',
        'Requires-Dist: fixture-test-only; extra == "development"',
        version='2.0',
    )
    _wheel(directory, 'fixture-native', '1.2', tag='cp313-cp313-win_amd64')
    pin = _pin(directory, lock)
    result = _prepare(directory, lock, output, pin)
    assert result['status'] == 'INSTALL_REQUIREMENTS_WRITTEN', result
    assert result['dependency_closure'] == 'VERIFIED_FOR_TARGET'
    assert result['dependency_target_python'].startswith('3.13.')
    assert result['dependency_resolution'] == 'REQUIRED'
    pins = output.read_text(encoding='ascii').splitlines()[4:]
    assert pins == sorted(pins, key=lambda line: line.partition('==')[0])
    assert any(line.startswith('fixture-native==1.2 --hash=') for line in pins)
    first = output.read_bytes()
    output.unlink()
    assert _prepare(directory, lock, output, pin)['status'] == 'INSTALL_REQUIREMENTS_WRITTEN'
    assert output.read_bytes() == first


def test_target_specific_transitive_requirement_blocks_publication(approved_wheels):
    directory, lock, output = approved_wheels
    _wheel(directory, 'fastapi', '200.0', dependencies=('fixture-client[windows]',))
    _metadata_wheel(
        directory, 'fixture-client',
        'Provides-Extra: windows',
        'Requires-Dist: fixture-native>=1; extra == "windows" and platform_machine == "AMD64"',
    )
    result = _prepare(directory, lock, output, _pin(directory, lock))
    assert result['status'] == 'BLOCKED'
    assert result['missing_dependencies'] == [('fixture-client', 'fixture-native')]
    assert 'missing_unconditional_dependencies' not in result
    assert not output.exists()


@pytest.mark.parametrize(('edge', 'conflict'), [
    ('fixture-child==1.*', 'VERSION'),
    ('fixture-child[absent-extra]', 'EXTRA'),
])
def test_present_wheel_must_satisfy_declared_constraint(approved_wheels, edge, conflict):
    directory, lock, output = approved_wheels
    _wheel(directory, 'fastapi', '200.0', dependencies=(edge,))
    _wheel(directory, 'fixture-child', '2.0')
    result = _prepare(directory, lock, output, _pin(directory, lock))
    assert result['blockers'] == ['WHEELSET_DEPENDENCY_CONFLICT']
    assert result['dependency_conflicts'] == [('fastapi', 'fixture-child', conflict)]
    assert not output.exists()


@pytest.mark.parametrize(('header', 'reason'), [
    ('Requires-Python: >=3.14', 'WHEELSET_REQUIRES_DIFFERENT_PYTHON'),
    ('Requires-Python: Windows-AMD64', 'INVALID_WHEEL_METADATA'),
    ('Requires-Python: café', 'INVALID_WHEEL_METADATA'),
    ('Requires-Dist: fixture invalid constraint', 'INVALID_WHEEL_METADATA'),
    ('Requires-Dist: fixture @ https://example.invalid/fixture.whl; extra == "unused"',
     'DIRECT_WHEEL_DEPENDENCY_FORBIDDEN'),
])
def test_invalid_or_unsupported_metadata_blocks_preparation(approved_wheels, header, reason):
    directory, lock, output = approved_wheels
    _metadata_wheel(directory, 'fastapi', header)
    result = _prepare(directory, lock, output, _pin(directory, lock))
    assert result['status'] == 'BLOCKED' and result['blockers'] == [reason]
    assert not output.exists()


@pytest.mark.parametrize('large_description', [False, True])
def test_metadata_bound_applies_to_headers_only(approved_wheels, large_description):
    directory, lock, output = approved_wheels
    large_text = 'x' * (2 * runtime._MAX_WHEEL_METADATA_HEADER_BYTES)
    if large_description:
        _metadata_wheel(directory, 'pydantic', description=large_text)
    else:
        _metadata_wheel(directory, 'pydantic', f'Summary: {large_text}')
    result = _prepare(directory, lock, output, _pin(directory, lock))
    if large_description:
        assert result['status'] == 'INSTALL_REQUIREMENTS_WRITTEN', result
    else:
        assert result['status'] == 'BLOCKED' and result['blockers'] == ['INVALID_WHEEL_METADATA']
        assert not output.exists()
