"""Narrow synthetic contracts; no paths, SQL, payloads, or callables."""
from dataclasses import dataclass
from enum import Enum
import re
from typing import Protocol


class Code(str, Enum):
    OK = 'OK'
    INVALID_REQUEST = 'INVALID_REQUEST'
    UNKNOWN_COMMAND = 'UNKNOWN_COMMAND'
    UNSUPPORTED = 'UNSUPPORTED'
    BUSY = 'BUSY'
    STALE = 'STALE'
    REVOKED = 'REVOKED'
    WITNESS_UNAVAILABLE = 'WITNESS_UNAVAILABLE'
    WITNESS_MISMATCH = 'WITNESS_MISMATCH'
    NOT_FOUND = 'NOT_FOUND'
    LOCK_ORDER = 'LOCK_ORDER'
    INTERNAL_ERROR = 'INTERNAL_ERROR'


@dataclass(frozen=True)
class SourceIdentity:
    source_id: str
    revision: int

    def __post_init__(self):
        if (not isinstance(self.source_id, str) or not re.fullmatch(r'src_[0-9a-f]{32}', self.source_id)
                or type(self.revision) is not int or self.revision < 1):
            raise ValueError('Invalid synthetic source identity')


@dataclass(frozen=True)
class StorageIdentity:
    storage_id: str

    def __post_init__(self):
        if not isinstance(self.storage_id, str) or not re.fullmatch(r'store_[0-9a-f]{32}', self.storage_id):
            raise ValueError('Invalid synthetic storage identity')


@dataclass(frozen=True)
class Fence:
    operation_id: str
    generation: int


class ConditionalGenerationStore(Protocol):
    """Future adapter contract; no implementation or production authorization."""
    def compare_generation_and_state(self, source: SourceIdentity, fence: Fence) -> bool: ...


class FreshnessWitness(Protocol):
    """Future witness contract: availability and authenticated head required."""
    def authenticated_head(self) -> bytes | None: ...


@dataclass(frozen=True)
class Response:
    code: Code
    operation_id: str | None = None
    generation: int | None = None
    state: str = 'SIMULATION_ONLY'
    authorized: bool = False


@dataclass(frozen=True)
class AuditEvent:
    command: str
    code: Code
    state: str = 'SIMULATION_ONLY'


class ProtocolError(Exception):
    def __init__(self, code: Code):
        self.code = code
        super().__init__(code.value)


CAPABILITIES = {
    'simulation': 'AVAILABLE_IN_MEMORY',
    'os_restricted_broker': 'UNSUPPORTED',
    'independent_writer_exclusion': 'UNSUPPORTED',
    'verified_durable_storage': 'UNSUPPORTED',
    'independent_freshness_witness': 'UNSUPPORTED',
    'production_ingestion': 'UNSUPPORTED',
    'retrieval': 'UNSUPPORTED',
    'production_recovery_commit': 'UNSUPPORTED',
    'authorization': 'UNSUPPORTED',
    'artifact_disclosure': 'UNSUPPORTED',
    'code_execution': 'UNSUPPORTED',
}
