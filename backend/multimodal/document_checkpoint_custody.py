"""Canonical source checkpoint evidence for an independently administered authority.

The deployment supplies the authority out of band. This module owns no key,
current-checkpoint store, signing operation, file, or network client.
"""

from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Protocol


MAX_EVIDENCE_BYTES = 4096
_ID = re.compile('[0-9a-f]{32}')
_SHA256 = re.compile('[0-9a-f]{64}')
_ISSUER = re.compile('[a-z0-9][a-z0-9._-]{0,63}')
_AUTHENTICATION = re.compile('(?:[0-9a-f]{2}){1,512}')
_DOMAIN = b'sovereignai:source-checkpoint-custody:v1\x00'


class CheckpointCustodyError(ValueError):
    """A bounded diagnostic without checkpoint contents or credentials."""


def _is_token(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')


@dataclass(frozen=True, slots=True)
class CheckpointStatement:
    checkpoint_id: str
    deployment_id: str
    database_id: str
    catalog_sha256: str
    recovery_generation: str
    created_at: int  # Unix time in UTC, with one representation and no fractional seconds.
    issuer_id: str
    version: int = 1

    def __post_init__(self):
        ids = (self.checkpoint_id, self.deployment_id, self.database_id, self.recovery_generation)
        if (not all(_is_token(value, _ID) for value in ids) or not _is_token(self.catalog_sha256, _SHA256)
                or not _is_token(self.issuer_id, _ISSUER) or type(self.version) is not int or self.version != 1
                or type(self.created_at) is not int or not 1 <= self.created_at <= 253402300799):
            raise CheckpointCustodyError('INVALID_CHECKPOINT_EVIDENCE')

    def authentication_message(self) -> bytes:
        return _DOMAIN + _canonical(asdict(self))


@dataclass(frozen=True, slots=True)
class CheckpointEvidence:
    statement: CheckpointStatement
    authentication: bytes = field(repr=False)

    def __post_init__(self):
        if (type(self.statement) is not CheckpointStatement or type(self.authentication) is not bytes
                or not 1 <= len(self.authentication) <= 512):
            raise CheckpointCustodyError('INVALID_CHECKPOINT_EVIDENCE')

    def encode(self) -> bytes:
        return _canonical({'authentication': self.authentication.hex(), 'checkpoint': asdict(self.statement)})

    @classmethod
    def decode(cls, data: bytes):
        if type(data) is bytes and 0 < len(data) <= MAX_EVIDENCE_BYTES:
            try:
                envelope = json.loads(data.decode('ascii'))
                authentication = envelope['authentication']
                if _is_token(authentication, _AUTHENTICATION):
                    evidence = cls(CheckpointStatement(**envelope['checkpoint']), bytes.fromhex(authentication))
                    if evidence.encode() == data:  # Reject duplicate members, alternate encodings and extra fields.
                        return evidence
            except (KeyError, ValueError, TypeError, RecursionError):
                pass
        raise CheckpointCustodyError('INVALID_CHECKPOINT_EVIDENCE')


class IndependentCheckpointAuthority(Protocol):
    """Bounded, read-only, local checks from a separately retained trust domain."""

    def authenticate(self, issuer_id: str, message: bytes, authentication: bytes) -> bool:
        """Verify using independently configured issuer material and algorithm policy."""
        ...

    def current_fingerprint(self, deployment_id: str, database_id: str, recovery_generation: str) -> bytes | None:
        """Return SHA-256 of the current authentication message from independent custody.

        The anchor is selected using trusted scope, never populated from candidate
        evidence. Return None if there is no authoritative current record.
        """
        ...


@dataclass(frozen=True, slots=True)
class CheckpointCustody:
    """Bind a trusted deployment's external authority to one canonical database."""

    database: Path
    deployment_id: str
    database_id: str
    issuer_id: str
    authority: IndependentCheckpointAuthority = field(repr=False)

    def __post_init__(self):
        if (not isinstance(self.database, Path) or not self.database.is_absolute()
                or self.database.resolve(strict=True) != self.database or not self.database.is_file()
                or not _is_token(self.deployment_id, _ID)
                or not _is_token(self.database_id, _ID) or not _is_token(self.issuer_id, _ISSUER)):
            raise CheckpointCustodyError('CHECKPOINT_CUSTODY_UNVERIFIED')

    def _require_scope(self, statement: CheckpointStatement, *, database: Path, generation: str) -> None:
        if (database.resolve(strict=True) != self.database or statement.deployment_id != self.deployment_id
                or statement.database_id != self.database_id or statement.issuer_id != self.issuer_id
                or statement.recovery_generation != generation):
            raise CheckpointCustodyError('CHECKPOINT_SCOPE_MISMATCH')

    def require_current(self, statement: CheckpointStatement, *, database: Path, generation: str) -> None:
        self._require_scope(statement, database=database, generation=generation)
        try:
            retained = self.authority.current_fingerprint(self.deployment_id, self.database_id, generation)
        except Exception:
            raise CheckpointCustodyError('CHECKPOINT_CUSTODY_UNVERIFIED') from None
        if type(retained) is not bytes or len(retained) != 32:
            raise CheckpointCustodyError('CHECKPOINT_CUSTODY_UNVERIFIED')
        if not hmac.compare_digest(retained, hashlib.sha256(statement.authentication_message()).digest()):
            raise CheckpointCustodyError('CHECKPOINT_EVIDENCE_NOT_CURRENT')

    def verify(self, data: bytes, *, database: Path, generation: str) -> CheckpointStatement:
        evidence = CheckpointEvidence.decode(data)
        statement = evidence.statement
        self._require_scope(statement, database=database, generation=generation)
        try:
            authenticated = self.authority.authenticate(self.issuer_id, statement.authentication_message(), evidence.authentication)
        except Exception:
            raise CheckpointCustodyError('CHECKPOINT_CUSTODY_UNVERIFIED') from None
        if authenticated is not True:
            raise CheckpointCustodyError('CHECKPOINT_AUTHENTICATION_FAILED')
        self.require_current(statement, database=database, generation=generation)
        return statement
