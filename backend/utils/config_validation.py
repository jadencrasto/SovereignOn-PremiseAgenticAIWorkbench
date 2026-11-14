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
        is_prod = self._cfg.app_env.lower() in ("production", "prod")
        auth_on = getattr(self._cfg, "auth_enabled", True)
        dev_override = getattr(self._cfg, "auth_dev_override", False)

        host = self._cfg.backend_host.strip().lower()
        loopback = host in ("127.0.0.1", "::1", "localhost")
        results.append({
            "rule": "external_binding_auth",
            "status": "PASS" if loopback or (auth_on and not dev_override) else "FAIL",
            "message": "Non-loopback binding requires authentication without a development override.",
        })

        if is_prod and not auth_on:
            results.append({
                "rule": "prod_auth_enabled",
                "status": "FAIL",
                "message": "Authentication cannot be disabled in production, including with a development override.",
            })
        else:
            results.append({
                "rule": "prod_auth_enabled",
                "status": "PASS",
                "message": "Authentication configuration matches environment.",
            })

        results.append({
            "rule": "prod_auth_dev_override",
            "status": "FAIL" if is_prod and dev_override else "PASS",
            "message": "Authentication development override must be disabled in production.",
        })

        if getattr(self._cfg, 'local_chat_only', False):
            from backend.health.capabilities import loopback_tags_url
            try:
                loopback_tags_url(self._cfg.ollama_base_url)
                safe_ollama = True
            except ValueError:
                safe_ollama = False
            local_ok = (self._cfg.app_env.lower() in ('development', 'dev') and auth_on and not dev_override
                        and host in ('127.0.0.1', '::1') and safe_ollama)
            results.append({"rule": "local_chat_only_security", "status": "PASS" if local_ok else "FAIL",
                "message": "Chat-only requires development, authenticated loopback configuration and a loopback Ollama endpoint."})

        # 1b. Production vs Dev Mode conflict check
        dev_mode_flag = getattr(self._cfg, "dev_mode", None)
        if is_prod and dev_mode_flag is True:
            results.append({
                "rule": "prod_dev_mode_conflict",
                "status": "FAIL",
                "message": "Contradictory security configuration: app_env is production but dev_mode is True. Production mode fails closed.",
            })
        else:
            results.append({
                "rule": "prod_dev_mode_conflict",
                "status": "PASS",
                "message": "No contradictory development flags detected in production.",
            })

        # 1c. Production Cookie Security check
        cookie_secure = getattr(self._cfg, "auth_cookie_secure", False)
        if is_prod:
            if cookie_secure:
                results.append({
                    "rule": "prod_cookie_security",
                    "status": "PASS",
                    "message": "Production cookie flagged Secure.",
                })
            else:
                results.append({
                    "rule": "prod_cookie_security",
                    "status": "WARN",
                    "message": "Production cookie is not Secure; deployment must guarantee HTTPS/TLS protection externally if TLS is terminated by a trusted reverse proxy.",
                })
        else:
            results.append({
                "rule": "prod_cookie_security",
                "status": "PASS",
                "message": "Development cookie policy active (Secure flag optional on local loopback).",
            })

        # 1d. Documentation Exposure check
        docs_enabled_in_prod = getattr(self._cfg, "enable_docs_in_prod", False)
        if is_prod and docs_enabled_in_prod:
            results.append({
                "rule": "prod_docs_exposure",
                "status": "WARN",
                "message": "Interactive API documentation (/docs, /redoc, /openapi.json) is enabled in production environment.",
            })
        else:
            results.append({
                "rule": "prod_docs_exposure",
                "status": "PASS",
                "message": "Interactive API documentation exposure matches environment policy.",
            })

        # 2. CORS configuration check
        cors_list = self._cfg.cors_origins_list
        if "*" in cors_list and is_prod:
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
                "message": "CORS origins are explicitly configured.",
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
        try:
            parsed = urlparse(self._cfg.ollama_base_url)
            host = (parsed.hostname or "").lower()
            malformed = (parsed.scheme not in ("http", "https") or not host or
                         parsed.username is not None or parsed.password is not None or
                         bool(parsed.query or parsed.fragment) or
                         parsed.path not in ("", "/") or parsed.port == 0 or
                         any(ord(c) <= 32 or ord(c) == 127 for c in self._cfg.ollama_base_url) or
                         "\\" in self._cfg.ollama_base_url)
        except ValueError:
            malformed = True
            host = ""
        if malformed:
            results.append({"rule": "ollama_local_url", "status": "FAIL",
                            "message": "Ollama endpoint is malformed or includes unsupported components."})
        elif host in ("localhost", "127.0.0.1", "::1"):
            results.append({
                "rule": "ollama_local_url",
                "status": "PASS",
                "message": "Ollama is configured with a loopback host; availability is unverified.",
            })
        else:
            results.append({
                "rule": "ollama_local_url",
                "status": "WARN",
                "message": "Ollama is configured with a non-loopback host; offline operation is unverified.",
            })

        # 5. Directory paths
        try:
            self._cfg.ensure_dirs()
            results.append({
                "rule": "runtime_directories",
                "status": "PASS",
                "message": "Runtime directory initialization completed; writability and security are unverified.",
            })
        except Exception:
            results.append({
                "rule": "runtime_directories",
                "status": "FAIL",
                "message": "Failed to initialize runtime directories; check server configuration and permissions.",
            })

        # 6. Production code execution isolation
        from backend.security.execution_policy import code_execution_allowed
        code_exec_isolation = getattr(self._cfg, "code_exec_isolation", "subprocess").lower()
        if not code_execution_allowed():
            results.append({
                "rule": "prod_code_exec_isolation",
                "status": "PASS",
                "message": "Code execution is disabled; isolation has not been verified.",
            })
        elif is_prod:
            if code_exec_isolation != "docker":
                results.append({
                    "rule": "prod_code_exec_isolation",
                    "status": "FAIL",
                    "message": "Production environment requires Docker container isolation ('code_exec_isolation: docker'). Subprocess isolation is not permitted in production.",
                })
            else:
                from backend.tools.code_execution import check_docker_daemon_available, check_docker_image_available
                if not check_docker_daemon_available():
                    results.append({
                        "rule": "prod_code_exec_isolation",
                        "status": "FAIL",
                        "message": "Production environment has Docker code execution configured, but Docker daemon is unreachable or stopped. Failing closed.",
                    })
                else:
                    image_name = getattr(self._cfg, "code_exec_docker_image", "sovereign-code-sandbox:latest")
                    if not check_docker_image_available(image_name):
                        results.append({
                            "rule": "prod_code_exec_isolation",
                            "status": "FAIL",
                            "message": f"Production Docker image '{image_name}' is not found in local Docker storage. Execution refused.",
                        })
                    else:
                        results.append({
                            "rule": "prod_code_exec_isolation",
                            "status": "PASS",
                            "message": "Production code execution is securely isolated via Docker container.",
                        })
        else:
            results.append({
                "rule": "prod_code_exec_isolation",
                "status": "PASS",
                "message": f"Code execution isolation mode is '{code_exec_isolation}' (development/non-production).",
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
