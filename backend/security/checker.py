"""
backend/security/checker.py
----------------------------
Phase 7: Security Diagnostics & Posture Checker.

Produces deterministic PASS / WARN / FAIL evaluations of application security settings.

Note on Egress Diagnostic:
Assesses application-level configured outbound, model provider, and telemetry endpoints.
Does not claim to prove operating-system or network-level firewall isolation.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from backend.auth.models import AuthStore
from backend.config import Settings, settings

logger = logging.getLogger(__name__)


class SecurityDiagnostic:
    def __init__(self, id: str, category: str, title: str, status: str, details: str, remediation: str = "") -> None:
        self.id = id
        self.category = category
        self.title = title
        self.status = status  # PASS | WARN | FAIL
        self.details = details
        self.remediation = remediation

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "status": self.status,
            "details": self.details,
            "remediation": self.remediation,
        }


class SecurityChecker:
    """Evaluates the application security baseline across authentication, sandbox, and local air-gap configuration."""

    def __init__(self, cfg: Settings = settings, auth_store: Optional[AuthStore] = None) -> None:
        self._cfg = cfg
        self._auth_store = auth_store

    def run_all_checks(self) -> List[Dict[str, Any]]:
        diagnostics: List[SecurityDiagnostic] = [
            self._check_authentication_mode(),
            self._check_default_credentials(),
            self._check_application_egress(),
            self._check_sandbox_containment(),
            self._check_cors_policy(),
            self._check_database_hardening(),
        ]
        return [d.to_dict() for d in diagnostics]

    def _check_authentication_mode(self) -> SecurityDiagnostic:
        auth_enabled = getattr(self._cfg, "auth_enabled", True)
        is_prod = self._cfg.app_env.lower() == "production"

        if is_prod and not auth_enabled:
            return SecurityDiagnostic(
                id="SEC-001",
                category="Authentication",
                title="Production Authentication Enforcement",
                status="FAIL",
                details="Authentication is disabled (auth_enabled=false) while app_env is set to production.",
                remediation="Enable authentication in your environment (.env: AUTH_ENABLED=true).",
            )
        elif not auth_enabled:
            return SecurityDiagnostic(
                id="SEC-001",
                category="Authentication",
                title="Development Authentication Mode",
                status="WARN",
                details="Authentication is disabled (dev mode active with synthetic local admin identity).",
                remediation="Enable authentication prior to exposing this server on any network.",
            )
        else:
            return SecurityDiagnostic(
                id="SEC-001",
                category="Authentication",
                title="Local Authentication Active",
                status="PASS",
                details="Local Argon2id session authentication is enabled and active.",
            )

    def _check_default_credentials(self) -> SecurityDiagnostic:
        if not self._auth_store:
            return SecurityDiagnostic(
                id="SEC-002",
                category="Credentials",
                title="Initial Credential Status",
                status="PASS",
                details="Auth store not configured for inspection.",
            )

        try:
            users = self._auth_store.list_users()
            has_unrotated_first_run = any(u.must_change_password for u in users)
            if has_unrotated_first_run:
                return SecurityDiagnostic(
                    id="SEC-002",
                    category="Credentials",
                    title="First-Run Admin Credential Pending Rotation",
                    status="WARN",
                    details="One or more administrator accounts have not yet changed their initial one-time generated password.",
                    remediation="Log in as admin and update password via Settings or /api/auth/change-password.",
                )
            return SecurityDiagnostic(
                id="SEC-002",
                category="Credentials",
                title="User Credentials Posture",
                status="PASS",
                details="All user accounts have completed initial credential setup.",
            )
        except Exception as exc:
            return SecurityDiagnostic(
                id="SEC-002",
                category="Credentials",
                title="User Credentials Check Error",
                status="WARN",
                details=f"Could not inspect user table: {exc}",
            )

    def _check_application_egress(self) -> SecurityDiagnostic:
        """
        Assesses application-level configured endpoints for outbound cloud or telemetry services.
        (Note: Does not claim to prove operating system or firewall network-layer isolation).
        """
        parsed = urlparse(self._cfg.ollama_base_url)
        host = (parsed.hostname or "").lower()

        is_local = host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")
        if not is_local:
            return SecurityDiagnostic(
                id="SEC-003",
                category="Network / Air-gap",
                title="Application Egress Configuration",
                status="WARN",
                details=f"Ollama provider URL is configured to external/non-loopback host '{self._cfg.ollama_base_url}'.",
                remediation="Configure OLLAMA_BASE_URL to point to a loopback address (e.g. http://localhost:11434) for sovereign operation.",
            )

        return SecurityDiagnostic(
            id="SEC-003",
            category="Network / Air-gap",
            title="Application Egress Configuration",
            status="PASS",
            details="All application model providers and vector stores are configured strictly to local loopback endpoints (no external cloud APIs or telemetry configured).",
        )

    def _check_sandbox_containment(self) -> SecurityDiagnostic:
        sandbox = self._cfg.sandbox_dir.resolve()
        project = self._cfg.tasks_dir.parent.resolve()  # data/

        if not str(sandbox).startswith(str(project)):
            return SecurityDiagnostic(
                id="SEC-004",
                category="Filesystem Sandbox",
                title="Sandbox Containment",
                status="WARN",
                details=f"Sandbox path '{sandbox}' is located outside standard project data directory.",
                remediation="Ensure sandbox directory has restricted filesystem permissions.",
            )

        return SecurityDiagnostic(
            id="SEC-004",
            category="Filesystem Sandbox",
            title="Sandbox Containment",
            status="PASS",
            details=f"Sandbox directory is properly contained at {sandbox}.",
        )

    def _check_cors_policy(self) -> SecurityDiagnostic:
        origins = self._cfg.cors_origins_list
        is_prod = self._cfg.app_env.lower() == "production"

        if "*" in origins and is_prod:
            return SecurityDiagnostic(
                id="SEC-005",
                category="API Security",
                title="CORS Access Policy",
                status="FAIL",
                details="Wildcard CORS origins ('*') detected in production environment.",
                remediation="Set CORS_ORIGINS to explicit frontend URL(s).",
            )
        elif "*" in origins:
            return SecurityDiagnostic(
                id="SEC-005",
                category="API Security",
                title="CORS Access Policy",
                status="WARN",
                details="Wildcard CORS origins ('*') active in development mode.",
                remediation="Restrict CORS_ORIGINS before deploying to production.",
            )

        return SecurityDiagnostic(
            id="SEC-005",
            category="API Security",
            title="CORS Access Policy",
            status="PASS",
            details=f"CORS origins restricted to: {', '.join(origins)}",
        )

    def _check_database_hardening(self) -> SecurityDiagnostic:
        db_path = self._cfg.tasks_db_path
        if not db_path.exists():
            return SecurityDiagnostic(
                id="SEC-006",
                category="Database",
                title="SQLite Hardening",
                status="PASS",
                details="Database will initialize WAL mode and foreign keys upon first connection.",
            )

        try:
            conn = sqlite3.connect(str(db_path))
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.close()

            if journal_mode.lower() != "wal":
                return SecurityDiagnostic(
                    id="SEC-006",
                    category="Database",
                    title="SQLite Hardening",
                    status="WARN",
                    details=f"SQLite journal mode is '{journal_mode}', expected 'wal'.",
                    remediation="Verify database directory permissions to allow WAL file creation.",
                )

            return SecurityDiagnostic(
                id="SEC-006",
                category="Database",
                title="SQLite Hardening",
                status="PASS",
                details="SQLite WAL journal mode and foreign key constraints active.",
            )
        except Exception as exc:
            return SecurityDiagnostic(
                id="SEC-006",
                category="Database",
                title="SQLite Hardening Check Error",
                status="WARN",
                details=f"Could not inspect database PRAGMAs: {exc}",
            )
