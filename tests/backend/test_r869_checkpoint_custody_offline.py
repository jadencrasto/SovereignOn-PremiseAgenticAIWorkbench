"""Exercise authenticated checkpoint custody without Windows keys or networking."""

from dataclasses import replace
import json

import pytest

from backend.multimodal import document_source_maintenance as maintenance
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal.document_checkpoint_custody import MAX_EVIDENCE_BYTES, CheckpointCustodyError, CheckpointEvidence
from backend.multimodal.document_reasoning import ReasoningError
from backend.security.provenance_foundation import document_content_allowed
from recovery_custody_fixture import SyntheticCustodian
from test_r863_source_maintenance_offline import authority  # noqa: F401


def _state(database):
    return recovery._read_shared_state(recovery._coordination_path(database))


@pytest.fixture
def retained(authority):
    app, database = authority
    current_catalog = recovery.capture_source_checkpoint(app)
    recovery.quarantine_source_authority(app)
    state = _state(database)
    issuer = SyntheticCustodian(database)
    evidence = issuer.issue(current_catalog, state.generation)
    return app, database, state, issuer, evidence


def _assert_blocked(app, database, state, candidate, *, custody=None):
    with pytest.raises(ReasoningError) as denied:
        recovery.validate_source_restore(app, trusted_checkpoint=candidate, custody=custody)
    assert denied.value.code == 'AUTHORIZATION_UNAVAILABLE'
    assert _state(database) == state and state.pending


def test_canonical_format_and_signed_field_coverage(retained):
    *_, issuer, evidence = retained
    canonical = evidence.encode()
    assert CheckpointEvidence.decode(canonical) == evidence
    message = evidence.statement.authentication_message()
    assert message.startswith(b'sovereignai:source-checkpoint-custody:v1\x00')
    assert json.loads(message.split(b'\x00', 1)[1]) == json.loads(canonical)['checkpoint']
    assert evidence.authentication.hex() not in repr(evidence)
    assert issuer._key not in canonical


@pytest.mark.parametrize('damage', ['whitespace', 'reordered', 'duplicate', 'extra', 'boolean_time', 'overflow', 'deep', 'hex', 'bad_bytes'])
def test_noncanonical_corrupt_and_oversized_evidence_is_rejected(retained, damage):
    *_, evidence = retained
    candidate = evidence.encode()
    as_mapping = json.loads(candidate)
    malformed = {
        'whitespace': lambda: candidate + b'\n',
        'reordered': lambda: json.dumps(dict(reversed(tuple(as_mapping.items()))), separators=(',', ':')).encode(),
        'duplicate': lambda: candidate.replace(b'"version":1', b'"version":1,"version":1'),
        'extra': lambda: candidate[:-1] + b',"key":"ignored"}',
        'boolean_time': lambda: candidate.replace(b'1700000000', b'true'),
        'overflow': lambda: candidate + b' ' * MAX_EVIDENCE_BYTES,
        'deep': lambda: b'[' * 1500 + b']' * 1500,
        'hex': lambda: candidate.replace(evidence.authentication.hex().encode(), b'zz'),
        'bad_bytes': lambda: candidate[:-1] + b'\xff',
    }[damage]()
    with pytest.raises(CheckpointCustodyError, match='INVALID_CHECKPOINT_EVIDENCE'):
        CheckpointEvidence.decode(malformed)


@pytest.mark.parametrize(('field', 'value'), [
    ('deployment_id', '3' * 32), ('database_id', '4' * 32), ('issuer_id', 'another-issuer'),
    ('recovery_generation', '5' * 32),
])
def test_valid_authentication_cannot_move_evidence_between_scopes(retained, field, value):
    app, database, state, issuer, evidence = retained
    foreign = replace(evidence.statement, **{field: value})
    issuer.retain(foreign)
    _assert_blocked(app, database, state, issuer.sign(foreign).encode(), custody=issuer.custody)


def test_custody_configured_for_another_file_cannot_authorize_recovery(retained, tmp_path):
    app, database, state, issuer, evidence = retained
    foreign = tmp_path / 'logical-database.db'
    foreign.touch()
    _assert_blocked(app, database, state, evidence.encode(), custody=replace(issuer.custody, database=foreign))


@pytest.mark.parametrize('damage', ['missing_authority', 'missing_record', 'raw_digest_with_authority', 'raw_digest_alone', 'forged', 'time_modified', 'substituted', 'wrong_catalog'])
def test_quarantine_survives_failed_authentication_or_freshness(retained, damage):
    app, database, state, issuer, evidence = retained
    custody, candidate = issuer.custody, evidence.encode()
    if damage == 'missing_authority':
        custody = None
    elif damage == 'missing_record':
        issuer.records.clear()
    elif damage.startswith('raw_digest'):
        candidate = evidence.statement.catalog_sha256.encode('ascii')
        if damage == 'raw_digest_alone':
            custody = None
    elif damage == 'forged':
        candidate = replace(evidence, authentication=b'0' * 32).encode()
    elif damage == 'time_modified':
        candidate = replace(evidence, statement=replace(evidence.statement, created_at=1700000001)).encode()
    elif damage == 'substituted':
        candidate = issuer.sign(replace(evidence.statement, checkpoint_id='9' * 32)).encode()
    else:
        candidate = issuer.issue('0' * 64, state.generation).encode()
    _assert_blocked(app, database, state, candidate, custody=custody)


def test_fresh_authenticated_checkpoint_clears_only_current_quarantine(retained):
    app, database, pending, issuer, evidence = retained
    recovery.validate_source_restore(app, trusted_checkpoint=evidence.encode(), custody=issuer.custody)
    ready = _state(database)
    assert not ready.pending and ready.generation != pending.generation
    recovery.quarantine_source_authority(app)
    _assert_blocked(app, database, _state(database), evidence.encode(), custody=issuer.custody)
    assert not document_content_allowed()
    for item in database.parent.iterdir():
        if item.is_file():
            data = item.read_bytes()
            assert b'sovereignai:source-checkpoint-custody' not in data
            assert evidence.authentication.hex().encode() not in data
            assert issuer._key not in data


def test_custody_rotation_after_digest_scan_preserves_quarantine(retained, monkeypatch):
    app, database, state, issuer, evidence = retained
    checkpoint = recovery._scan_checkpoint

    def scan_and_rotate(connection, deadline):
        digest = checkpoint(connection, deadline)
        issuer.retain(replace(evidence.statement, checkpoint_id='9' * 32))
        return digest

    monkeypatch.setattr(recovery, '_scan_checkpoint', scan_and_rotate)
    _assert_blocked(app, database, state, evidence.encode(), custody=issuer.custody)


def test_cli_preflight_verifies_evidence_without_authorizing_restore_or_writing(authority, tmp_path, monkeypatch, capsys):
    app, database = authority
    bundle = tmp_path / 'backup'
    maintenance._backup(database, bundle)
    backup = bundle / maintenance._BACKUP
    ordinary = tmp_path / 'export'
    maintenance._export_checkpoint(backup, ordinary)
    digest = recovery.capture_source_checkpoint(app)
    recovery.quarantine_source_authority(app)
    state = _state(database)
    issuer = SyntheticCustodian(database)
    evidence_file = tmp_path / 'authenticated.json'
    evidence_file.write_bytes(issuer.issue(digest, state.generation).encode())
    snapshot = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    monkeypatch.setattr(maintenance, '_local_operator', lambda _: 'fixture-operator')
    monkeypatch.setattr(maintenance, '_guard_location', lambda *args, **kwargs: None)

    def preflight(checkpoint, *, custody=None):
        args = ['preflight', '--database', str(database), '--backup', str(backup), '--trusted-checkpoint',
                str(checkpoint), '--expected-generation', state.generation]
        assert maintenance.main(args, custody=custody) == 2
        result = json.loads(capsys.readouterr().out)
        assert result['status'] == 'BLOCKED'
        return result

    assert preflight(evidence_file)['blockers'] == ['CHECKPOINT_CUSTODY_UNVERIFIED']
    verified = preflight(evidence_file, custody=issuer.custody)
    assert 'AUTHENTICATED_CHECKPOINT_CURRENT' in verified['checks']
    assert 'CHECKPOINT_CUSTODY_UNVERIFIED' not in verified['blockers']
    assert 'SQLITE_CLIENT_INVENTORY_UNVERIFIED' in verified['blockers']
    exported = preflight(ordinary / maintenance._CHECKPOINT, custody=issuer.custody)
    assert 'SUPPLIED_CHECKPOINT_MATCHES' in exported['checks']
    assert 'CHECKPOINT_CUSTODY_UNVERIFIED' in exported['blockers']
    assert maintenance.main(['status', '--database', str(database)], custody=issuer.custody) == 0
    assert json.loads(capsys.readouterr().out)['checkpoint_custody_verified'] is False
    _assert_blocked(app, database, state, (ordinary / maintenance._CHECKPOINT).read_bytes(), custody=issuer.custody)
    assert {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()} == snapshot
