"""Single-threaded test coordinator, not a cross-process lock or SQLite."""
from .contracts import Code, ProtocolError


class SimulatedLocks:
    """One simulated owner; lock ranks enforce owner → global → source → tx."""
    def __init__(self):
        self.owner = None
        self.stack = []

    def acquire(self, owner, rank, key=''):
        if not isinstance(owner, str) or not owner or rank not in (0, 1, 2, 3) or not isinstance(key, str):
            raise ProtocolError(Code.INVALID_REQUEST)
        if self.owner is not None and self.owner != owner:
            raise ProtocolError(Code.BUSY)
        if self.owner is None and (rank != 0 or key):
            raise ProtocolError(Code.LOCK_ORDER)
        if self.stack:
            last_rank, last_key = self.stack[-1]
            if rank < last_rank or (rank == last_rank and (rank != 2 or key <= last_key)):
                raise ProtocolError(Code.LOCK_ORDER)
            if rank > last_rank + 1 or (rank in (0, 1, 3) and key) or (rank == 2 and not key):
                raise ProtocolError(Code.LOCK_ORDER)
        self.owner = owner
        self.stack.append((rank, key))

    def release_all(self, owner):
        if self.owner != owner:
            raise ProtocolError(Code.STALE)
        self.stack.clear()
        self.owner = None
