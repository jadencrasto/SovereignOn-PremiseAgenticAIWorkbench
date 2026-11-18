"""Synthetic external checkpoint issuer for offline recovery regressions."""

import hashlib
import hmac

from backend.agent.sqlite_transaction import read_only_connection
from backend.multimodal import document_source_recovery as recovery
from backend.multimodal.document_checkpoint_custody import CheckpointCustody, CheckpointEvidence, CheckpointStatement


class SyntheticCustodian:
    _key = b'non-production checkpoint custody fixture key'
    issuer = 'fixture-issuer'

    def __init__(self, database):
        deployment_id = '1' * 32
        database_id = hashlib.sha256(database.as_uri().encode('ascii')).hexdigest()[:32]
        self.custody = CheckpointCustody(database, deployment_id, database_id, self.issuer, self)
        self.records = {}

    def sign(self, statement):
        proof = hmac.digest(self._key, statement.authentication_message(), 'sha256')
        return CheckpointEvidence(statement, proof)

    def retain(self, statement):
        scope = (statement.deployment_id, statement.database_id, statement.recovery_generation)
        self.records[scope] = hashlib.sha256(statement.authentication_message()).digest()

    def issue(self, catalog_digest, generation):
        statement = CheckpointStatement('2' * 32, self.custody.deployment_id, self.custody.database_id,
                                        catalog_digest, generation, 1700000000, self.issuer)
        self.retain(statement)
        return self.sign(statement)

    def authenticate(self, issuer_id, message, authentication):
        return issuer_id == self.issuer and hmac.compare_digest(hmac.digest(self._key, message, 'sha256'), authentication)

    def current_fingerprint(self, deployment_id, database_id, generation):
        return self.records.get((deployment_id, database_id, generation))


def restore_with_checkpoint(app, *, trusted_checkpoint):
    """Drive historical lifecycle assertions using a synthetic trusted handoff."""
    database = app.state.settings.tasks_db_path.resolve(strict=True)
    coordinator = recovery._coordination_path(database)
    if trusted_checkpoint is None or not coordinator.exists():
        return recovery.validate_source_restore(app, trusted_checkpoint=None)
    issuer = SyntheticCustodian(database)
    with read_only_connection(coordinator) as connection:
        generation = recovery._read_state(connection).generation
    if len(trusted_checkpoint) == 64:
        payload = issuer.issue(trusted_checkpoint, generation).encode()
    else:
        payload = trusted_checkpoint.encode('ascii')
    return recovery.validate_source_restore(app, trusted_checkpoint=payload, custody=issuer.custody)
