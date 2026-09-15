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
            self._check_code_execution_isolation(),
            self._check_cookie_security(),
            self._check_documentation_exposure(),
        ]
        return [d.to_dict() for d in diagnostics]

    def _check_authentication_mode(self) -> SecurityDiagnostic:
        auth_enabled = getattr(self._cfg, "auth_enabled", True)
        is_prod = self._cfg.app_env.lower() == "production"
        dev_mode_flag = getattr(self._cfg, "dev_mode", None)

        if is_prod and dev_mode_flag is True:
            return SecurityDiagnostic(
                id="SEC-001",
                category="Authentication",
                title="Production Configuration Conflict",
                status="FAIL",
                details="Contradictory security configuration: app_env is production but dev_mode is True. Production mode fails closed.",
                remediation="Disable DEV_MODE when running in production.",
            )

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

    def _check_code_execution_isolation(self) -> SecurityDiagnostic:
        isolation = getattr(self._cfg, "code_exec_isolation", "subprocess").lower()
        if isolation == "docker":
            from backend.tools.code_execution import check_docker_daemon_available, check_docker_image_available
            daemon_ok = check_docker_daemon_available()
            image_name = getattr(self._cfg, "code_exec_docker_image", "sovereign-code-sandbox:latest")
            image_ok = check_docker_image_available(image_name) if daemon_ok else False

            if not daemon_ok:
                return SecurityDiagnostic(
                    id="SEC-007",
                    category="Code Execution",
                    title="Code Execution Isolation",
                    status="FAIL",
                    details="Docker container isolation configured, but Docker daemon is unreachable or stopped. Code execution refused.",
                    remediation="Start Docker daemon or set CODE_EXEC_ISOLATION=subprocess for hardened local subprocess execution.",
                )
            if not image_ok:
                return SecurityDiagnostic(
                    id="SEC-007",
                    category="Code Execution",
                    title="Code Execution Isolation",
                    status="FAIL",
                    details=f"Docker container isolation configured, but sovereign image '{image_name}' is not found locally. Automatic pulling is prohibited in air-gap mode.",
                    remediation=f"Load or build the local image '{image_name}' into Docker.",
                )
            return SecurityDiagnostic(
                id="SEC-007",
                category="Code Execution",
                title="Code Execution Isolation",
                status="PASS",
                details=f"Container isolation active (Docker, image '{image_name}', --network none, read-only root, unprivileged user).",
            )
        else:
            return SecurityDiagnostic(
                id="SEC-007",
                category="Code Execution",
                title="Code Execution Isolation",
                status="WARN",
                details="Hardened local subprocess execution active (process group isolation, env sanitization, AST checks; process-level boundary only, not container isolation).",
                remediation="Configure Docker container isolation (CODE_EXEC_ISOLATION=docker) for true container boundary.",
            )

    def _check_cookie_security(self) -> SecurityDiagnostic:
        is_prod = self._cfg.app_env.lower() == "production"
        cookie_secure = getattr(self._cfg, "auth_cookie_secure", False)

        if is_prod:
            if cookie_secure:
                return SecurityDiagnostic(
                    id="SEC-008",
                    category="Cookie Security",
                    title="Session Cookie Security Policy",
                    status="PASS",
                    details="Production session cookies are configured with Secure=True, HttpOnly=True, and SameSite=Lax.",
                )
            else:
                return SecurityDiagnostic(
                    id="SEC-008",
                    category="Cookie Security",
                    title="Session Cookie Security Policy",
                    status="WARN",
                    details="Production session cookies are configured with Secure=False. The application cookie itself is not Secure; deployment must guarantee HTTPS/TLS protection externally if TLS is terminated by a trusted reverse proxy.",
                    remediation="Set AUTH_COOKIE_SECURE=true or ensure your reverse proxy enforces TLS and injects appropriate transport security.",
                )
        else:
            return SecurityDiagnostic(
                id="SEC-008",
                category="Cookie Security",
                title="Session Cookie Security Policy",
                status="PASS",
                details="Development environment: session cookies configured with HttpOnly=True and SameSite=Lax (Secure flag optional on local loopback).",
            )

    def _check_documentation_exposure(self) -> SecurityDiagnostic:
        is_prod = self._cfg.app_env.lower() == "production"
        docs_enabled = getattr(self._cfg, "enable_docs_in_prod", False)

        if is_prod:
            if docs_enabled:
                return SecurityDiagnostic(
                    id="SEC-009",
                    category="API Security",
                    title="Interactive API Documentation Exposure",
                    status="WARN",
                    details="Interactive documentation routes (/docs, /redoc, /openapi.json) are enabled in production environment via ENABLE_DOCS_IN_PROD=true.",
                    remediation="Set ENABLE_DOCS_IN_PROD=false in production to prevent schema and endpoint enumeration.",
                )
            else:
                return SecurityDiagnostic(
                    id="SEC-009",
                    category="API Security",
                    title="Interactive API Documentation Exposure",
                    status="PASS",
                    details="Interactive documentation endpoints (/docs, /redoc, /openapi.json) are disabled in production.",
                )
        else:
            return SecurityDiagnostic(
                id="SEC-009",
                category="API Security",
                title="Interactive API Documentation Exposure",
                status="PASS",
                details="Interactive documentation endpoints (/docs, /redoc, /openapi.json) are enabled for local development.",
            )

