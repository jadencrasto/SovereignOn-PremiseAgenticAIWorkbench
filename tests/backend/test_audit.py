"""
tests/backend/test_audit.py
---------------------------
Phase 7 tests for Centralized Audit Logging System.

Tests:
- Event persistence and indexed query filtering.
- Automatic metadata sanitization and redaction of sensitive keys.
- String truncation of large metadata values.
- Aggregate summary statistics.
- Retention pruning by age and max row volume.
"""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from backend.audit.logger import AuditLogger, sanitize_metadata


class TestMetadataSanitization:
    """Test redaction of sensitive keys and truncation of oversized values."""

    def test_redact_sensitive_keys(self):
        raw = {
            "username": "alice",
            "password": "SuperSecretPassword123!",
            "token": "raw_token_xyz",
            "api_key": "ollama_key_abc",
            "authorization": "Bearer token123",
            "secret": "my_secret",
            "image_base64": "iVBORw0KGgoAAAANSUhEUg...",
            "content": "Full document content here",
            "body": "Full request body here",
        }
        sanitized = sanitize_metadata(raw)
        assert sanitized["username"] == "alice"
        assert sanitized["password"] == "[REDACTED]"
        assert sanitized["token"] == "[REDACTED]"
        assert sanitized["api_key"] == "[REDACTED]"
        assert sanitized["authorization"] == "[REDACTED]"
        assert sanitized["secret"] == "[REDACTED]"
        assert sanitized["image_base64"] == "[REDACTED]"
        assert sanitized["content"] == "[REDACTED]"
        assert sanitized["body"] == "[REDACTED]"

    def test_truncate_long_strings(self):
        long_str = "A" * 1000
        raw = {"summary": long_str}
        sanitized = sanitize_metadata(raw, max_len=100)
        assert len(sanitized["summary"]) < 200
        assert "truncated" in sanitized["summary"]

    def test_nested_redaction(self):
        raw = {
            "outer": {
                "inner_pass": "pass123",
                "normal": "value",
            },
            "list_items": [
                {"token": "token_abc", "info": "item1"},
            ],
        }
        sanitized = sanitize_metadata(raw)
        assert sanitized["outer"]["inner_pass"] == "[REDACTED]"
        assert sanitized["outer"]["normal"] == "value"
        assert sanitized["list_items"][0]["token"] == "[REDACTED]"
        assert sanitized["list_items"][0]["info"] == "item1"


class TestAuditLogger:
    """Test SQLite AuditLogger operations."""

    @pytest.fixture
    def logger(self, tmp_path: Path):
        db_file = tmp_path / "test_audit.db"
        return AuditLogger(db_path=db_file, retention_days=30, max_rows=100)

    def test_log_and_query_event(self, logger):
        event_id = logger.log(
            event_type="auth.login_success",
            user_id="user_1",
            role="operator",
            action="login",
            resource="user:alice",
            success=True,
            metadata={"username": "alice", "password": "should_be_redacted"},
        )
        assert event_id.startswith("audit_")

        res = logger.query_events(event_type="auth.login_success")
        assert res["total"] == 1
        event = res["events"][0]
        assert event["event_id"] == event_id
        assert event["user_id"] == "user_1"
        assert event["metadata"]["username"] == "alice"
        assert event["metadata"]["password"] == "[REDACTED]"

    def test_query_filters(self, logger):
        logger.log(event_type="tool.execution", tool="calculator", success=True, task_id="t1")
        logger.log(event_type="tool.execution", tool="file_write", success=False, task_id="t1", failure_reason="Forbidden")
        logger.log(event_type="auth.login_failed", success=False, user_id="u2")

        # Filter by tool
        calc_events = logger.query_events(tool="calculator")
        assert calc_events["total"] == 1

        # Filter by success = False
        failed_events = logger.query_events(success=False)
        assert failed_events["total"] == 2

        # Filter by task_id
        task_events = logger.query_events(task_id="t1")
        assert task_events["total"] == 2

    def test_summary_metrics(self, logger):
        logger.log(event_type="tool.execution", tool="calculator", success=True)
        logger.log(event_type="tool.execution", tool="file_write", success=False, failure_reason="Permission denied")
        logger.log(event_type="auth.login_failed", success=False)

        summary = logger.get_summary()
        assert summary["total_events"] == 3
        assert summary["failed_events"] == 2
        assert summary["denied_actions"] == 1
        assert summary["tool_executions"] == 2
        assert summary["auth_failures"] == 1

    def test_retention_pruning(self, logger, tmp_path):
        # Insert old event
        logger.log(event_type="old_event")
        # Artificially age the event beyond 30 days
        with logger._lock:
            conn = logger._connect()
            old_iso = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
            conn.execute("UPDATE audit_events SET timestamp = ?", (old_iso,))
            conn.commit()
            conn.close()

        # Insert fresh event
        logger.log(event_type="fresh_event")

        deleted = logger.prune_retention()
        assert deleted == 1

        res = logger.query_events()
        assert res["total"] == 1
        assert res["events"][0]["event_type"] == "fresh_event"
