"""
tests/backend/test_tasks_api.py
--------------------------------
Phase 6 integration tests for the /api/tasks REST endpoints.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


class TestTasksAPI:
    """Test /api/tasks REST endpoints."""

    def test_list_tasks_empty(self, client):
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert "total" in data

    def test_get_nonexistent_task_returns_404(self, client):
        resp = client.get("/api/tasks/task_nonexistent_999")
        assert resp.status_code == 404

    def test_task_creation_and_retrieval(self, client):
        # Create a task directly via task manager in app state
        task_manager = client.app.state.task_manager
        task = task_manager.create_task(
            session_id="test_session_123",
            user_request="Perform analysis and create summary",
        )

        resp = client.get(f"/api/tasks/{task.task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task.task_id
        assert data["session_id"] == "test_session_123"
        assert data["status"] == "pending"

    def test_cancel_task_endpoint(self, client):
        task_manager = client.app.state.task_manager
        task = task_manager.create_task(
            session_id="test_session_cancel",
            user_request="Long running task",
        )

        resp = client.post(f"/api/tasks/{task.task_id}/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"

    def test_approvals_endpoint(self, client):
        task_manager = client.app.state.task_manager
        approval_manager = client.app.state.approval_manager

        task = task_manager.create_task(
            session_id="test_session_appr",
            user_request="Task with approval",
        )

        appr = approval_manager.request_approval(
            task_id=task.task_id,
            step_id="step_1",
            tool_name="file_write",
            arguments={"filename": "res.txt", "content": "data"},
            risk_level="high",
            reason="Save report",
        )

        resp = client.get(f"/api/tasks/{task.task_id}/approvals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["approvals"][0]["approval_id"] == appr.approval_id
