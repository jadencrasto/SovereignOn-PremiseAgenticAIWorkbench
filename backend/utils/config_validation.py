"""
backend/utils/config_validation.py
-----------------------------------
Phase 7: Configuration validation and fail-fast startup checks.

Ensures that the server refuses to start in misconfigured or insecure states.
Supports CLI invocation via `python -m backend.utils.config_validation` or CLI tools.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from backend.config import Settings, settings

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """Raised when application configuration fails validation."""
    pass


class ConfigValidator:
    """Validates configuration parameters for safety and operational readiness."""

    def __init__(self, cfg: Settings = settings) -> None:
        self._cfg = cfg

    def validate(self) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Validate current configuration.

        Returns:
            (is_valid, list of check results with status: PASS | WARN | FAIL)
        """
        results: List[Dict[str, Any]] = []

        # 1. Environment & Auth consistency
        if self._cfg.app_env.lower() == "production" and not getattr(self._cfg, "auth_enabled", True):
            results.append({
                "rule": "prod_auth_enabled",
                "status": "FAIL",
                "message": "Authentication cannot be disabled (auth_enabled=false) in production environment.",
            })
        else:
            results.append({
                "rule": "prod_auth_enabled",
                "status": "PASS",
                "message": "Authentication configuration matches environment.",
            })

        # 2. CORS configuration check
        cors_list = self._cfg.cors_origins_list
        if "*" in cors_list and self._cfg.app_env.lower() == "production":
            results.append({
                "rule": "cors_wildcard_in_prod",
                "status": "FAIL",
                "message": "Wildcard CORS origin ('*') is forbidden in production environment.",
            })
        elif "*" in cors_list:
            results.append({
                "rule": "cors_wildcard_in_dev",
                "status": "WARN",
                "message": "Wildcard CORS origin ('*') is active. Restrict this before deploying to production.",
            })
        else:
            results.append({
                "rule": "cors_origins",
                "status": "PASS",
                "message": f"CORS origins restricted to: {cors_list}",
            })

        # 3. Port bounds
        if not (1 <= self._cfg.backend_port <= 65535):
            results.append({
                "rule": "backend_port_range",
                "status": "FAIL",
                "message": f"Invalid backend port: {self._cfg.backend_port}. Must be between 1 and 65535.",
            })
        else:
            results.append({
                "rule": "backend_port_range",
                "status": "PASS",
                "message": f"Backend port {self._cfg.backend_port} is valid.",
            })

        # 4. Ollama Endpoint check (local air-gap verification)
        parsed = urlparse(self._cfg.ollama_base_url)
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            results.append({
                "rule": "ollama_local_url",
                "status": "PASS",
                "message": f"Ollama is configured locally at {self._cfg.ollama_base_url}.",
            })
        else:
            results.append({
                "rule": "ollama_local_url",
                "status": "WARN",
                "message": f"Ollama base URL points to non-loopback address: '{self._cfg.ollama_base_url}'. Ensure network is secured.",
            })

        # 5. Directory paths
        try:
            self._cfg.ensure_dirs()
            results.append({
                "rule": "runtime_directories",
                "status": "PASS",
                "message": "All runtime directories exist and are writable.",
            })
        except Exception as exc:
            results.append({
                "rule": "runtime_directories",
                "status": "FAIL",
                "message": f"Failed to initialize runtime directories: {exc}",
            })

        # Determine overall validity
        has_fails = any(r["status"] == "FAIL" for r in results)
        return (not has_fails), results

    def enforce_or_exit(self) -> None:
        """Enforce validation rules on startup; exit if any FAIL rule triggers."""
        valid, results = self.validate()
        for r in results:
            if r["status"] == "FAIL":
                logger.critical("CONFIG VALIDATION FAILED [%s]: %s", r["rule"], r["message"])
            elif r["status"] == "WARN":
                logger.warning("CONFIG VALIDATION WARNING [%s]: %s", r["rule"], r["message"])

        if not valid:
            logger.critical("Server refusing startup due to fatal configuration validation errors.")
            raise ConfigValidationError("Fatal configuration errors detected.")


def cli_validate() -> int:
    """CLI entry point for validate-config."""
    validator = ConfigValidator()
    valid, results = validator.validate()
    print("=" * 60)
    print(" Sovereign AI Workbench — Configuration Validation")
    print("=" * 60)
    for r in results:
        status_color = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}.get(r["status"], "[INFO]")
        print(f"{status_color:8} {r['rule']:25} {r['message']}")
    print("=" * 60)
    if valid:
        print("Configuration is valid.")
        return 0
    else:
        print("Configuration contains errors.")
        return 1


if __name__ == "__main__":
    sys.exit(cli_validate())
