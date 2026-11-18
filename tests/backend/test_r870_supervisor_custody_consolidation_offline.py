"""Integration cases crossing the supervisor, custody and SQLite commit boundaries."""

from contextlib import closing
import sqlite3

import pytest

from backend.multimodal import document_source_maintenance as maintenance
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal.document_reasoning import ReasoningError
from recovery_custody_fixture import SyntheticCustodian
from test_a22_async_offline import run_offline
from test_r863_source_maintenance_offline import authority  # noqa: F401
from test_r867_deployment_supervisor_offline import admit, controlled, platform, prepare, state  # noqa: F401


def _validate(app, issuer, evidence):
    recovery.validate_source_restore(app, trusted_checkpoint=evidence, custody=issuer.custody)


def test_idle_supervisor_needs_explicit_release_and_new_checkpoint_generation(controlled):
    app, database, _, owner = controlled
    issuer = SyntheticCustodian(database)
    digest = recovery.capture_source_checkpoint(app)
    recovery.quarantine_source_authority(app)
    pending = state(database)
    evidence = issuer.issue(digest, pending.generation).encode()
    with pytest.raises(ReasoningError) as denied:
        _validate(app, issuer, evidence)
    assert denied.value.code == 'AUTHORIZATION_UNAVAILABLE'
    assert state(database) == pending

    recovery.set_recovery_maintenance(database, pending.generation, active=True)
    owner.release_maintenance(pending.generation)
    released = state(database)
    assert released.pending and released.generation != pending.generation
    with pytest.raises(ReasoningError):
        _validate(app, issuer, evidence)
    assert state(database) == released

    _validate(app, issuer, issuer.issue(digest, released.generation).encode())
    assert not state(database).pending
    with pytest.raises(ReasoningError):
        owner.start_worker()


def test_crash_with_current_custody_still_blocks_release_and_preflight(controlled, platform, tmp_path):
    app, database, boundary, owner = controlled
    digest = recovery.capture_source_checkpoint(app)
    bundle = tmp_path / 'bundle'
    maintenance._backup(database, bundle)

    async def crash():
        child, _, _ = admit(database, boundary, owner, platform)
        pending = prepare(app, database)
        boundary.exit(child)
        return pending

    pending = run_offline(crash())
    issuer = SyntheticCustodian(database)
    signed = issuer.issue(digest, pending.generation).encode()
    proof = tmp_path / 'custody.json'
    proof.write_bytes(signed)
    with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)) as unmanaged:
        unmanaged.execute('BEGIN')
        unmanaged.execute('SELECT COUNT(*) FROM sqlite_schema').fetchone()
        result = maintenance._preflight(database, bundle / maintenance._BACKUP, proof, pending.generation,
                                        custody=issuer.custody)
    assert result.status == 'BLOCKED'
    assert 'AUTHENTICATED_CHECKPOINT_CURRENT' in result.checks
    assert {'WORKER_SHUTDOWN_ACK_MISSING', 'SQLITE_CLIENT_INVENTORY_UNVERIFIED'} <= set(result.blockers)
    with pytest.raises(ReasoningError):
        owner.release_maintenance(pending.generation)
    with pytest.raises(ReasoningError):
        _validate(app, issuer, signed)
    assert state(database) == pending


@pytest.mark.parametrize('issuer_outage', [False, True])
def test_issuer_rechecked_at_commit_without_changing_retained_fingerprint(authority, monkeypatch, issuer_outage):
    app, database = authority
    digest = recovery.capture_source_checkpoint(app)
    recovery.quarantine_source_authority(app)
    pending = state(database)
    issuer = SyntheticCustodian(database)
    signed = issuer.issue(digest, pending.generation).encode()
    authenticate = issuer.authenticate
    calls = []

    def revoke_during_validation(*args):
        calls.append(True)
        if len(calls) == 1:
            return authenticate(*args)
        if issuer_outage:
            raise OSError('fixture issuer unavailable')
        return False

    monkeypatch.setattr(issuer, 'authenticate', revoke_during_validation)
    with pytest.raises(ReasoningError):
        _validate(app, issuer, signed)
    assert len(calls) == 2 and state(database) == pending
    with pytest.raises(ReasoningError):
        recovery.SourceRestoreBinding(app, database)


def test_validation_holds_wal_catalog_stable_until_coordinator_commit(authority, monkeypatch):
    app, database = authority
    with closing(sqlite3.connect(database, timeout=0)) as other:
        assert other.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
        digest = recovery.capture_source_checkpoint(app)
        recovery.quarantine_source_authority(app)
        pending = state(database)
        issuer = SyntheticCustodian(database)
        signed = issuer.issue(digest, pending.generation).encode()
        authenticate = issuer.authenticate
        attempts = []

        def try_write_during_commit(*args):
            result = authenticate(*args)
            attempts.append(True)
            if len(attempts) == 2:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    other.execute('BEGIN IMMEDIATE')
            return result

        monkeypatch.setattr(issuer, 'authenticate', try_write_during_commit)
        _validate(app, issuer, signed)
        assert len(attempts) == 2 and not state(database).pending
        assert recovery.capture_source_checkpoint(app) == digest
        other.execute('BEGIN IMMEDIATE')  # The guard does not hold the lock after release.
        other.rollback()
