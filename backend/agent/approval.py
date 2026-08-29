"""
backend/agent/approval.py
--------------------------
Phase 6: Approval Manager — human-in-the-loop gate for high-risk operations.

CRITICAL SECURITY PROPERTY:
    An approval is cryptographically bound to the EXACT combination of:
        (task_id, step_id, tool_name, arguments_hash)

    If ANY of these change after approval, execution MUST be rejected.
    Before executing an approved step, the caller MUST:
        1. Reload the persisted task
        2. Verify task/step state
        3. Recompute the arguments hash
        4. Compare with the hash that was approved

Approvals are persisted in SQLite alongside task state so they survive
backend restarts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.agent.task_store import TaskStore

logger = logging.getLogger(__name__)


def compute_arguments_hash(
    task_id: str,
    step_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> str:
    """
    Compute a deterministic hash binding an approval to exact arguments.

    The hash covers: task_id + step_id + tool_name + sorted arguments JSON.
    This ensures that any modification to the arguments after approval
    will produce a different hash and be rejected.
    """
    payload = json.dumps(
        {
            "task_id": task_id,
            "step_id": step_id,
            "tool_name": tool_name,
            "arguments": arguments,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ApprovalRequest(BaseModel):
    """A request for human approval of a high-risk tool operation."""
    approval_id: str = Field(default_factory=lambda: f"appr_{uuid.uuid4().hex[:12]}")
    task_id: str
    step_id: str
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    arguments_hash: str
    risk_level: str = "low"
    reason: str = ""
    status: str = "pending"   # pending | approved | rejected | expired
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    expires_at: str = ""
    resolved_at: Optional[str] = None


class ApprovalManager:
    """
    Manages the lifecycle of approval requests for high-risk tool operations.

    Approvals are persisted in SQLite and survive backend restarts.
    """

    def __init__(
        self,
        store: TaskStore,
        timeout_seconds: int = 300,
    ) -> None:
        self._store = store
        self._timeout_seconds = timeout_seconds

    def request_approval(
        self,
        task_id: str,
        step_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        risk_level: str = "high",
        reason: str = "",
    ) -> ApprovalRequest:
        """
        Create a new approval request and persist it.
        """
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self._timeout_seconds)

        args_hash = compute_arguments_hash(task_id, step_id, tool_name, arguments)

        approval = ApprovalRequest(
            task_id=task_id,
            step_id=step_id,
            tool_name=tool_name,
            arguments=arguments,
            arguments_hash=args_hash,
            risk_level=risk_level,
            reason=reason,
            status="pending",
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )

        self._store.save_approval(approval.model_dump())

        logger.info(
            "approval_requested | approval=%s task=%s step=%s tool=%s risk=%s hash=%s",
            approval.approval_id, task_id, step_id, tool_name, risk_level,
            args_hash[:16],
        )

        self._store.save_event({
            "task_id": task_id,
            "step_id": step_id,
            "event_type": "approval_requested",
            "tool_name": tool_name,
            "risk_level": risk_level,
        })

        return approval

    def approve(self, approval_id: str) -> ApprovalRequest:
        """
        Approve a pending request.

        Raises ValueError if:
            - Approval not found
            - Approval is not in 'pending' state
            - Approval has expired
        """
        approval = self._get_and_validate(approval_id)

        now = datetime.now(timezone.utc)
        approval.status = "approved"
        approval.resolved_at = now.isoformat()

        self._store.update_approval_status(
            approval_id, "approved", resolved_at=approval.resolved_at
        )

        logger.info(
            "approval_granted | approval=%s task=%s step=%s tool=%s",
            approval_id, approval.task_id, approval.step_id, approval.tool_name,
        )

        self._store.save_event({
            "task_id": approval.task_id,
            "step_id": approval.step_id,
            "event_type": "approval_granted",
            "tool_name": approval.tool_name,
            "risk_level": approval.risk_level,
        })

        return approval

    def reject(self, approval_id: str, reason: str = "") -> ApprovalRequest:
        """
        Reject a pending request.
        """
        approval = self._get_and_validate(approval_id)

        now = datetime.now(timezone.utc)
        approval.status = "rejected"
        approval.resolved_at = now.isoformat()

        self._store.update_approval_status(
            approval_id, "rejected", resolved_at=approval.resolved_at
        )

        logger.info(
            "approval_rejected | approval=%s task=%s step=%s tool=%s reason=%s",
            approval_id, approval.task_id, approval.step_id,
            approval.tool_name, reason[:100],
        )

        self._store.save_event({
            "task_id": approval.task_id,
            "step_id": approval.step_id,
            "event_type": "approval_rejected",
            "tool_name": approval.tool_name,
            "risk_level": approval.risk_level,
            "result_summary": reason[:200],
        })

        return approval

    def get_pending_for_task(self, task_id: str) -> Optional[ApprovalRequest]:
        """Get the current pending approval for a task, if any."""
        row = self._store.get_pending_approval_for_task(task_id)
        if row is None:
            return None

        approval = ApprovalRequest(**row)

        # Check expiration
        if self._is_expired(approval):
            self._expire(approval)
            return None

        return approval

    def get_approvals_for_task(self, task_id: str) -> List[ApprovalRequest]:
        """List all approvals (including resolved) for a task."""
        rows = self._store.list_approvals_for_task(task_id)
        return [ApprovalRequest(**r) for r in rows]

    def verify_approval_for_execution(
        self,
        approval_id: str,
        task_id: str,
        step_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> bool:
        """
        Verify that an approved approval matches the exact execution context.

        This MUST be called before executing an approved step.
        Recomputes the arguments hash and compares with the approved hash.

        Returns True only if ALL of the following match:
            - approval exists and is 'approved'
            - approval is not expired
            - task_id matches
            - step_id matches
            - tool_name matches
            - arguments hash matches (recomputed from current arguments)
        """
        row = self._store.get_approval(approval_id)
        if row is None:
            logger.warning("approval_verify_failed | approval=%s reason=not_found", approval_id)
            return False

        approval = ApprovalRequest(**row)

        if approval.status != "approved":
            logger.warning(
                "approval_verify_failed | approval=%s reason=status_%s",
                approval_id, approval.status,
            )
            return False

        if self._is_expired(approval):
            self._expire(approval)
            logger.warning(
                "approval_verify_failed | approval=%s reason=expired",
                approval_id,
            )
            return False

        # Verify binding
        if approval.task_id != task_id:
            logger.warning(
                "approval_verify_failed | approval=%s reason=task_mismatch "
                "expected=%s got=%s",
                approval_id, approval.task_id, task_id,
            )
            return False

        if approval.step_id != step_id:
            logger.warning(
                "approval_verify_failed | approval=%s reason=step_mismatch "
                "expected=%s got=%s",
                approval_id, approval.step_id, step_id,
            )
            return False

        if approval.tool_name != tool_name:
            logger.warning(
                "approval_verify_failed | approval=%s reason=tool_mismatch "
                "expected=%s got=%s",
                approval_id, approval.tool_name, tool_name,
            )
            return False

        # Recompute hash from current arguments and compare
        current_hash = compute_arguments_hash(task_id, step_id, tool_name, arguments)
        if current_hash != approval.arguments_hash:
            logger.warning(
                "approval_verify_failed | approval=%s reason=hash_mismatch "
                "approved=%s current=%s",
                approval_id, approval.arguments_hash[:16], current_hash[:16],
            )
            return False

        logger.info(
            "approval_verified | approval=%s task=%s step=%s tool=%s",
            approval_id, task_id, step_id, tool_name,
        )
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_and_validate(self, approval_id: str) -> ApprovalRequest:
        """Retrieve an approval and validate it can be acted on."""
        row = self._store.get_approval(approval_id)
        if row is None:
            raise ValueError(f"Approval not found: {approval_id}")

        approval = ApprovalRequest(**row)

        if approval.status != "pending":
            raise ValueError(
                f"Approval {approval_id} is already {approval.status}, "
                f"cannot modify"
            )

        if self._is_expired(approval):
            self._expire(approval)
            raise ValueError(f"Approval {approval_id} has expired")

        return approval

    def _is_expired(self, approval: ApprovalRequest) -> bool:
        """Check if an approval has expired."""
        if not approval.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(approval.expires_at)
            return datetime.now(timezone.utc) > expires
        except (ValueError, TypeError):
            return False

    def _expire(self, approval: ApprovalRequest) -> None:
        """Mark an approval as expired."""
        now = datetime.now(timezone.utc).isoformat()
        approval.status = "expired"
        approval.resolved_at = now
        self._store.update_approval_status(
            approval.approval_id, "expired", resolved_at=now
        )
        logger.info(
            "approval_expired | approval=%s task=%s step=%s",
            approval.approval_id, approval.task_id, approval.step_id,
        )
