"""Only in-memory simulation. No I/O, authentication, durability or isolation."""
from dataclasses import dataclass
import re
import uuid

from .contracts import AuditEvent, Code, Fence, ProtocolError, Response, SourceIdentity, StorageIdentity
from .coordination import SimulatedLocks


@dataclass
class SyntheticRecord:
    source: SourceIdentity
    storage: StorageIdentity
    generation: int = 1
    revoked: bool = False
    state: str = 'RESTART_UNVERIFIED'


class InMemoryStorage:
    """Synthetic state only; not a SQLite adapter or durable provenance."""
    def __init__(self, record: SyntheticRecord):
        if type(record) is not SyntheticRecord:
            raise TypeError('Synthetic records only')
        self.record = record
        self.epoch = 1

    def revoke_for_test(self):
        self.record.revoked = True
        self.record.generation += 1
        self.record.state = 'REVOKED'


class InMemoryWitness:
    """An idealized local value, not an independent rollback witness."""
    def __init__(self, epoch=1, available=True):
        self.epoch, self.available = epoch, available


class SimulatedBroker:
    """Untrusted requests can only simulate observations; no authorization."""
    def __init__(self, storage: InMemoryStorage, witness: InMemoryWitness, locks: SimulatedLocks):
        if type(storage) is not InMemoryStorage or type(witness) is not InMemoryWitness or type(locks) is not SimulatedLocks:
            raise TypeError('Only built-in in-memory simulation components are accepted')
        self.storage, self.witness, self.locks = storage, witness, locks
        self._active = None
        self._issued = set()
        self.audit = []

    def _witness_check(self):
        if not self.witness.available:
            raise ProtocolError(Code.WITNESS_UNAVAILABLE)
        if self.witness.epoch != self.storage.epoch:
            raise ProtocolError(Code.WITNESS_MISMATCH)

    def _new_id(self):
        # Random opaque IDs; uniqueness checked only for this live simulation.
        for _ in range(10):
            candidate = 'op_' + uuid.uuid4().hex
            if candidate not in self._issued:
                self._issued.add(candidate)
                return candidate
        raise ProtocolError(Code.INTERNAL_ERROR)

    def handle(self, request):
        command = None
        try:
            if type(request) is not dict or type(request.get('command')) is not str:
                raise ProtocolError(Code.INVALID_REQUEST)
            command = request['command']
            if command in {'ingest', 'retrieve', 'mutate_source', 'write_index',
                           'commit_recovery', 'authorize', 'artifact_read', 'execute_code'}:
                raise ProtocolError(Code.UNSUPPORTED)
            schemas = {'status': {'command'}, 'begin': {'command', 'source_id', 'expected_generation'},
                       'finish': {'command', 'operation_id', 'generation'},
                       'interrupt': {'command', 'operation_id', 'generation'}}
            if command not in schemas:
                raise ProtocolError(Code.UNKNOWN_COMMAND)
            if set(request) != schemas[command]:
                raise ProtocolError(Code.INVALID_REQUEST)
            if command == 'status':
                result = Response(Code.OK)
            elif command == 'begin':
                result = self._begin(request)
            else:
                result = self._finish(request, interrupted=command == 'interrupt')
        except ProtocolError as exc:
            result = Response(exc.code)
        except Exception:
            # Never include client input or exception details in the response.
            result = Response(Code.INTERNAL_ERROR)
        self.audit.append(AuditEvent(command if command in ('status', 'begin', 'finish', 'interrupt') else 'rejected', result.code))
        return result

    def _begin(self, request):
        sid, generation = request['source_id'], request['expected_generation']
        if (not isinstance(sid, str) or not re.fullmatch(r'src_[0-9a-f]{32}', sid)
                or type(generation) is not int or generation < 1):
            raise ProtocolError(Code.INVALID_REQUEST)
        if self._active is not None:
            raise ProtocolError(Code.BUSY)
        self._witness_check()
        row = self.storage.record
        if sid != row.source.source_id:
            raise ProtocolError(Code.NOT_FOUND)
        if row.revoked:
            raise ProtocolError(Code.REVOKED)
        if generation != row.generation:
            raise ProtocolError(Code.STALE)
        operation = self._new_id()
        try:
            for rank, key in ((0, ''), (1, ''), (2, sid), (3, '')):
                self.locks.acquire(operation, rank, key)
            # Simulated short conditional transaction ends here.
            self.locks.stack.pop()
            row.state = 'RECONCILING'
            self._active = Fence(operation, generation)
        except Exception:
            if self.locks.owner == operation:
                self.locks.release_all(operation)
            raise
        return Response(Code.OK, operation, generation, 'SIMULATED_RECONCILING')

    def _finish(self, request, interrupted):
        op, generation = request['operation_id'], request['generation']
        if (not isinstance(op, str) or not re.fullmatch(r'op_[0-9a-f]{32}', op)
                or type(generation) is not int or generation < 1):
            raise ProtocolError(Code.INVALID_REQUEST)
        fence = self._active
        if fence is None or fence.operation_id != op or fence.generation != generation or self.locks.owner != op:
            raise ProtocolError(Code.STALE)
        row = self.storage.record
        try:
            self.locks.acquire(op, 3)
            if row.revoked:
                raise ProtocolError(Code.REVOKED)
            if row.generation != generation or row.state != 'RECONCILING':
                raise ProtocolError(Code.STALE)
            self._witness_check()
            # No verified state: even a simulated completion is unverified.
            row.state = 'RESTART_UNVERIFIED'
            return Response(Code.OK, state='SIMULATED_INTERRUPTED' if interrupted else 'SIMULATED_COMPLETE')
        finally:
            if not row.revoked and row.state == 'RECONCILING':
                row.state = 'RESTART_UNVERIFIED'
            self._active = None
            self.locks.release_all(op)

    def crash_for_test(self):
        """Discard volatile simulated state; not real process recovery."""
        if self._active is not None:
            op = self._active.operation_id
            if not self.storage.record.revoked:
                self.storage.record.state = 'RESTART_UNVERIFIED'
            self._active = None
            self.locks.release_all(op)
