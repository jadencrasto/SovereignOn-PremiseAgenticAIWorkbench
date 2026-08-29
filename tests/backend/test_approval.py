"""
tests/backend/test_approval.py
-------------------------------
Phase 6 tests for ApprovalManager, cryptographic hash binding, and execution verification.
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from backend.agent.approval import ApprovalManager, compute_arguments_hash
from backend.agent.task_store import TaskStore


class TestApprovalManager:
    """Test human approval lifecycle and cryptographic hash binding."""

    @pytest.fixture
    def manager_and_store(self, tmp_path: Path):
        db_file = tmp_path / "test_tasks.db"
        store = TaskStore(db_path=db_file)
        store.save_task({
            "task_id": "task_1",
            "session_id": "sess_1",
            "user_request": "Write output",
            "status": "awaiting_approval",
        })
        mgr = ApprovalManager(store=store, timeout_seconds=300)
        return mgr, store

    def test_compute_arguments_hash_deterministic(self):
        hash1 = compute_arguments_hash("t1", "s1", "file_write", {"filename": "out.txt", "content": "data"})
        hash2 = compute_arguments_hash("t1", "s1", "file_write", {"content": "data", "filename": "out.txt"})
        assert hash1 == hash2

    def test_compute_arguments_hash_detects_mutation(self):
        hash1 = compute_arguments_hash("t1", "s1", "file_write", {"filename": "out.txt", "content": "safe"})
        hash2 = compute_arguments_hash("t1", "s1", "file_write", {"filename": "out.txt", "content": "malicious"})
        assert hash1 != hash2

    def test_request_and_approve(self, manager_and_store):
        mgr, _ = manager_and_store
        appr = mgr.request_approval(
            task_id="task_1",
            step_id="step_1",
            tool_name="file_write",
            arguments={"filename": "a.txt", "content": "hello"},
            risk_level="high",
            reason="Save report",
        )
        assert appr.status == "pending"
        assert appr.approval_id.startswith("appr_")

        approved = mgr.approve(appr.approval_id)
        assert approved.status == "approved"
        assert approved.resolved_at is not None

    def test_request_and_reject(self, manager_and_store):
        mgr, _ = manager_and_store
        appr = mgr.request_approval(
            task_id="task_1",
            step_id="step_1",
            tool_name="file_write",
            arguments={"filename": "a.txt", "content": "hello"},
        )
        rejected = mgr.reject(appr.approval_id, reason="Denied by admin")
        assert rejected.status == "rejected"
        assert rejected.resolved_at is not None

    def test_verify_approval_for_execution_matches(self, manager_and_store):
        mgr, _ = manager_and_store
        args = {"filename": "a.txt", "content": "hello"}
        appr = mgr.request_approval(
            task_id="task_1",
            step_id="step_1",
            tool_name="file_write",
            arguments=args,
        )
        mgr.approve(appr.approval_id)

        # Verification with exact match should succeed
        assert mgr.verify_approval_for_execution(
            approval_id=appr.approval_id,
            task_id="task_1",
            step_id="step_1",
            tool_name="file_write",
            arguments=args,
        ) is True

    def test_verify_approval_rejects_modified_arguments(self, manager_and_store):
        mgr, _ = manager_and_store
        approved_args = {"filename": "a.txt", "content": "hello"}
        appr = mgr.request_approval(
            task_id="task_1",
            step_id="step_1",
            tool_name="file_write",
            arguments=approved_args,
        )
        mgr.approve(appr.approval_id)

        # Attacker / agent tries to execute with modified arguments
        modified_args = {"filename": "a.txt", "content": "rm -rf /"}
        assert mgr.verify_approval_for_execution(
            approval_id=appr.approval_id,
            task_id="task_1",
            step_id="step_1",
            tool_name="file_write",
            arguments=modified_args,
        ) is False

    def test_verify_approval_rejects_task_or_tool_mismatch(self, manager_and_store):
        mgr, _ = manager_and_store
        args = {"filename": "a.txt"}
        appr = mgr.request_approval(
            task_id="task_1",
            step_id="step_1",
            tool_name="file_write",
            arguments=args,
        )
        mgr.approve(appr.approval_id)

        # Wrong tool name
        assert mgr.verify_approval_for_execution(
            approval_id=appr.approval_id,
            task_id="task_1",
            step_id="step_1",
            tool_name="other_tool",
            arguments=args,
        ) is False

        # Wrong task ID
        assert mgr.verify_approval_for_execution(
            approval_id=appr.approval_id,
            task_id="wrong_task",
            step_id="step_1",
            tool_name="file_write",
            arguments=args,
        ) is False
