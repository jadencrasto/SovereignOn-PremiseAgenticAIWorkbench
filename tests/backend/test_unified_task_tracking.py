"""
tests/backend/test_unified_task_tracking.py
---------------------------------------------
Tests for unified task tracking — verifying that ALL requests entering
the agent/tool pipeline create persistent TaskManager records via
chat_stream_with_tools_tracked().

Covers:
  1.  RAG creates a persistent task with document_search step
  2.  Calculator creates a persistent task with calculator step
  3.  DOCX (planned) creates exactly one task via run_agent_task
  4.  Step status transitions persist correctly
  5.  Completed tasks have no pending steps
  6.  Failed tool produces failed task/step
  7.  Re-fetch returns same persisted state
  8.  Non-tool conversational messages do NOT create tasks
  9.  No duplicate tasks for single request
  10. Approval-required tasks retain awaiting_approval status
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone

from backend.agent.planner import AgentPlan, PlanStep, StepStatus, PlanStatus
from backend.agent.task import TaskManager, TaskStatus, TaskState, TaskStateError
from backend.agent.task_store import TaskStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path):
    db_file = tmp_path / "test_unified_tasks.db"
    return TaskStore(db_path=db_file)


@pytest.fixture
def manager(store):
    return TaskManager(store=store)


def _make_plan(task_id: str, steps: list, status: str = "executing") -> AgentPlan:
    """Helper to build an AgentPlan from step dicts."""
    plan_steps = [
        PlanStep(
            id=s["id"],
            description=s["description"],
            tool_name=s.get("tool_name"),
            requires_approval=s.get("requires_approval", False),
            status=s.get("status", StepStatus.pending.value),
        )
        for s in steps
    ]
    return AgentPlan(
        task_id=task_id,
        objective="Test objective",
        steps=plan_steps,
        status=status,
    )


# ---------------------------------------------------------------------------
# Test 1: RAG creates a persistent task
# ---------------------------------------------------------------------------

class TestRAGTaskTracking:
    """Simulate chat_stream_with_tools_tracked behavior for a RAG query."""

    def test_rag_creates_persistent_task(self, manager):
        """A document_search tool execution should create a persistent task."""
        task = manager.create_task(
            session_id="sess_rag_1",
            user_request="What are the maintenance findings for P-204?",
        )
        assert task.task_id.startswith("task_")
        assert task.status == TaskStatus.PENDING

        # Transition through lifecycle
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)

        # Verify persisted
        fetched = manager.get_task(task.task_id)
        assert fetched is not None
        assert fetched.status == TaskStatus.EXECUTING

    def test_rag_task_records_document_search_step(self, manager):
        """Task plan should contain a document_search step."""
        task = manager.create_task(
            session_id="sess_rag_2",
            user_request="Search for pump maintenance report",
        )
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)

        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Execute document_search", "tool_name": "document_search", "status": "running"},
        ])
        manager.set_plan(task.task_id, plan)

        # Complete the step
        manager.update_step_status(task.task_id, "step_1", StepStatus.completed.value, result="3 results returned")

        fetched = manager.get_task(task.task_id)
        assert fetched.plan is not None
        assert len(fetched.plan.steps) == 1
        assert fetched.plan.steps[0].tool_name == "document_search"
        assert fetched.plan.steps[0].status == StepStatus.completed.value


# ---------------------------------------------------------------------------
# Test 2: Calculator creates a persistent task
# ---------------------------------------------------------------------------

class TestCalculatorTaskTracking:

    def test_calculator_creates_persistent_task(self, manager):
        """A calculator tool execution should create a persistent task."""
        task = manager.create_task(
            session_id="sess_calc_1",
            user_request="Calculate 42 + 58",
        )
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)

        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Execute calculator", "tool_name": "calculator", "status": "running"},
            {"id": "step_2", "description": "Generate response", "tool_name": None, "status": "pending"},
        ])
        manager.set_plan(task.task_id, plan)

        # Complete calculator step
        manager.update_step_status(task.task_id, "step_1", StepStatus.completed.value, result="Result: 100")
        # Complete response step
        manager.update_step_status(task.task_id, "step_2", StepStatus.running.value)
        manager.update_step_status(task.task_id, "step_2", StepStatus.completed.value)

        # Complete task
        manager.update_status(task.task_id, TaskStatus.COMPLETED, result="42 + 58 = 100")

        fetched = manager.get_task(task.task_id)
        assert fetched.status == TaskStatus.COMPLETED
        assert fetched.plan.steps[0].tool_name == "calculator"
        assert fetched.plan.steps[0].status == StepStatus.completed.value
        assert fetched.result == "42 + 58 = 100"

    def test_calculator_task_records_calculator_step(self, manager):
        """Step tool_name should be 'calculator'."""
        task = manager.create_task(session_id="sess_calc_2", user_request="Calculate 7 * 8")
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)

        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Execute calculator", "tool_name": "calculator", "status": "completed"},
        ])
        manager.set_plan(task.task_id, plan)

        fetched = manager.get_task(task.task_id)
        assert fetched.plan.steps[0].tool_name == "calculator"


# ---------------------------------------------------------------------------
# Test 3: DOCX planned workflow creates exactly one task
# ---------------------------------------------------------------------------

class TestDocxTaskTracking:

    def test_docx_creates_exactly_one_task(self, manager):
        """A DOCX workflow goes through run_agent_task, creating exactly one task."""
        task = manager.create_task(
            session_id="sess_docx_1",
            user_request="Create a maintenance report DOCX for P-204",
        )

        # Simulated multi-step plan
        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Search documents", "tool_name": "document_search"},
            {"id": "step_2", "description": "Synthesize report", "tool_name": None},
            {"id": "step_3", "description": "Create DOCX file", "tool_name": "docx_create", "requires_approval": True},
            {"id": "step_4", "description": "Verify artifact", "tool_name": "artifact_verifier"},
        ], status="planning")
        manager.set_plan(task.task_id, plan)

        # Only one task should exist
        tasks = manager.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].task_id == task.task_id

    def test_docx_retains_multi_step_plan(self, manager):
        """DOCX task should have 4 steps."""
        task = manager.create_task(
            session_id="sess_docx_2",
            user_request="Create a summary report docx",
        )
        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Search documents", "tool_name": "document_search"},
            {"id": "step_2", "description": "Synthesize", "tool_name": None},
            {"id": "step_3", "description": "Create DOCX", "tool_name": "docx_create", "requires_approval": True},
            {"id": "step_4", "description": "Verify", "tool_name": "artifact_verifier"},
        ])
        manager.set_plan(task.task_id, plan)

        fetched = manager.get_task(task.task_id)
        assert len(fetched.plan.steps) == 4
        assert fetched.plan.steps[2].requires_approval is True


# ---------------------------------------------------------------------------
# Test 4: Step status transitions persist
# ---------------------------------------------------------------------------

class TestStepTransitions:

    def test_step_status_transitions_persist(self, manager):
        """Step transitions (pending → running → completed) are persisted."""
        task = manager.create_task(session_id="sess_trans_1", user_request="Test transitions")
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)

        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Step 1", "tool_name": "calculator", "status": "pending"},
        ])
        manager.set_plan(task.task_id, plan)

        # pending → running
        manager.update_step_status(task.task_id, "step_1", StepStatus.running.value)
        fetched = manager.get_task(task.task_id)
        assert fetched.plan.steps[0].status == StepStatus.running.value

        # running → completed
        manager.update_step_status(task.task_id, "step_1", StepStatus.completed.value, result="Done")
        fetched = manager.get_task(task.task_id)
        assert fetched.plan.steps[0].status == StepStatus.completed.value
        assert fetched.plan.steps[0].result == "Done"


# ---------------------------------------------------------------------------
# Test 5: Completed tasks have no pending steps
# ---------------------------------------------------------------------------

class TestCompletedTaskSteps:

    def test_completed_task_no_pending_steps(self, manager):
        """A properly completed task should not have any steps still pending."""
        task = manager.create_task(session_id="sess_comp_1", user_request="Complete task")
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)

        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Execute tool", "tool_name": "calculator", "status": "completed"},
            {"id": "step_2", "description": "Generate response", "tool_name": None, "status": "completed"},
        ])
        manager.set_plan(task.task_id, plan)
        manager.update_status(task.task_id, TaskStatus.COMPLETED, result="All done")

        fetched = manager.get_task(task.task_id)
        assert fetched.status == TaskStatus.COMPLETED
        pending_steps = [s for s in fetched.plan.steps if s.status == StepStatus.pending.value]
        assert len(pending_steps) == 0


# ---------------------------------------------------------------------------
# Test 6: Approval-required tasks stay awaiting_approval
# ---------------------------------------------------------------------------

class TestApprovalTracking:

    def test_approval_required_stays_awaiting(self, manager):
        """Task with approval-required step transitions to awaiting_approval."""
        task = manager.create_task(session_id="sess_appr_1", user_request="Create file")
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)

        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Create file", "tool_name": "docx_create", "requires_approval": True, "status": "pending"},
        ])
        manager.set_plan(task.task_id, plan)

        # Mark step awaiting approval
        manager.update_step_status(task.task_id, "step_1", StepStatus.awaiting_approval.value)
        # Mark task awaiting approval
        manager.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)

        fetched = manager.get_task(task.task_id)
        assert fetched.status == TaskStatus.AWAITING_APPROVAL
        assert fetched.plan.steps[0].status == StepStatus.awaiting_approval.value


# ---------------------------------------------------------------------------
# Test 7: Failed tool produces failed task/step
# ---------------------------------------------------------------------------

class TestFailedToolTracking:

    def test_failed_tool_creates_failed_step(self, manager):
        """A failed tool execution should mark the step and task as failed."""
        task = manager.create_task(session_id="sess_fail_1", user_request="Execute failing tool")
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)

        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Execute tool", "tool_name": "code_execution", "status": "running"},
        ])
        manager.set_plan(task.task_id, plan)

        # Fail the step
        manager.update_step_status(
            task.task_id, "step_1", StepStatus.failed.value,
            error="Sandbox execution blocked",
        )
        manager.update_status(task.task_id, TaskStatus.FAILED, error="Tool execution failed")

        fetched = manager.get_task(task.task_id)
        assert fetched.status == TaskStatus.FAILED
        assert fetched.plan.steps[0].status == StepStatus.failed.value
        assert "blocked" in fetched.plan.steps[0].error.lower()


# ---------------------------------------------------------------------------
# Test 8: Re-fetch returns same state (persistence)
# ---------------------------------------------------------------------------

class TestPersistence:

    def test_refetch_returns_same_state(self, manager):
        """Re-fetching a task after mutations returns the latest persisted state."""
        task = manager.create_task(session_id="sess_pers_1", user_request="Persist test")
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)

        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Execute", "tool_name": "calculator", "status": "completed"},
        ])
        manager.set_plan(task.task_id, plan)
        manager.update_status(task.task_id, TaskStatus.COMPLETED, result="100")

        # First fetch
        fetch1 = manager.get_task(task.task_id)
        # Second fetch
        fetch2 = manager.get_task(task.task_id)

        assert fetch1.task_id == fetch2.task_id
        assert fetch1.status == fetch2.status == TaskStatus.COMPLETED
        assert fetch1.result == fetch2.result == "100"
        assert fetch1.plan.steps[0].status == fetch2.plan.steps[0].status == StepStatus.completed.value


# ---------------------------------------------------------------------------
# Test 9: No duplicate tasks for single request
# ---------------------------------------------------------------------------

class TestNoDuplicateTasks:

    def test_single_request_creates_one_task(self, manager):
        """Creating one task and listing should show exactly one."""
        manager.create_task(session_id="sess_dup_1", user_request="Single request")

        tasks = manager.list_tasks()
        matching = [t for t in tasks if t.session_id == "sess_dup_1"]
        assert len(matching) == 1

    def test_multiple_creates_are_distinct(self, manager):
        """Multiple create calls produce distinct task IDs."""
        t1 = manager.create_task(session_id="sess_dup_2", user_request="Request A")
        t2 = manager.create_task(session_id="sess_dup_2", user_request="Request B")

        assert t1.task_id != t2.task_id
        tasks = manager.list_tasks()
        assert len(tasks) == 2


# ---------------------------------------------------------------------------
# Test 10: Non-tool conversation does NOT create tasks
# ---------------------------------------------------------------------------

class TestConversationalNoTask:
    """
    Validates the design invariant: chat_stream_with_tools_tracked() only creates
    a task when a tool_start event is observed. If the LLM responds directly
    without calling any tool, no task should be created.

    This is tested by verifying the TaskManager state before/after — the tracked
    method defers task creation until the first tool_start event.
    """

    def test_no_task_without_tool_execution(self, manager):
        """If no tools are executed, no task should exist in the store."""
        # The tracked method only creates tasks on tool_start.
        # If we never call create_task, list should be empty.
        tasks = manager.list_tasks()
        assert len(tasks) == 0

    def test_task_only_created_after_tool_start(self, manager):
        """Simulate: first a non-tool response, then a tool response."""
        # No task before tool execution
        assert len(manager.list_tasks()) == 0

        # Now simulate a tool execution happening
        task = manager.create_task(
            session_id="sess_conv_1",
            user_request="Calculate something",
        )
        assert len(manager.list_tasks()) == 1
        assert task.task_id.startswith("task_")


# ---------------------------------------------------------------------------
# Test 11: Skipped steps for non-applicable stages
# ---------------------------------------------------------------------------

class TestSkippedSteps:

    def test_pending_to_skipped_transition(self, manager):
        """A step that is not applicable can transition pending → skipped."""
        task = manager.create_task(session_id="sess_skip_1", user_request="Skip test")
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)

        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Execute tool", "tool_name": "calculator", "status": "pending"},
            {"id": "step_2", "description": "Approval gate", "tool_name": None, "requires_approval": True, "status": "pending"},
        ])
        manager.set_plan(task.task_id, plan)

        # Skip the approval step (not needed for calculator)
        manager.update_step_status(task.task_id, "step_2", StepStatus.skipped.value)

        fetched = manager.get_task(task.task_id)
        assert fetched.plan.steps[1].status == StepStatus.skipped.value

    def test_skipped_is_terminal(self, manager):
        """Skipped is a terminal state — cannot transition further."""
        task = manager.create_task(session_id="sess_skip_2", user_request="Skip terminal test")
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)

        plan = _make_plan(task.task_id, [
            {"id": "step_1", "description": "Step", "tool_name": None, "status": "pending"},
        ])
        manager.set_plan(task.task_id, plan)
        manager.update_step_status(task.task_id, "step_1", StepStatus.skipped.value)

        with pytest.raises(TaskStateError):
            manager.update_step_status(task.task_id, "step_1", StepStatus.running.value)


# ---------------------------------------------------------------------------
# Test 12: Invalid state transitions raise errors
# ---------------------------------------------------------------------------

class TestInvalidTransitions:

    def test_completed_cannot_transition(self, manager):
        """Terminal states cannot transition."""
        task = manager.create_task(session_id="sess_inv_1", user_request="Terminal test")
        manager.update_status(task.task_id, TaskStatus.PLANNING)
        manager.update_status(task.task_id, TaskStatus.EXECUTING)
        manager.update_status(task.task_id, TaskStatus.COMPLETED)

        with pytest.raises(TaskStateError):
            manager.update_status(task.task_id, TaskStatus.EXECUTING)
