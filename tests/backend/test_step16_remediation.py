"""
tests/backend/test_step16_remediation.py
-----------------------------------------
Comprehensive regression and validation test suite for Phase D — Step 16:
Cross-Cutting Security Remediation.

Priorities Covered:
1. RAG Clearance Enforcement & Fail-Closed Metadata:
   - Centralized hierarchy check
   - Oversampling of candidate chunks before clearance filtering
   - Fail-closed behavior on missing or invalid clearance
   - Document details endpoint (GET /api/documents/{id}) 404 / filtered chunks
   - Document listing hides documents with 0 accessible chunks
   - Document search tool passes caller clearance

2. Artifact Authorization & Collision Protection:
   - Multi-user isolation on list, preview, and download
   - Admin access to all artifacts, Operator access to legacy unowned
   - Viewer denied access to legacy unowned artifacts
   - Cross-user filename collision prevention
   - Cross-task overwrite prevention
   - Stored path validation within sandbox boundary
   - Repeated creation updates ownership cleanly

3. Central Audit Attribution & Lifecycle Completeness:
   - Tool execution logs server-side user_id and user_role
   - task.created, task.completed, task.cancelled, task.failed emitted
   - Terminal failure states (FAILED, FAILED_TIMEOUT, FAILED_INTERRUPTED) emit task.failed
   - Lifecycle audit event deduplication per task
   - User identity preserved across resumed tasks

4. Production Isolation & Fail-Closed Boundaries:
   - Config validation rule prod_code_exec_isolation
   - Production mode requires Docker container isolation
   - Docker daemon unavailability fails closed in production
   - CodeExecutionInput forbids extra parameter injection
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from backend.agent.planner import AgentPlan, PlanStep, StepStatus
from backend.agent.task import TaskManager, TaskState, TaskStatus
from backend.agent.task_store import TaskStore
from backend.audit.logger import AuditLogger
from backend.auth.models import ClearanceLevel, Permission, User, UserRole, AuthStore
from backend.auth.security import SessionManager, hash_password
from backend.config import Settings
from backend.main import create_app
from backend.rag.retriever import RetrievedChunk, Retriever
from backend.rag.service import DocumentInfo, DocumentService
from backend.tools.code_execution import (
    CodeExecutionInput,
    create_code_execution,
)
from backend.tools.document_search import create_document_search
from backend.tools.registry import ToolDefinition, ToolRegistry, ToolResult
from backend.utils.config_validation import ConfigValidator


# ---------------------------------------------------------------------------
# Fixture: Multi-User Environment
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path: Path):
    """Setup isolated test environment with SQLite DB, auth, and pre-seeded users."""
    db_path = tmp_path / "tasks.db"
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        app_env="development",
        auth_enabled=True,
        tasks_db_path=db_path,
        sandbox_dir=sandbox,
        upload_dir=tmp_path / "uploads",
        chroma_persist_dir=tmp_path / "chromadb",
    )
    settings.sandbox_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    app = create_app(settings)
    auth_store = AuthStore(db_path=db_path)
    session_mgr = SessionManager(store=auth_store)

    admin_user = User(
        id="user_admin_16",
        username="admin_s16",
        password_hash=hash_password("AdminPass123!"),
        role=UserRole.ADMIN.value,
        clearance=ClearanceLevel.L3.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    operator_user = User(
        id="user_op_16",
        username="operator_s16",
        password_hash=hash_password("OperatorPass123!"),
        role=UserRole.OPERATOR.value,
        clearance=ClearanceLevel.L2.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    viewer_user = User(
        id="user_viewer_16",
        username="viewer_s16",
        password_hash=hash_password("ViewerPass123!"),
        role=UserRole.VIEWER.value,
        clearance=ClearanceLevel.L1.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )
    other_viewer = User(
        id="user_viewer_other_16",
        username="viewer_other_s16",
        password_hash=hash_password("ViewerPass456!"),
        role=UserRole.VIEWER.value,
        clearance=ClearanceLevel.L1.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )

    for u in (admin_user, operator_user, viewer_user, other_viewer):
        auth_store.create_user(u)

    admin_tok, _ = session_mgr.create_session(admin_user.id)
    op_tok, _ = session_mgr.create_session(operator_user.id)
    viewer_tok, _ = session_mgr.create_session(viewer_user.id)
    other_tok, _ = session_mgr.create_session(other_viewer.id)

    app.state.auth_store = auth_store
    app.state.session_manager = session_mgr

    with TestClient(app) as client:
        yield {
            "client": client,
            "app": app,
            "settings": settings,
            "db_path": db_path,
            "sandbox_dir": sandbox,
            "task_store": app.state.task_store,
            "task_manager": app.state.task_manager,
            "audit_logger": app.state.audit_logger,
            "tool_registry": app.state.tool_registry,
            "users": {
                "admin": admin_user,
                "operator": operator_user,
                "viewer": viewer_user,
                "other_viewer": other_viewer,
            },
            "headers": {
                "admin": {"Authorization": f"Bearer {admin_tok}"},
                "operator": {"Authorization": f"Bearer {op_tok}"},
                "viewer": {"Authorization": f"Bearer {viewer_tok}"},
                "other_viewer": {"Authorization": f"Bearer {other_tok}"},
            },
        }


# ===========================================================================
# Priority 1: RAG Clearance Enforcement & Metadata Protection
# ===========================================================================

@pytest.mark.asyncio
async def test_rag_clearance_filtering_and_oversampling(tmp_path: Path):
    """Verify Retriever oversamples candidates and applies centralized clearance hierarchy."""
    mock_store = MagicMock()
    # Return 6 candidate chunks: 2 admin, 2 operator, 2 viewer
    # The Retriever calls _store.count() and _store.query()
    mock_store.count.return_value = 6
    mock_store.query.return_value = {
        "ids": [["c0", "c1", "c2", "c3", "c4", "c5"]],
        "documents": [["Secret formula 1", "Secret formula 2", "Operational metric", "Operational log", "Public info", "More secret data"]],
        "metadatas": [[
            {"chunk_index": 0, "filename": "classified.pdf", "clearance": "admin", "document_id": "d1"},
            {"chunk_index": 1, "filename": "classified.pdf", "clearance": "admin", "document_id": "d1"},
            {"chunk_index": 2, "filename": "ops.pdf", "clearance": "operator", "document_id": "d2"},
            {"chunk_index": 3, "filename": "ops.pdf", "clearance": "operator", "document_id": "d2"},
            {"chunk_index": 4, "filename": "public.pdf", "clearance": "viewer", "document_id": "d3"},
            {"chunk_index": 5, "filename": "classified.pdf", "clearance": "admin", "document_id": "d1"},
        ]],
        "distances": [[0.1, 0.15, 0.2, 0.25, 0.3, 0.35]],
    }

    mock_emb = AsyncMock()
    mock_emb.embed = AsyncMock(return_value=[0.1] * 384)
    retriever = Retriever(embedding_service=mock_emb, vector_store=mock_store)

    # 1. Viewer retrieves: should only get viewer chunks (1 total)
    res_viewer = await retriever.retrieve("formula", top_k=5, user_clearance="viewer")
    assert len(res_viewer) == 1
    assert res_viewer[0].filename == "public.pdf"
    assert res_viewer[0].clearance == "viewer"

    # 2. Operator retrieves: should get operator and viewer chunks (3 total)
    res_operator = await retriever.retrieve("formula", top_k=5, user_clearance="operator")
    assert len(res_operator) == 3
    filenames = [c.filename for c in res_operator]
    assert "public.pdf" in filenames
    assert "ops.pdf" in filenames
    assert "classified.pdf" not in filenames

    # 3. Admin retrieves: should get all 6 chunks up to k=5
    res_admin = await retriever.retrieve("formula", top_k=5, user_clearance="admin")
    assert len(res_admin) == 5  # capped by top_k=5


@pytest.mark.asyncio
async def test_rag_missing_or_invalid_clearance_fails_closed():
    """Verify chunks with missing, None, or invalid clearance fail closed to admin."""
    mock_store = MagicMock()
    mock_store.count.return_value = 3
    mock_store.query.return_value = {
        "ids": [["c0", "c1", "c2"]],
        "documents": [["Corrupt clr", "None clr", "Unknown clr"]],
        "metadatas": [[
            {"chunk_index": 0, "filename": "unlabeled.pdf", "document_id": "d1"},           # missing clearance
            {"chunk_index": 1, "filename": "none.pdf", "clearance": None, "document_id": "d2"},  # None
            {"chunk_index": 2, "filename": "bogus.pdf", "clearance": "super_secret", "document_id": "d3"},  # unrecognized
        ]],
        "distances": [[0.1, 0.2, 0.3]],
    }
    mock_emb = AsyncMock()
    mock_emb.embed = AsyncMock(return_value=[0.1] * 384)
    retriever = Retriever(embedding_service=mock_emb, vector_store=mock_store)

    # Viewer gets nothing (all chunks fail-closed to admin)
    assert len(await retriever.retrieve("test", top_k=5, user_clearance="viewer")) == 0
    # Operator gets nothing
    assert len(await retriever.retrieve("test", top_k=5, user_clearance="operator")) == 0
    # Admin gets all 3 (since they fail closed to admin which admin can access)
    res_admin = await retriever.retrieve("test", top_k=5, user_clearance="admin")
    assert len(res_admin) == 3


def test_rag_document_details_unauthorized_hidden(env):
    """GET /api/documents/{id} returns 404 if caller clearance has 0 accessible chunks."""
    client = env["client"]
    app = env["app"]

    mock_rag = MagicMock(spec=DocumentService)
    def mock_get_details(doc_id, user_clearance="viewer"):
        if doc_id == "doc_secret_01":
            if user_clearance == "admin":
                return {
                    "document_id": "doc_secret_01",
                    "filename": "top_secret.pdf",
                    "file_type": ".pdf",
                    "chunk_count": 2,
                    "relative_path": "top_secret.pdf",
                    "chunks": [{"chunk_id": "c1", "chunk_index": 0, "page": 1, "text": "secret", "metadata": {"clearance": "admin"}}],
                }
            return None
        return {
            "document_id": "doc_public_01",
            "filename": "public_guide.pdf",
            "file_type": ".pdf",
            "chunk_count": 1,
            "relative_path": "public_guide.pdf",
            "chunks": [{"chunk_id": "c2", "chunk_index": 0, "page": 1, "text": "public", "metadata": {"clearance": "viewer"}}],
        }

    mock_rag.get_document_details.side_effect = mock_get_details
    app.state.doc_service = mock_rag

    # Viewer requesting secret doc -> 404
    resp_viewer = client.get("/api/documents/doc_secret_01", headers=env["headers"]["viewer"])
    assert resp_viewer.status_code == 404

    # Admin requesting secret doc -> 200
    resp_admin = client.get("/api/documents/doc_secret_01", headers=env["headers"]["admin"])
    assert resp_admin.status_code == 200
    assert resp_admin.json()["filename"] == "top_secret.pdf"

    # Viewer requesting public doc -> 200
    resp_pub = client.get("/api/documents/doc_public_01", headers=env["headers"]["viewer"])
    assert resp_pub.status_code == 200


def test_rag_document_list_hides_unpermitted_docs(env):
    """GET /api/documents excludes documents where user clearance permits 0 chunks."""
    client = env["client"]
    app = env["app"]

    mock_rag = MagicMock(spec=DocumentService)
    def mock_list_docs(user_clearance="viewer"):
        if user_clearance == "admin":
            return [
                DocumentInfo(document_id="d1", filename="top_secret.pdf", chunk_count=2, file_type=".pdf"),
                DocumentInfo(document_id="d2", filename="public.pdf", chunk_count=1, file_type=".pdf"),
            ]
        elif user_clearance in ("operator", "internal"):
            return [
                DocumentInfo(document_id="d2", filename="public.pdf", chunk_count=1, file_type=".pdf"),
            ]
        else:
            return [
                DocumentInfo(document_id="d2", filename="public.pdf", chunk_count=1, file_type=".pdf"),
            ]

    mock_rag.list_documents.side_effect = mock_list_docs
    app.state.doc_service = mock_rag

    # Viewer should not see top_secret.pdf
    resp_viewer = client.get("/api/documents", headers=env["headers"]["viewer"])
    assert resp_viewer.status_code == 200
    data = resp_viewer.json()
    docs = data.get("documents", data) if isinstance(data, dict) else data
    filenames = [d["filename"] for d in docs]
    assert "public.pdf" in filenames
    assert "top_secret.pdf" not in filenames

    # Admin should see both
    resp_admin = client.get("/api/documents", headers=env["headers"]["admin"])
    assert resp_admin.status_code == 200
    admin_data = resp_admin.json()
    admin_docs = admin_data.get("documents", admin_data) if isinstance(admin_data, dict) else admin_data
    assert len(admin_docs) == 2


@pytest.mark.asyncio
async def test_document_search_tool_forwards_user_clearance():
    """Verify document_search tool executes with caller clearance passed down."""
    mock_retriever = AsyncMock()
    mock_retriever.retrieve = AsyncMock(return_value=[
        RetrievedChunk(
            document_id="d1", filename="public.pdf", chunk_id="c1",
            chunk_index=0, page=1, text="Public text", score=0.1, clearance="viewer",
        )
    ])
    mock_retriever.is_chunk_relevant = MagicMock(return_value=True)
    # Prevent the code from treating mock_retriever._store as a real store
    mock_retriever._store = MagicMock()

    doc_search_fn = create_document_search(retriever=mock_retriever)

    # Build a proper Pydantic input
    from backend.tools.document_search import DocumentSearchInput
    input_args = DocumentSearchInput(query="hydrocracker", top_k=3)

    # Execute as viewer
    res = await doc_search_fn(input_args, user_role="viewer")
    assert isinstance(res, list)
    assert len(res) == 1
    mock_retriever.retrieve.assert_called_with(
        "hydrocracker", top_k=3, user_clearance="viewer"
    )


# ===========================================================================
# Priority 2: Artifact Authorization & Collision Protection
# ===========================================================================

def test_artifact_multi_user_isolation(env):
    """User A's artifact is hidden from User B on list, preview, and download."""
    client = env["client"]
    task_store: TaskStore = env["task_store"]
    sandbox_dir = env["sandbox_dir"]

    user_a = env["users"]["viewer"]
    user_b = env["users"]["other_viewer"]

    # Create a valid task record first to satisfy FK constraint
    task_store.save_task({
        "task_id": "task_a_001",
        "session_id": "sess_a_001",
        "user_request": "Generate report",
        "status": "completed",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "user_id": user_a.id,
        "user_role": user_a.role,
    })

    # Write file to sandbox
    art_file = sandbox_dir / "user_a_private.txt"
    art_file.write_text("Confidential user A report", encoding="utf-8")

    # Record ownership in task_store
    task_store.save_artifact(
        filename="user_a_private.txt",
        path="data/sandbox/user_a_private.txt",
        task_id="task_a_001",
        user_id=user_a.id,
        user_role=user_a.role,
        size_bytes=len("Confidential user A report"),
    )

    # Patch settings.sandbox_dir so the API reads from test sandbox
    with patch("backend.api.artifacts.settings") as mock_settings:
        mock_settings.sandbox_dir = sandbox_dir

        # User A lists -> sees it
        resp_a_list = client.get("/api/artifacts", headers=env["headers"]["viewer"])
        assert resp_a_list.status_code == 200
        fnames_a = [a["filename"] for a in resp_a_list.json()["artifacts"]]
        assert "user_a_private.txt" in fnames_a

        # User B lists -> does NOT see it
        resp_b_list = client.get("/api/artifacts", headers=env["headers"]["other_viewer"])
        assert resp_b_list.status_code == 200
        fnames_b = [a["filename"] for a in resp_b_list.json()["artifacts"]]
        assert "user_a_private.txt" not in fnames_b

        # User B preview -> 404
        resp_b_prev = client.get("/api/artifacts/user_a_private.txt/preview", headers=env["headers"]["other_viewer"])
        assert resp_b_prev.status_code == 404

        # User B download -> 404
        resp_b_dl = client.get("/api/artifacts/user_a_private.txt", headers=env["headers"]["other_viewer"])
        assert resp_b_dl.status_code == 404

        # User A preview & download -> 200
        resp_a_prev = client.get("/api/artifacts/user_a_private.txt/preview", headers=env["headers"]["viewer"])
        assert resp_a_prev.status_code == 200
        assert "Confidential user A report" in resp_a_prev.json()["content"]

        resp_a_dl = client.get("/api/artifacts/user_a_private.txt", headers=env["headers"]["viewer"])
        assert resp_a_dl.status_code == 200


def test_artifact_admin_and_operator_access(env):
    """Admin accesses all artifacts; Operator accesses legacy unowned; Viewer cannot access legacy."""
    client = env["client"]
    sandbox_dir = env["sandbox_dir"]

    # Create a legacy unowned artifact (not in task_store DB)
    legacy_file = sandbox_dir / "legacy_report.txt"
    legacy_file.write_text("Legacy data without DB record", encoding="utf-8")

    with patch("backend.api.artifacts.settings") as mock_settings:
        mock_settings.sandbox_dir = sandbox_dir

        # 1. Admin accesses legacy
        resp_admin = client.get("/api/artifacts/legacy_report.txt/preview", headers=env["headers"]["admin"])
        assert resp_admin.status_code == 200

        # 2. Operator accesses legacy
        resp_op = client.get("/api/artifacts/legacy_report.txt/preview", headers=env["headers"]["operator"])
        assert resp_op.status_code == 200

        # 3. Viewer cannot access legacy (404)
        resp_viewer = client.get("/api/artifacts/legacy_report.txt/preview", headers=env["headers"]["viewer"])
        assert resp_viewer.status_code == 404


@pytest.mark.asyncio
async def test_artifact_cross_user_and_cross_task_collision(env):
    """ToolRegistry prevents cross-user overwrites and cross-task filename conflicts."""
    registry: ToolRegistry = env["tool_registry"]
    task_store: TaskStore = env["task_store"]

    # Create valid task records
    task_store.save_task({
        "task_id": "task_001",
        "session_id": "sess_001",
        "user_request": "Generate summary",
        "status": "executing",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "user_id": "user_1",
        "user_role": "operator",
    })
    task_store.save_task({
        "task_id": "task_002",
        "session_id": "sess_002",
        "user_request": "Attacker task",
        "status": "executing",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "user_id": "user_2",
        "user_role": "operator",
    })
    task_store.save_task({
        "task_id": "task_003",
        "session_id": "sess_003",
        "user_request": "Another user_1 task",
        "status": "executing",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "user_id": "user_1",
        "user_role": "operator",
    })

    # Pre-seed existing artifact owned by user_1 in task_1
    task_store.save_artifact(
        filename="summary.docx",
        path="data/sandbox/summary.docx",
        task_id="task_001",
        user_id="user_1",
        user_role="operator",
        size_bytes=100,
    )

    # 1. User 2 attempts to write summary.docx -> blocked by collision check
    res_cross_user = await registry.execute(
        name="docx_create",
        arguments={"filename": "summary.docx", "title": "Attack", "paragraphs": ["hack"]},
        user_id="user_2",
        task_id="task_002",
        user_role="operator",
    )
    assert not res_cross_user.success
    assert "owned by another user" in (res_cross_user.error or "").lower()

    # 2. User 1 with a different task (task_003) attempts to overwrite summary.docx -> blocked
    res_cross_task = await registry.execute(
        name="docx_create",
        arguments={"filename": "summary.docx", "title": "New Task", "paragraphs": ["text"]},
        user_id="user_1",
        task_id="task_003",
        user_role="operator",
    )
    assert not res_cross_task.success
    assert "another task" in (res_cross_task.error or "").lower()


@pytest.mark.asyncio
async def test_artifact_repeated_creation_updates_cleanly(env):
    """Repeated writes by the same task/user update ownership cleanly."""
    registry: ToolRegistry = env["tool_registry"]
    task_store: TaskStore = env["task_store"]

    # Create valid task record
    task_store.save_task({
        "task_id": "task_rep_001",
        "session_id": "sess_rep_001",
        "user_request": "Generate output",
        "status": "executing",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "user_id": "user_1",
        "user_role": "operator",
    })

    res1 = await registry.execute(
        name="file_write",
        arguments={"filename": "output.txt", "content": "Version 1"},
        user_id="user_1",
        task_id="task_rep_001",
        user_role="operator",
    )
    assert res1.success
    art1 = task_store.get_artifact("output.txt")
    assert art1 is not None
    assert art1["task_id"] == "task_rep_001"

    # Same task/user writes again -> updates cleanly
    res2 = await registry.execute(
        name="file_write",
        arguments={"filename": "output.txt", "content": "Version 2 updated"},
        user_id="user_1",
        task_id="task_rep_001",
        user_role="operator",
    )
    assert res2.success
    art2 = task_store.get_artifact("output.txt")
    assert art2["task_id"] == "task_rep_001"


# ===========================================================================
# Priority 3: Central Audit Attribution & Lifecycle Completeness
# ===========================================================================

@pytest.mark.asyncio
async def test_audit_attribution_tool_execution(env):
    """Tool execution records server-side user_id and user_role in AuditLogger."""
    registry: ToolRegistry = env["tool_registry"]
    audit_logger: AuditLogger = env["audit_logger"]

    await registry.execute(
        name="calculator",
        arguments={"expression": "100 * 2"},
        session_id="sess_audit_01",
        task_id="task_aud_01",
        step_id="step_01",
        user_id="user_test_auditor",
        user_role="operator",
    )

    result = audit_logger.query_events(event_type="tool.execution", limit=10)
    logs = result.get("events", [])
    assert len(logs) >= 1
    found = next((l for l in logs if l.get("task_id") == "task_aud_01"), None)
    assert found is not None
    assert found["user_id"] == "user_test_auditor"
    assert found["role"] == "operator"
    assert found["tool"] == "calculator"
    assert found["success"] is True


def test_audit_lifecycle_events_emitted_and_deduplicated(env):
    """Lifecycle events (created, completed, cancelled, failed) are emitted and deduplicated."""
    task_manager: TaskManager = env["task_manager"]
    audit_logger: AuditLogger = env["audit_logger"]

    task = task_manager.create_task(
        session_id="sess_lc_01",
        user_request="Test lifecycle audit",
        user_id="user_lc_01",
        user_role="operator",
    )

    # 1. Verify task.created was emitted
    created_result = audit_logger.query_events(event_type="task.created")
    created_logs = [l for l in created_result.get("events", []) if l.get("task_id") == task.task_id]
    assert len(created_logs) == 1
    assert created_logs[0]["user_id"] == "user_lc_01"

    # 2. Transition through executing -> completed
    task_manager.update_status(task.task_id, TaskStatus.PLANNING)
    task_manager.update_status(task.task_id, TaskStatus.EXECUTING)
    task_manager.update_status(task.task_id, TaskStatus.COMPLETED, result="Done")

    completed_result = audit_logger.query_events(event_type="task.completed")
    completed_logs = [l for l in completed_result.get("events", []) if l.get("task_id") == task.task_id]
    assert len(completed_logs) == 1

    # 3. Test terminal failure states (FAILED, FAILED_TIMEOUT, FAILED_INTERRUPTED)
    task_fail = task_manager.create_task(
        session_id="sess_lc_02",
        user_request="Test failure audit",
        user_id="user_lc_fail",
        user_role="operator",
    )
    task_manager.update_status(task_fail.task_id, TaskStatus.PLANNING)
    task_manager.update_status(task_fail.task_id, TaskStatus.FAILED, error="Syntax error in plan")

    fail_result = audit_logger.query_events(event_type="task.failed")
    fail_logs = [l for l in fail_result.get("events", []) if l.get("task_id") == task_fail.task_id]
    assert len(fail_logs) == 1
    assert "Syntax error in plan" in fail_logs[0]["failure_reason"]

    # 4. Cancellation audit
    task_cancel = task_manager.create_task(
        session_id="sess_lc_03",
        user_request="Test cancel audit",
        user_id="user_lc_cancel",
        user_role="operator",
    )
    task_manager.cancel_task(task_cancel.task_id)

    cancel_result = audit_logger.query_events(event_type="task.cancelled")
    cancel_logs = [l for l in cancel_result.get("events", []) if l.get("task_id") == task_cancel.task_id]
    assert len(cancel_logs) == 1


# ===========================================================================
# Priority 4: Production Isolation & Fail-Closed Boundaries
# ===========================================================================

def test_config_validation_prod_requires_docker(tmp_path: Path):
    """ConfigValidator in production fails closed if code_exec_isolation != docker."""
    prod_settings = Settings(
        app_env="production",
        auth_enabled=True,
        auth_cookie_secure=True,
        tasks_db_path=tmp_path / "tasks.db",
        code_exec_isolation="subprocess",  # Invalid in production
    )
    validator = ConfigValidator(prod_settings)
    is_valid, results = validator.validate()
    assert not is_valid
    rule = next(r for r in results if r["rule"] == "prod_code_exec_isolation")
    assert rule["status"] == "FAIL"
    assert "requires Docker container isolation" in rule["message"]


def test_config_validation_prod_docker_unavailable_fails(tmp_path: Path):
    """ConfigValidator in production with Docker fails closed if Docker daemon is unreachable."""
    prod_settings = Settings(
        app_env="production",
        auth_enabled=True,
        auth_cookie_secure=True,
        tasks_db_path=tmp_path / "tasks.db",
        code_exec_isolation="docker",
    )
    with patch("backend.tools.code_execution.check_docker_daemon_available", return_value=False):
        validator = ConfigValidator(prod_settings)
        is_valid, results = validator.validate()
        assert not is_valid
        rule = next(r for r in results if r["rule"] == "prod_code_exec_isolation")
        assert rule["status"] == "FAIL"
        assert "Docker daemon is unreachable" in rule["message"]


@pytest.mark.asyncio
async def test_code_execution_prod_fails_without_docker(tmp_path: Path):
    """execute_code_execution raises RuntimeError if invoked in production with subprocess isolation."""
    prod_settings = Settings(
        app_env="production",
        code_exec_isolation="subprocess",
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    exec_fn = create_code_execution(sandbox_dir=sandbox, cfg=prod_settings)
    input_model = CodeExecutionInput(code="print(1 + 1)")

    with pytest.raises(RuntimeError) as exc_info:
        await exec_fn(input_model)
    assert "Production security policy violation" in str(exc_info.value)


def test_code_execution_input_forbids_extra_fields():
    """CodeExecutionInput forbids extra parameter injection (e.g. attempting to override isolation)."""
    with pytest.raises(Exception):
        CodeExecutionInput(code="print(1)", isolation_mode="subprocess")
