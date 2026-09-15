"""
tests/backend/test_code_isolation_step8.py
--------------------------------------------
Phase B Step 8: Comprehensive tests for Code Execution Isolation & Exit Code Classification.

Verifies:
1. Exit code 3221225786 (0xC000013A) classified as STATUS_CONTROL_C_EXIT and treated as failure.
2. Windows NTSTATUS exit code classification map.
3. Windows creation flags mitigation (CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW).
4. Subprocess environment sanitization (exclusion of backend secrets and credentials).
5. Enhanced AST checks: eval, exec, compile, importlib, __subclasses__, dynamic imports.
6. Subprocess basic safe execution still works.
7. Execution timeout enforcement.
8. Streaming output-size cap with immediate process termination on limit breach.
9. Docker mode: daemon unavailable => execution refused.
10. Docker mode: image unavailable => execution refused (no auto-pull).
11. Docker mode: fail-closed with ZERO fallback to subprocess.
12. Docker CLI flags: --network none, --user, --cap-drop=ALL, --security-opt, --read-only, --tmpfs, memory/cpu/pids limits.
13. Docker zero host filesystem / socket mounts.
14. Docker passes code via stdin (python -u -).
15. Docker sovereign image configuration.
16. SecurityChecker SEC-007 diagnostics (PASS for verified container, WARN for hardened subprocess, FAIL for unavailable Docker).
17. Registry disable/enable support.
18. RBAC enforcement retention (Viewer blocked, Admin allowed).
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from backend.config import Settings
from backend.security.checker import SecurityChecker
from backend.tools.code_execution import (
    CodeExecutionInput,
    build_docker_run_args,
    check_docker_daemon_available,
    check_docker_image_available,
    classify_exit_code,
    create_code_execution,
    get_sanitized_subprocess_env,
    get_subprocess_creation_flags,
    validate_python_code_safety,
    _run_docker_worker,
    _run_subprocess_worker,
)
from backend.tools.registry import ToolDefinition, ToolRegistry


# ============================================================================
# 1. Exit Code Classification & 3221225786 (0xC000013A) Handling
# ============================================================================

class TestExitCodeClassification:
    def test_exit_code_3221225786_classified_as_control_c(self):
        """Confirm 3221225786 == 0xC000013A == STATUS_CONTROL_C_EXIT."""
        assert 3221225786 == 0xC000013A
        assert classify_exit_code(3221225786) == "STATUS_CONTROL_C_EXIT"
        assert classify_exit_code(0xC000013A) == "STATUS_CONTROL_C_EXIT"
        # Also check signed 32-bit integer representation
        signed_rep = -1073741510
        assert (signed_rep & 0xFFFFFFFF) == 0xC000013A
        assert classify_exit_code(signed_rep) == "STATUS_CONTROL_C_EXIT"

    def test_other_ntstatus_codes(self):
        assert classify_exit_code(0xC0000005) == "STATUS_ACCESS_VIOLATION"
        assert classify_exit_code(0xC0000142) == "STATUS_DLL_INIT_FAILED"
        assert classify_exit_code(0xC00000FD) == "STATUS_STACK_OVERFLOW"
        assert classify_exit_code(0xC0000409) == "STATUS_STACK_BUFFER_OVERRUN"
        assert classify_exit_code(0) is None

    @pytest.mark.asyncio
    async def test_control_c_treated_as_failure_with_description(self, tmp_path: Path):
        """When a worker returns exit code 3221225786, success MUST be False with clear description."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        exec_fn = create_code_execution(sandbox)
        fake_result = {
            "stdout": "",
            "stderr": "Interrupted",
            "exit_code": 3221225786,
            "timed_out": False,
            "output_limit_exceeded": False,
        }

        with patch("backend.tools.code_execution._run_subprocess_worker", return_value=fake_result):
            res = await exec_fn(CodeExecutionInput(code="print('hello')"))
            assert res["success"] is False
            assert res["exit_code"] == 3221225786
            assert "STATUS_CONTROL_C_EXIT" in res["exit_code_description"]


# ============================================================================
# 2. Subprocess Hardening: Process Group & Environment Sanitization
# ============================================================================

class TestSubprocessHardening:
    def test_windows_creation_flags(self):
        """Verify Windows process group and no-window creation flags are set as mitigation."""
        flags = get_subprocess_creation_flags()
        if sys.platform == "win32":
            assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
            assert flags & subprocess.CREATE_NO_WINDOW
        else:
            assert flags == 0

    def test_environment_sanitization_strips_secrets(self, tmp_path: Path):
        """Verify server secrets and database URLs are never leaked to child process."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        with patch.dict(os.environ, {
            "DATABASE_URL": "sqlite:////secret/db.sqlite",
            "JWT_SECRET": "super_secret_key_123",
            "OLLAMA_BASE_URL": "http://internal:11434",
            "ADMIN_PASSWORD": "do_not_leak_me",
            "PATH": "C:\\Windows\\System32",
        }, clear=True):
            clean_env = get_sanitized_subprocess_env(sandbox)
            assert "DATABASE_URL" not in clean_env
            assert "JWT_SECRET" not in clean_env
            assert "OLLAMA_BASE_URL" not in clean_env
            assert "ADMIN_PASSWORD" not in clean_env
            assert clean_env.get("PATH") == "C:\\Windows\\System32"
            assert clean_env.get("PYTHONUNBUFFERED") == "1"
            assert clean_env.get("PYTHONDONTWRITEBYTECODE") == "1"
            assert clean_env.get("PYTHONPATH") == str(sandbox.resolve())


# ============================================================================
# 3. Enhanced AST Inspection
# ============================================================================

class TestEnhancedASTValidation:
    @pytest.mark.parametrize("payload", [
        "eval('1 + 1')",
        "exec('x = 42')",
        "compile('x = 1', '<str>', 'exec')",
        "builtins.eval('1')",
    ])
    def test_blocks_eval_exec_compile(self, payload):
        with pytest.raises(ValueError, match="Security policy violation"):
            validate_python_code_safety(payload)

    @pytest.mark.parametrize("payload", [
        "import importlib",
        "import importlib.util",
        "from importlib import import_module",
        "from importlib.machinery import SourceFileLoader",
    ])
    def test_blocks_importlib(self, payload):
        with pytest.raises(ValueError, match="Security policy violation"):
            validate_python_code_safety(payload)

    @pytest.mark.parametrize("payload", [
        "().__class__.__subclasses__()",
        "int.__bases__",
        "object().__class__.__bases__",
    ])
    def test_blocks_subclasses_and_bases(self, payload):
        with pytest.raises(ValueError, match="Security policy violation"):
            validate_python_code_safety(payload)

    def test_blocks_dynamic_import_with_non_constant(self):
        with pytest.raises(ValueError, match="Security policy violation"):
            validate_python_code_safety("mod = 'socket'; __import__(mod)")


# ============================================================================
# 4. Subprocess Execution & Streaming Limits
# ============================================================================

class TestSubprocessExecutionLimits:
    @pytest.mark.asyncio
    async def test_safe_python_succeeds(self, tmp_path: Path):
        """Basic safe Python calculation succeeds in hardened subprocess mode."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        exec_fn = create_code_execution(sandbox)
        res = await exec_fn(CodeExecutionInput(code="print(sum([10, 20, 30, 4]))"))
        assert res["success"] is True
        assert res["stdout"].strip() == "64"
        assert res["exit_code"] == 0
        assert res["timed_out"] is False
        assert res["output_limit_exceeded"] is False
        assert res["isolation_mode"] == "subprocess"

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self, tmp_path: Path):
        """Long-running code is cleanly terminated and reported."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        exec_fn = create_code_execution(sandbox)
        res = await exec_fn(CodeExecutionInput(code="import time\ntime.sleep(10)", timeout_seconds=1))
        assert res["success"] is False
        assert res["timed_out"] is True
        assert res["exit_code"] == -1
        assert "timed out" in res["stderr"]

    @pytest.mark.asyncio
    async def test_output_limit_terminates_process_early(self, tmp_path: Path):
        """
        Runaway output is capped incrementally and process is terminated immediately
        to prevent unbounded buffering.
        """
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        exec_fn = create_code_execution(sandbox)

        # Patch _MAX_OUTPUT_BYTES to a small threshold for testing
        with patch("backend.tools.code_execution._MAX_OUTPUT_BYTES", 2048):
            code = "import sys\nwhile True:\n    sys.stdout.write('A' * 512)\n    sys.stdout.flush()\n"
            res = await exec_fn(CodeExecutionInput(code=code, timeout_seconds=5))
            assert res["success"] is False
            assert res["output_limit_exceeded"] is True
            assert "OUTPUT TRUNCATED" in res["stdout"]
            assert len(res["stdout"]) < 5000  # Stopped early, not unbounded


# ============================================================================
# 5. Air-Gap Safe Docker Container Execution & Controls
# ============================================================================

class TestDockerContainerIsolation:
    def test_build_docker_run_args_security_controls(self):
        """Verify the full suite of container hardening arguments."""
        args = build_docker_run_args(
            image_name="sovereign-code-sandbox:latest",
            memory_mb=256,
            cpus=0.5,
            pids_limit=32,
        )

        assert "docker" in args
        assert "run" in args
        assert "-i" in args
        assert "--rm" in args
        assert "--network" in args
        assert args[args.index("--network") + 1] == "none"
        assert "--user" in args
        assert args[args.index("--user") + 1] == "65534:65534"
        assert "--cap-drop=ALL" in args
        assert "--security-opt=no-new-privileges" in args
        assert "--read-only" in args
        assert "--tmpfs" in args
        assert args[args.index("--tmpfs") + 1] == "/tmp:rw,noexec,nosuid,size=64m"
        assert "--memory=256m" in args
        assert "--cpus=0.5" in args
        assert "--pids-limit=32" in args
        assert "-e" in args
        assert "HOME=/tmp" in args
        assert "PYTHONPYCACHEPREFIX=/tmp" in args
        assert "PYTHONUNBUFFERED=1" in args
        assert "PYTHONDONTWRITEBYTECODE=1" in args
        assert "sovereign-code-sandbox:latest" in args
        assert "python" in args
        assert "-u" in args
        assert "-" in args

    def test_zero_host_filesystem_or_socket_mounts(self):
        """Ensure no host directory, volume, or socket is mounted into the container."""
        args = build_docker_run_args("sovereign-code-sandbox:latest")
        assert "-v" not in args
        assert "--volume" not in args
        assert "--mount" not in args
        assert not any("docker.sock" in a for a in args)

    @pytest.mark.asyncio
    async def test_docker_daemon_unavailable_refuses_execution(self, tmp_path: Path):
        """When Docker daemon is unreachable, execution is refused immediately."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        cfg = Settings(code_exec_isolation="docker", code_exec_docker_image="sovereign-code-sandbox:latest")
        exec_fn = create_code_execution(sandbox, cfg=cfg)

        with patch("backend.tools.code_execution.check_docker_daemon_available", return_value=False), \
             patch("backend.tools.code_execution._run_subprocess_worker") as mock_subproc:

            with pytest.raises(RuntimeError, match="Docker daemon is unreachable or stopped"):
                await exec_fn(CodeExecutionInput(code="print('test')"))

            # Crucial: verify zero silent fallback to subprocess
            mock_subproc.assert_not_called()

    @pytest.mark.asyncio
    async def test_docker_image_unavailable_refuses_execution_no_autopull(self, tmp_path: Path):
        """When local image is not found, execution is refused with no auto-pull."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        cfg = Settings(code_exec_isolation="docker", code_exec_docker_image="nonexistent-sovereign-img:latest")
        exec_fn = create_code_execution(sandbox, cfg=cfg)

        with patch("backend.tools.code_execution.check_docker_daemon_available", return_value=True), \
             patch("backend.tools.code_execution.check_docker_image_available", return_value=False), \
             patch("backend.tools.code_execution._run_subprocess_worker") as mock_subproc:

            with pytest.raises(RuntimeError, match="strictly prevents automatic image pulling"):
                await exec_fn(CodeExecutionInput(code="print('test')"))

            mock_subproc.assert_not_called()

    def test_never_calls_docker_pull(self):
        """Verify check_docker_image_available uses 'docker image inspect', never 'docker pull'."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            check_docker_image_available("test-image:latest")

            mock_run.assert_called_once()
            called_cmd = mock_run.call_args[0][0]
            assert "pull" not in called_cmd
            assert called_cmd == ["docker", "image", "inspect", "test-image:latest"]


# ============================================================================
# 6. Security Diagnostics & Posture Checker (SEC-007)
# ============================================================================

class TestSecurityDiagnosticsCodeExecution:
    def test_subprocess_mode_warns_not_container_isolation(self):
        """Subprocess mode emits WARN indicating absence of true container boundary."""
        cfg = Settings(code_exec_isolation="subprocess")
        checker = SecurityChecker(cfg=cfg)
        diag = checker._check_code_execution_isolation()

        assert diag.id == "SEC-007"
        assert diag.status == "WARN"
        assert "Hardened local subprocess execution active" in diag.details
        assert "not container isolation" in diag.details

    def test_docker_mode_daemon_down_fails(self):
        cfg = Settings(code_exec_isolation="docker", code_exec_docker_image="sovereign-sandbox:latest")
        checker = SecurityChecker(cfg=cfg)

        with patch("backend.tools.code_execution.check_docker_daemon_available", return_value=False):
            diag = checker._check_code_execution_isolation()
            assert diag.id == "SEC-007"
            assert diag.status == "FAIL"
            assert "daemon is unreachable or stopped" in diag.details

    def test_docker_mode_image_missing_fails(self):
        cfg = Settings(code_exec_isolation="docker", code_exec_docker_image="sovereign-sandbox:latest")
        checker = SecurityChecker(cfg=cfg)

        with patch("backend.tools.code_execution.check_docker_daemon_available", return_value=True), \
             patch("backend.tools.code_execution.check_docker_image_available", return_value=False):
            diag = checker._check_code_execution_isolation()
            assert diag.id == "SEC-007"
            assert diag.status == "FAIL"
            assert "is not found locally" in diag.details

    def test_docker_mode_ready_passes(self):
        cfg = Settings(code_exec_isolation="docker", code_exec_docker_image="sovereign-sandbox:latest")
        checker = SecurityChecker(cfg=cfg)

        with patch("backend.tools.code_execution.check_docker_daemon_available", return_value=True), \
             patch("backend.tools.code_execution.check_docker_image_available", return_value=True):
            diag = checker._check_code_execution_isolation()
            assert diag.id == "SEC-007"
            assert diag.status == "PASS"
            assert "Container isolation active" in diag.details


# ============================================================================
# 7. ToolRegistry Enable/Disable & RBAC Retention
# ============================================================================

class TestRegistryIntegrationAndRBAC:
    def test_tool_registry_enable_disable(self):
        registry = ToolRegistry()
        tool = ToolDefinition(
            name="dummy_tool",
            description="dummy",
            input_schema=CodeExecutionInput,
            execute_fn=lambda x: {"dummy": True},
            category="Test",
            enabled=True,
        )
        registry.register(tool)
        assert registry.get("dummy_tool").enabled is True

        assert registry.disable("dummy_tool") is True
        assert registry.get("dummy_tool").enabled is False

        assert registry.enable("dummy_tool") is True
        assert registry.get("dummy_tool").enabled is True

    @pytest.mark.asyncio
    async def test_rbac_user_viewer_blocked_from_code_execution(self, tmp_path: Path):
        """Viewer role cannot execute code_execution (Step 6/7 RBAC preservation)."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="code_execution",
            description="Execute code",
            input_schema=CodeExecutionInput,
            execute_fn=create_code_execution(sandbox),
            category="Computation",
            read_only=False,
            requires_approval=False,
            enabled=True,
        ))

        res = await registry.execute(
            name="code_execution",
            arguments={"code": "print(123)"},
            session_id="test_session",
            user_role="user",  # Viewer role
        )

        assert res.success is False
        assert "Permission denied" in res.error

    @pytest.mark.asyncio
    async def test_rbac_admin_allowed_code_execution(self, tmp_path: Path):
        """Admin role can execute code_execution."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="code_execution",
            description="Execute code",
            input_schema=CodeExecutionInput,
            execute_fn=create_code_execution(sandbox),
            category="Computation",
            read_only=False,
            requires_approval=False,
            enabled=True,
        ))

        res = await registry.execute(
            name="code_execution",
            arguments={"code": "print(100 + 23)"},
            session_id="test_session",
            user_role="admin",
        )

        assert res.success is True
        assert res.result["stdout"].strip() == "123"
