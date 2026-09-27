"""
backend/tools/security_diagnostics.py
-------------------------------------
Security diagnostics and posture checking agent tool.

Executes deterministic local posture checks across authentication,
egress isolation, sandbox containment, and database hardening.
Enforces VIEW_SECURITY permission.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.auth.models import Permission, has_permission

logger = logging.getLogger(__name__)


class SecurityDiagnosticsInput(BaseModel):
    """Input schema for security_diagnostics tool."""
    category: Optional[str] = Field(
        default=None,
        description="Optional filter by check category (e.g. 'Authentication', 'Air-Gap', 'Sandbox', 'Database').",
    )


def create_security_diagnostics(settings_obj: Any = None, auth_store: Any = None) -> callable:
    """Create the security_diagnostics execution function."""
    from backend.config import settings
    from backend.security.checker import SecurityChecker

    active_cfg = settings_obj or settings

    async def execute_security_diagnostics(
        args: SecurityDiagnosticsInput,
        user_role: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Execute deterministic security checks with permission gating."""
        effective_role = user_role or kwargs.get("user_role") or "viewer"

        # Check permission: viewing security diagnostics requires VIEW_SECURITY
        if not has_permission(effective_role, Permission.VIEW_SECURITY):
            raise PermissionError(
                f"Permission denied: role '{effective_role}' lacks VIEW_SECURITY permission "
                f"required to access security diagnostics."
            )

        checker = SecurityChecker(cfg=active_cfg, auth_store=auth_store)
        all_checks = checker.run_all_checks()

        if args.category:
            cat_lower = args.category.strip().lower()
            filtered = [c for c in all_checks if cat_lower in c.get("category", "").lower()]
        else:
            filtered = all_checks

        # Calculate overall posture
        statuses = [c.get("status") for c in all_checks]
        if "FAIL" in statuses:
            overall = "FAIL"
        elif "WARN" in statuses:
            overall = "WARN"
        else:
            overall = "PASS"

        from pathlib import Path
        from urllib.parse import urlparse
        ollama_url = getattr(active_cfg, "ollama_base_url", "http://localhost:11434")
        parsed_url = urlparse(ollama_url)
        host = (parsed_url.hostname or "").lower()
        is_loopback = host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")
        ext_apis = "None / local-only" if is_loopback else f"External ({ollama_url})"
        net_status = "Restricted / local loopback only" if is_loopback else f"External network access configured ({ollama_url})"
        doc_storage = "Local / data/uploads"
        retention = getattr(active_cfg, "audit_retention_days", 180)
        max_rows = getattr(active_cfg, "audit_max_rows", 50000)
        audit_status = f"Active (SQLite audit trail, {retention}-day retention, max {max_rows} rows)"
        default_model = getattr(active_cfg, "default_model", "ollama/qwen2.5:7b")

        return {
            "overall_status": overall,
            "total_checks": len(all_checks),
            "reported_checks": len(filtered),
            "diagnostics": filtered,
            "current_model": default_model,
            "external_api_connections": ext_apis,
            "network_access_status": net_status,
            "document_storage_location": doc_storage,
            "audit_logging_status": audit_status,
        }

    return execute_security_diagnostics
