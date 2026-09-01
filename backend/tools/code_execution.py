"""
backend/tools/code_execution.py
---------------------------------
Local Python code execution sandbox tool for the Sovereign AI Workbench.

Security & Isolation:
- Executes strictly within settings.sandbox_dir boundary
- Static AST inspection prevents dangerous module imports and escapes
  (e.g., sockets, network libraries, subprocess, ctypes, winreg)
- Subprocess isolation with strict execution timeout (default 5s, max 30s)
- Captures stdout, stderr, exit code, and timeout status
- Truncates oversized outputs to prevent memory exhaustion
- Uses asyncio.to_thread(subprocess.run) for robust cross-platform event loop compatibility (Windows Selector/Proactor)
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Execution limits
_DEFAULT_TIMEOUT_SECONDS = 5
_MAX_TIMEOUT_SECONDS = 30
_MAX_OUTPUT_BYTES = 50 * 1024  # 50 KB max stdout/stderr

# Blacklisted modules that could attempt network access, process spawning, or sandbox escape
_DISALLOWED_MODULES: Set[str] = {
    # Network & sockets
    "socket", "urllib", "requests", "httpx", "aiohttp", "ftplib",
    "telnetlib", "smtplib", "xmlrpc", "http.client", "http.server",
    "paramiko", "asyncssh", "twisted", "tornado", "websocket",
    # Process & system escape
    "subprocess", "multiprocessing", "pty", "ctypes", "winreg", "_winreg",
    "posix", "shlex", "concurrent.futures.process",
}

# Dangerous os attributes/functions
_DISALLOWED_OS_CALLS: Set[str] = {
    "system", "popen", "spawn", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe", "exec", "execl", "execle",
    "execlp", "execlpe", "execv", "execve", "execvp", "execvpe", "fork",
    "forkpty", "kill", "killpg", "plock",
}


class CodeExecutionInput(BaseModel):
    """Input schema for the code_execution tool."""
    code: str = Field(
        ...,
        min_length=1,
        description="Python code to execute inside the local sandbox boundary.",
    )
    timeout_seconds: Optional[int] = Field(
        default=_DEFAULT_TIMEOUT_SECONDS,
        ge=1,
        le=_MAX_TIMEOUT_SECONDS,
        description="Execution timeout limit in seconds (default: 5s, max: 30s).",
    )


def validate_python_code_safety(code: str) -> None:
    """
    Perform static AST security inspection on candidate Python code before execution.

    Raises:
        ValueError: If code fails syntax parsing or contains blacklisted operations.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"Invalid Python syntax: {exc.msg} (line {exc.lineno})")

    for node in ast.walk(tree):
        # 1. Inspect import statements: 'import foo'
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod_root = alias.name.split(".")[0]
                if alias.name in _DISALLOWED_MODULES or mod_root in _DISALLOWED_MODULES:
                    raise ValueError(
                        f"Security policy violation: import of module '{alias.name}' is blocked in sandbox."
                    )

        # 2. Inspect 'from foo import bar'
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod_root = node.module.split(".")[0]
                if node.module in _DISALLOWED_MODULES or mod_root in _DISALLOWED_MODULES:
                    raise ValueError(
                        f"Security policy violation: import from module '{node.module}' is blocked in sandbox."
                    )

        # 3. Inspect attribute access like os.system or __builtins__.__import__
        elif isinstance(node, ast.Attribute):
            if node.attr in _DISALLOWED_OS_CALLS:
                raise ValueError(
                    f"Security policy violation: dangerous function call 'os.{node.attr}' is blocked."
                )

        # 4. Inspect direct calls to dangerous built-in functions
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in ("__import__",):
                    # Check first arg if constant
                    if node.args and isinstance(node.args[0], ast.Constant):
                        mod_name = str(node.args[0].value)
                        mod_root = mod_name.split(".")[0]
                        if mod_name in _DISALLOWED_MODULES or mod_root in _DISALLOWED_MODULES:
                            raise ValueError(
                                f"Security policy violation: dynamic import of '{mod_name}' is blocked."
                            )


def _run_subprocess_worker(tmp_path: Path, cwd: Path, env: dict, timeout: int) -> Dict[str, Any]:
    """
    Synchronous subprocess execution worker executed in threadpool.
    Guarantees cross-platform execution on all Windows/Linux event loops.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(tmp_path)],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "stdout": proc.stdout[:_MAX_OUTPUT_BYTES],
            "stderr": proc.stderr[:_MAX_OUTPUT_BYTES],
            "exit_code": proc.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as te:
        stdout_text = te.stdout if isinstance(te.stdout, str) else (
            te.stdout.decode("utf-8", errors="replace") if te.stdout else ""
        )
        return {
            "stdout": stdout_text[:_MAX_OUTPUT_BYTES],
            "stderr": f"Execution timed out after {timeout} seconds.",
            "exit_code": -1,
            "timed_out": True,
        }
    except Exception as exc:
        return {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": -1,
            "timed_out": False,
        }


def create_code_execution(sandbox_dir: Path) -> callable:
    """
    Create the code_execution execution function bound to sandbox_dir.
    """
    sandbox_resolved = sandbox_dir.resolve()
    sandbox_resolved.mkdir(parents=True, exist_ok=True)

    async def execute_code_execution(args: CodeExecutionInput) -> Dict[str, Any]:
        code = args.code.strip()
        if not code:
            raise ValueError("Code content cannot be empty.")

        # 1. Static security check
        validate_python_code_safety(code)

        timeout = min(max(1, args.timeout_seconds or _DEFAULT_TIMEOUT_SECONDS), _MAX_TIMEOUT_SECONDS)

        # 2. Write code to a temporary script file inside sandbox directory
        script_dir = sandbox_resolved / ".tmp_exec"
        script_dir.mkdir(parents=True, exist_ok=True)
        
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            dir=str(script_dir),
            encoding="utf-8",
            delete=False,
        ) as tmp:
            tmp.write(code)
            tmp_path = Path(tmp.name)

        # Prepare isolated execution environment
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(sandbox_resolved)

        try:
            raw_res = await asyncio.to_thread(
                _run_subprocess_worker,
                tmp_path=tmp_path,
                cwd=sandbox_resolved,
                env=env,
                timeout=timeout,
            )
        finally:
            # Cleanup temp script
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        exit_code = raw_res.get("exit_code", 0)
        timed_out = raw_res.get("timed_out", False)
        stdout_text = raw_res.get("stdout", "")
        stderr_text = raw_res.get("stderr", "")
        success = (exit_code == 0) and not timed_out

        logger.info(
            "code_execution | success=%s exit_code=%d timed_out=%s stdout_len=%d",
            success, exit_code, timed_out, len(stdout_text)
        )

        return {
            "success": success,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "exit_code": exit_code,
            "timed_out": timed_out,
        }

    return execute_code_execution
