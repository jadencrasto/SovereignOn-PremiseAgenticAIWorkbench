"""Pure policy primitives for a future verified arithmetic task.

This module does not authenticate callers, issue approvals, persist tasks, or
grant provenance. A digest is an integrity comparison aid, not evidence of
trust: any party able to modify both a record and its digest can forge it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

POLICY_VERSION = "independent-integer-addition-v1"
MAX_ABS_OPERAND = 1_000_000_000


@dataclass(frozen=True)
class AdditionRequest:
    """A deliberately small, deterministic and non-I/O operation."""

    left: int
    right: int

    def __post_init__(self) -> None:
        for value in (self.left, self.right):
            # bool is an int subclass: accepting it would weaken the schema.
            if type(value) is not int:
                raise TypeError("operands must be integers")
            if abs(value) > MAX_ABS_OPERAND:
                raise ValueError("operand outside permitted range")

    def execute(self) -> int:
        """Compute without external side effects or access to other state."""
        return self.left + self.right

    def canonical_record(self) -> dict:
        return {
            "policy_version": POLICY_VERSION,
            "operation": "integer_add",
            "arguments": {"left": self.left, "right": self.right},
        }

    def digest(self) -> str:
        encoded = json.dumps(
            self.canonical_record(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()
