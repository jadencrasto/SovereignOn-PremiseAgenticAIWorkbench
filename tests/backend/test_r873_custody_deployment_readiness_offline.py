"""Separate issuer and freshness adapters and an externally pinned wheel lock."""

from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from backend.multimodal import document_source_maintenance as maintenance
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal.document_checkpoint_custody import CheckpointCustody, CheckpointCustodyError
from backend.multimodal.document_reasoning import ReasoningError
from backend.runtime import windows_backend_readiness as runtime
from recovery_custody_fixture import SyntheticCustodian
from test_r863_source_maintenance_offline import authority  # noqa: F401
from test_r869_checkpoint_custody_offline import retained  # noqa: F401


def _custody(issuer, *, issuers=True, freshness=True):
    base = issuer.custody
    return CheckpointCustody.from_independent_sources(
        base.database, deployment_id=base.deployment_id, database_id=base.database_id, issuer_id=base.issuer_id,
        issuers=SimpleNamespace(verifier_for=lambda name: (
            (lambda message, proof: issuer.authenticate(name, message, proof)) if name == issuer.issuer else None
        )) if issuers else None,
        freshness=SimpleNamespace(current_fingerprint=issuer.current_fingerprint) if freshness else None,
    )


@pytest.mark.parametrize(('issuers', 'freshness'), [(False, True), (True, False)])
def test_both_independently_supplied_sources_are_required(retained, issuers, freshness):
    *_, issuer, _ = retained
    with pytest.raises(CheckpointCustodyError, match='CHECKPOINT_CUSTODY_UNVERIFIED'):
        _custody(issuer, issuers=issuers, freshness=freshness)


@pytest.mark.parametrize('fault', ['revoked', 'missing_record', 'old_valid_checkpoint', 'database', 'deployment', 'modified'])
def test_separate_sources_reject_invalid_or_noncurrent_evidence(retained, fault):
    app, database, pending, issuer, evidence = retained
    custody = _custody(issuer)
    if fault == 'revoked':
        issuer.issuer = 'other-active-issuer'
    elif fault == 'missing_record':
        issuer.records.clear()
    elif fault == 'old_valid_checkpoint':
        issuer.retain(replace(evidence.statement, checkpoint_id='9' * 32))
    elif fault in ('database', 'deployment'):
        evidence = issuer.sign(replace(evidence.statement, **{f'{fault}_id': 'a' * 32}))
    else:
        evidence = replace(evidence, authentication=b'wrong-fixture-proof')
    with pytest.raises(ReasoningError) as denied:
        recovery.validate_source_restore(app, trusted_checkpoint=evidence.encode(), custody=custody)
    assert denied.value.code == 'AUTHORIZATION_UNAVAILABLE'
    assert recovery._read_shared_state(recovery._coordination_path(database)) == pending


def test_separate_sources_allow_read_only_preflight_and_reject_replay_after_release(authority, tmp_path, monkeypatch, capsys):
    app, database = authority
    bundle = tmp_path / 'bundle'
    maintenance._backup(database, bundle)
    digest = recovery.capture_source_checkpoint(app)
    recovery.quarantine_source_authority(app)
    coordinator = recovery._coordination_path(database)
    pending = recovery._read_shared_state(coordinator)
    issuer = SyntheticCustodian(database)
    custody = _custody(issuer)
    evidence = tmp_path / 'custody.json'
    evidence.write_bytes(issuer.issue(digest, pending.generation).encode())
    monkeypatch.setattr(maintenance, '_local_operator', lambda _: 'fixture-operator')
    monkeypatch.setattr(maintenance, '_guard_location', lambda *args, **kwargs: None)
    fixture_bundle = maintenance._new_bundle
    monkeypatch.setattr(maintenance, '_new_bundle', lambda directory, filename, **_: fixture_bundle(directory, filename))
    output = tmp_path / 'export'
    assert maintenance.main(['export-checkpoint', '--backup', str(bundle / maintenance._BACKUP),
                             '--output-directory', str(output)], custody=custody) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'CHECKPOINT_EXPORTED_UNTRUSTED'
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    status = ['status', '--database', str(database)]
    assert maintenance.main(status) == 0
    assert json.loads(capsys.readouterr().out)['checkpoint_custody_configuration'] == 'UNPROVISIONED'
    assert maintenance.main(status, custody=custody) == 0
    configured = json.loads(capsys.readouterr().out)
    assert configured['checkpoint_custody_configuration'] == 'CONFIGURED_UNVERIFIED'
    assert configured['checkpoint_custody_verified'] is False
    assert maintenance.main(['preflight', '--database', str(database), '--backup', str(bundle / maintenance._BACKUP),
                             '--trusted-checkpoint', str(evidence), '--expected-generation', pending.generation],
                            custody=custody) == 2
    checked = json.loads(capsys.readouterr().out)
    assert 'AUTHENTICATED_CHECKPOINT_CURRENT' in checked['checks'] and checked['status'] == 'BLOCKED'
    assert {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()} == before
    recovery.validate_source_restore(app, trusted_checkpoint=evidence.read_bytes(), custody=custody)
    recovery.quarantine_source_authority(app)
    with pytest.raises(ReasoningError):
        recovery.validate_source_restore(app, trusted_checkpoint=evidence.read_bytes(), custody=custody)
    assert recovery._read_shared_state(coordinator).pending


@pytest.fixture
def wheelset(tmp_path):
    directory = tmp_path / 'wheels'
    directory.mkdir()
    wheels = {}
    for name, _, _ in runtime._STARTUP:
        filename = f"{name.lower().replace('-', '_')}-200.0-py3-none-any.whl"
        fixture = f'disposable hash input for {name}'.encode('ascii')
        (directory / filename).write_bytes(fixture)
        wheels[filename] = hashlib.sha256(fixture).hexdigest()
    # A fixture name with the CPython stable ABI and Windows x64 platform.
    filename = 'fixture_native-1.1-cp39-abi3-win_amd64.whl'
    (directory / filename).write_bytes(b'disposable native-tag hash fixture')
    wheels[filename] = hashlib.sha256((directory / filename).read_bytes()).hexdigest()
    return directory, tmp_path / 'approved.json', {'schema': 1, 'target': 'cp313-win_amd64', 'wheels': wheels}


def _verify(wheelset):
    directory, path, lock = wheelset
    data = json.dumps(lock, sort_keys=True).encode('ascii')
    path.write_bytes(data)
    return runtime.verify_wheelset(directory, path, approved_lock_sha256=hashlib.sha256(data).hexdigest())


def test_wheelset_requires_exact_names_and_hashes_from_independently_approved_lock(wheelset):
    directory, path, lock = wheelset
    assert _verify(wheelset)['status'] == 'WHEELSET_MATCHES_REVIEWED_LOCK'
    assert runtime.verify_wheelset(directory, path, approved_lock_sha256='0' * 64)['blockers'] == [
        'WHEEL_LOCK_DIGEST_MISMATCH',
    ]
    filename = next(iter(lock['wheels']))
    (directory / filename).write_bytes(b'modified fixture')
    actual = _verify(wheelset)
    assert actual['status'] == 'BLOCKED' and actual['mismatched_artifacts'] == [filename]


@pytest.mark.parametrize('fault', ['missing_file', 'unreviewed_file', 'incomplete_lock', 'wrong_architecture', 'below_floor'])
def test_wheelset_reports_missing_or_unreviewed_artifacts(wheelset, fault):
    directory, _, lock = wheelset
    filename = next(iter(lock['wheels']))
    if fault in ('missing_file', 'incomplete_lock'):
        (directory / filename).unlink()
        if fault == 'incomplete_lock':
            del lock['wheels'][filename]
    elif fault == 'unreviewed_file':
        (directory / 'extra-1.0-py3-none-any.whl').write_bytes(b'not in lock')
    else:
        old = 'fixture_native-1.1-cp39-abi3-win_amd64.whl' if fault == 'wrong_architecture' else filename
        filename = old.replace('win_amd64', 'win_arm64') if fault == 'wrong_architecture' else old.replace('200.0', '0.1.0')
        (directory / old).rename(directory / filename)
        lock['wheels'][filename] = lock['wheels'].pop(old)
    actual = _verify(wheelset)
    assert actual['status'] == 'BLOCKED'
    if fault == 'missing_file':
        assert actual['missing_artifacts'] == [filename]
    elif fault == 'unreviewed_file':
        assert actual['unreviewed_entries'] == 1
    elif fault == 'incomplete_lock':
        assert actual['missing_startup_packages'] == ['fastapi']
    else:
        assert actual['incompatible_artifacts'] == [filename]


@pytest.mark.parametrize('fault', ['oversized', 'duplicate', 'traversal'])
def test_corrupt_or_unsafe_review_material_fails_closed(wheelset, fault):
    directory, path, _ = wheelset
    if fault == 'oversized':
        data = b' ' * (runtime._MAX_LOCK_BYTES + 1)
    elif fault == 'duplicate':
        data = b'{"schema":1,"schema":1,"target":"cp313-win_amd64","wheels":{}}'
    else:
        data = json.dumps({'schema': 1, 'target': 'cp313-win_amd64',
                           'wheels': {'../escape-1.0-py3-none-any.whl': '0' * 64}}).encode('ascii')
    path.write_bytes(data)
    actual = runtime.verify_wheelset(directory, path, approved_lock_sha256=hashlib.sha256(data).hexdigest())
    assert actual['status'] == 'BLOCKED'
    assert actual['blockers'] == ['ARTIFACT_SIZE_OR_TYPE_INVALID' if fault == 'oversized' else 'INVALID_WHEEL_LOCK']


def test_arm64_runtime_cannot_supply_amd64_acceptance(monkeypatch):
    monkeypatch.setattr(runtime, '_interpreter_issues', lambda *args: [])
    monkeypatch.setattr(runtime, 'sys', SimpleNamespace(platform='win32', version_info=(3, 13, 7)))
    monkeypatch.setattr(runtime.sysconfig, 'get_platform', lambda: 'win-arm64')
    monkeypatch.setattr(runtime, '_dependency_issues', lambda: pytest.fail('Unverified runtime searched packages'))
    monkeypatch.setattr(runtime, '_native_issues', lambda: pytest.fail('Unverified runtime loaded DLLs'))
    result = runtime.inspect_runtime()
    assert result['status'] == 'BLOCKED' and result['interpreter_issues'] == ['WINDOWS_AMD64_RUNTIME_REQUIRED']
