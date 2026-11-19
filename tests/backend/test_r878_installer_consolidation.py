"""Regression tests for the disposable installer publication boundary."""

from pathlib import Path

import pytest

from backend.runtime import windows_backend_readiness as runtime
from test_r875_offline_runtime_provisioning import _pin, approved_wheels  # noqa: F401


@pytest.fixture
def deployment_install(approved_wheels, monkeypatch):
    directory, lock, _ = approved_wheels
    pin = _pin(directory, lock)
    monkeypatch.setattr(runtime, '_interpreter_issues', lambda *_: [])
    destination = directory.parent / 'disposable'

    def install():
        return runtime.verify_wheelset(directory, lock, approved_lock_sha256=pin, install_destination=destination)

    return directory, destination, install


def _fixture_packages(packages: Path, origin: str) -> None:
    packages.mkdir()
    for name, _, _ in runtime._STARTUP:
        info = packages / f'{name.replace("-", "_")}-200.0.dist-info'
        info.mkdir()
        (info / 'METADATA').write_text(f'Name: {name}\nVersion: 200.0\n', encoding='ascii')
    (packages / 'origin.txt').write_text(origin, encoding='ascii')


def test_two_installers_racing_for_one_destination_have_one_winner(deployment_install, monkeypatch):
    _, destination, install = deployment_install
    entered = []
    results = []

    def installer(stage, _, __, packages):
        entered.append(stage)
        assert not destination.exists()
        if len(entered) == 1:
            _fixture_packages(packages, 'first')
            results.append(install())
        else:
            _fixture_packages(packages, 'second')

    monkeypatch.setattr(runtime, '_run_local_installer', installer)
    results.append(install())
    assert len(set(entered)) == 2
    assert [result['status'] for result in results] == ['DISPOSABLE_PACKAGES_INSTALLED', 'BLOCKED']
    assert results[1]['blockers'] == ['DISPOSABLE_INSTALL_DESTINATION_EXISTS']
    assert (destination / 'site-packages' / 'origin.txt').read_text(encoding='ascii') == 'second'
    assert list(destination.parent.glob('.offline-install-*')) == []


def test_mutation_after_review_is_rejected_before_resolver(deployment_install, monkeypatch):
    directory, destination, install = deployment_install
    original = runtime._install_disposable

    def mutate(*args):
        wheel = next(directory.glob('fastapi-*.whl'))
        wheel.write_bytes(b'x' * wheel.stat().st_size)
        return original(*args)

    monkeypatch.setattr(runtime, '_install_disposable', mutate)
    monkeypatch.setattr(runtime, '_run_local_installer', lambda *_: pytest.fail('resolver started'))
    result = install()
    assert result['status'] == 'BLOCKED' and result['blockers'] == ['WHEEL_DIGEST_MISMATCH']
    assert not destination.exists()
    assert list(destination.parent.glob('.offline-install-*')) == []


def test_changed_stage_is_not_published_or_removed_by_cleanup(deployment_install, monkeypatch):
    _, destination, install = deployment_install
    moved = destination.parent / 'relocated'
    observed = []

    def installer(stage, _, __, packages):
        _fixture_packages(packages, 'original')
        stage.rename(moved)
        stage.mkdir()
        (stage / 'keep.txt').write_text('replacement owner', encoding='ascii')
        observed.append(stage)

    monkeypatch.setattr(runtime, '_run_local_installer', installer)
    result = install()
    assert result['status'] == 'BLOCKED' and result['blockers'] == ['INSTALL_DIRECTORY_CHANGED']
    assert not destination.exists()
    assert (observed[0] / 'keep.txt').read_text(encoding='ascii') == 'replacement owner'
    assert (moved / 'payload' / 'site-packages' / 'origin.txt').is_file()


def test_replaced_payload_is_rejected_even_if_distribution_metadata_matches(deployment_install, monkeypatch):
    _, destination, install = deployment_install

    def installer(stage, _, __, packages):
        payload = packages.parent
        payload.rename(stage / 'former-payload')
        payload.mkdir()
        _fixture_packages(packages, 'replacement')

    monkeypatch.setattr(runtime, '_run_local_installer', installer)
    result = install()
    assert result['status'] == 'BLOCKED' and result['blockers'] == ['INSTALL_DIRECTORY_CHANGED']
    assert not destination.exists()
    assert list(destination.parent.glob('.offline-install-*')) == []
