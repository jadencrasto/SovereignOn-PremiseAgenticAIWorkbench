"""
backend/tools/code_execution.py
---------------------------------
Python code execution tool for the Sovereign AI Workbench.

Security & Execution Modes:
1. Hardened Local Subprocess Execution (Default):
   - Executes strictly within settings.sandbox_dir boundary
   - Process group isolation on Windows (CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)
     as a mitigation against console control signal propagation (STATUS_CONTROL_C_EXIT)
   - Strict environment allowlist (no server secrets or database credentials leaked)
   - Static AST pre-inspection (blocks network imports, eval/exec/compile, os.system/popen,
     importlib, and class hierarchy traversal escapes)
   - Process timeout and hard output size limits with early termination
   - NOTE: This is hardened local subprocess execution only, NOT true OS/container isolation.

2. Air-Gap Safe Docker Container Isolation (Optional):
   - True container-level boundary with isolated kernel namespaces
   - Requires pre-existing local sovereign Docker image (NEVER automatically pulls from internet)
   - Disconnected network (--network none unconditionally)
   - Dropped capabilities (--cap-drop=ALL), no-new-privileges, unprivileged UID (65534:65534)
   - Read-only root filesystem with ephemeral memory-only tmpfs
   - Zero host filesystem or Docker socket mounts (code supplied via stdin)
   - Fail-closed: refuses execution if daemon or sovereign image is unavailable (never silently falls back)
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

from backend.config import settings

logger = logging.getLogger(__name__)

# Execution limits
_DEFAULT_TIMEOUT_SECONDS = 5
_MAX_TIMEOUT_SECONDS = 30
_MAX_OUTPUT_BYTES = 50 * 1024  # 50 KB max stdout/stderr

# Windows NTSTATUS exit codes (unsigned 32-bit representations)
_WINDOWS_NTSTATUS_CODES: Dict[int, str] = {
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC000013A: "STATUS_CONTROL_C_EXIT",           # Console control event / Ctrl+C / console close
    0xC0000142: "STATUS_DLL_INIT_FAILED",
    0xC00000FD: "STATUS_STACK_OVERFLOW",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    0x40010004: "DBG_TERMINATE_PROCESS",
}

# Blacklisted modules that could attempt network access, process spawning, or sandbox escape
_DISALLOWED_MODULES: Set[str] = {
    # Network & sockets
    "socket", "urllib", "requests", "httpx", "aiohttp", "ftplib",
    "telnetlib", "smtplib", "xmlrpc", "http.client", "http.server",
    "paramiko", "asyncssh", "twisted", "tornado", "websocket",
    # Process & system escape
    "subprocess", "multiprocessing", "pty", "ctypes", "winreg", "_winreg",
    "posix", "shlex", "concurrent.futures.process",
    # Dynamic import mechanisms
    "importlib",
}

# Dangerous os attributes/functions
_DISALLOWED_OS_CALLS: Set[str] = {
    "system", "popen", "spawn", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe", "exec", "execl", "execle",
    "execlp", "execlpe", "execv", "execve", "execvp", "execvpe", "fork",
    "forkpty", "kill", "killpg", "plock",
}

# Dangerous built-ins that bypass static import validation
_DISALLOWED_BUILTIN_CALLS: Set[str] = {"eval", "exec", "compile"}

# Attributes used for class hierarchy traversal / escape
_DISALLOWED_ATTRIBUTES: Set[str] = {"__subclasses__", "__bases__"}

# Safe environment variables allowlist for local subprocess execution
_SAFE_SUBPROCESS_ENV_KEYS: Set[str] = {
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC",
    "HOME", "USERPROFILE", "LANG", "LC_ALL",
}


class CodeExecutionInput(BaseModel):
    """Input schema for the code_execution tool."""
    code: str = Field(
        ...,
        min_length=1,
        description="Python code to execute inside the sandbox boundary.",
    )
    timeout_seconds: Optional[int] = Field(
        default=_DEFAULT_TIMEOUT_SECONDS,
        ge=1,
        le=_MAX_TIMEOUT_SECONDS,
        description="Execution timeout limit in seconds (default: 5s, max: 30s).",
    )


def classify_exit_code(code: int) -> Optional[str]:
    """Classify Windows NTSTATUS or system exit code if recognized."""
    unsigned_code = code & 0xFFFFFFFF
    return _WINDOWS_NTSTATUS_CODES.get(unsigned_code)


def get_subprocess_creation_flags() -> int:
    """
    Return creation flags for Windows child processes.
    Uses CREATE_NEW_PROCESS_GROUP and CREATE_NO_WINDOW as a mitigation
    against console control signal propagation (such as Ctrl+C / STATUS_CONTROL_C_EXIT).
    """
    flags = 0
    if sys.platform == "win32":
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return flags


def get_sanitized_subprocess_env(sandbox_dir: Path) -> Dict[str, str]:
    """
    Construct a strictly sanitized environment dictionary for child process execution.
    Prevents leakage of parent server credentials, database URLs, or API keys.
    """
    env: Dict[str, str] = {}
    for key in _SAFE_SUBPROCESS_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val

    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(sandbox_dir)
    return env


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

        # 3. Inspect attribute access like os.system, __subclasses__, eval, exec
        elif isinstance(node, ast.Attribute):
            if node.attr in _DISALLOWED_OS_CALLS:
                raise ValueError(
                    f"Security policy violation: dangerous function call 'os.{node.attr}' is blocked."
                )
            if node.attr in _DISALLOWED_ATTRIBUTES:
                raise ValueError(
                    f"Security policy violation: class hierarchy introspection '{node.attr}' is blocked."
                )
            if node.attr in _DISALLOWED_BUILTIN_CALLS:
                raise ValueError(
                    f"Security policy violation: call to '{node.attr}' is blocked in sandbox."
                )

        # 4. Inspect direct calls to dangerous built-in functions
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in _DISALLOWED_BUILTIN_CALLS:
                    raise ValueError(
                        f"Security policy violation: call to built-in function '{node.func.id}()' is blocked in sandbox."
                    )
                if node.func.id == "__import__":
                    if not node.args or not isinstance(node.args[0], ast.Constant):
                        raise ValueError(
                            "Security policy violation: dynamic import with non-constant argument is blocked."
                        )
                    mod_name = str(node.args[0].value)
                    mod_root = mod_name.split(".")[0]
                    if mod_name in _DISALLOWED_MODULES or mod_root in _DISALLOWED_MODULES:
                        raise ValueError(
                            f"Security policy violation: dynamic import of '{mod_name}' is blocked."
                        )


def _drain_process_with_limit(
    proc: subprocess.Popen,
    timeout: int,
    max_bytes: int = _MAX_OUTPUT_BYTES,
) -> Dict[str, Any]:
    """
    Read stdout and stderr concurrently with timeout and hard cumulative output-size cap.
    If cumulative output exceeds max_bytes, the process is immediately killed to prevent
    unbounded buffering and memory exhaustion.
    """
    stdout_chunks: List[str] = []
    stderr_chunks: List[str] = []
    total_bytes = 0
    output_limit_exceeded = False
    lock = threading.Lock()

    def stream_reader(stream, chunk_list):
        nonlocal total_bytes, output_limit_exceeded
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(1024)
                if not chunk:
                    break
                chunk_len = len(chunk.encode("utf-8", errors="replace"))
                with lock:
                    total_bytes += chunk_len
                    if total_bytes > max_bytes:
                        output_limit_exceeded = True
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        remaining = max(0, max_bytes - (total_bytes - chunk_len))
                        if remaining > 0:
                            chunk_list.append(chunk[:remaining])
                        break
                    else:
                        chunk_list.append(chunk)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_stdout = threading.Thread(target=stream_reader, args=(proc.stdout, stdout_chunks))
    t_stderr = threading.Thread(target=stream_reader, args=(proc.stderr, stderr_chunks))
    t_stdout.daemon = True
    t_stderr.daemon = True
    t_stdout.start()
    t_stderr.start()

    start_time = time.monotonic()
    t_stdout.join(timeout=timeout)
    elapsed = time.monotonic() - start_time
    remaining_time = max(0.1, timeout - elapsed)
    t_stderr.join(timeout=remaining_time)

    timed_out = False
    if t_stdout.is_alive() or t_stderr.is_alive():
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        t_stdout.join(timeout=0.5)
        t_stderr.join(timeout=0.5)

    try:
        proc.wait(timeout=2.0)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass

    stdout_text = "".join(stdout_chunks)
    stderr_text = "".join(stderr_chunks)

    if timed_out:
        timeout_msg = f"Execution timed out after {timeout} seconds."
        stderr_text = f"{stderr_text}\n{timeout_msg}".strip() if stderr_text else timeout_msg

    if output_limit_exceeded:
        trunc_banner = f"\n[OUTPUT TRUNCATED: Execution terminated because output exceeded maximum limit of {max_bytes} bytes]"
        stdout_text += trunc_banner
        limit_msg = f"Execution terminated: output exceeded maximum limit of {max_bytes} bytes."
        stderr_text = f"{stderr_text}\n{limit_msg}".strip() if stderr_text else limit_msg

    exit_code = proc.returncode if proc.returncode is not None else -1
    if timed_out:
        exit_code = -1

    return {
        "stdout": stdout_text,
        "stderr": stderr_text,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "output_limit_exceeded": output_limit_exceeded,
    }


def _run_subprocess_worker(
    tmp_path: Path,
    cwd: Path,
    env: dict,
    timeout: int,
) -> Dict[str, Any]:
    """
    Synchronous hardened local subprocess execution worker.
    Enforces process group isolation, creation flags, environment sanitization,
    timeout, and hard output-size capping with early termination.
    """
    creationflags = get_subprocess_creation_flags()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", str(tmp_path)],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        return _drain_process_with_limit(proc, timeout=timeout, max_bytes=_MAX_OUTPUT_BYTES)
    except Exception as exc:
        return {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": -1,
            "timed_out": False,
            "output_limit_exceeded": False,
        }


def check_docker_daemon_available() -> bool:
    """Check if Docker daemon is responsive without pulling or modifying anything."""
    try:
        res = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.returncode == 0 and bool(res.stdout.strip())
    except Exception:
        return False


def check_docker_image_available(image_name: str) -> bool:
    """Check if specified image exists in local Docker storage without pulling from internet."""
    try:
        res = subprocess.run(
            ["docker", "image", "inspect", image_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def build_docker_run_args(
    image_name: str,
    memory_mb: int = 512,
    cpus: float = 1.0,
    pids_limit: int = 64,
) -> List[str]:
    """
    Construct argument vector for air-gap safe Docker container execution.
    - Strictly no network (--network none)
    - Unprivileged non-root execution (--user 65534:65534)
    - Dropped capabilities (--cap-drop=ALL)
    - No privilege escalation (--security-opt=no-new-privileges)
    - Read-only root filesystem (--read-only)
    - Ephemeral memory-only tmpfs (--tmpfs /tmp:rw,noexec,nosuid,size=64m)
    - Hard resource ceilings (--memory, --cpus, --pids-limit)
    - Ephemeral cleanup (--rm)
    - Sanitized minimal environment (no host secrets)
    - ZERO host filesystem mounts (no host volume, no docker socket)
    - Code supplied via stdin (python -u -)
    """
    return [
        "docker", "run", "-i", "--rm",
        "--network", "none",
        "--user", "65534:65534",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        f"--memory={memory_mb}m",
        f"--cpus={cpus}",
        f"--pids-limit={pids_limit}",
        "-e", "HOME=/tmp",
        "-e", "PYTHONPYCACHEPREFIX=/tmp",
        "-e", "PYTHONUNBUFFERED=1",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        image_name,
        "python", "-u", "-"
    ]


def _run_docker_worker(
    code: str,
    image_name: str,
    timeout: int,
    memory_mb: int = 512,
    cpus: float = 1.0,
    pids_limit: int = 64,
) -> Dict[str, Any]:
    """
    Synchronous air-gap safe Docker container execution worker.
    Fails closed if daemon or image is unavailable. Never silently falls back to subprocess.
    """
    if not check_docker_daemon_available():
        raise RuntimeError(
            "Docker container isolation is configured, but Docker daemon is unreachable or stopped. "
            "Air-gap security policy prevents fallback to subprocess. Execution refused."
        )
    if not check_docker_image_available(image_name):
        raise RuntimeError(
            f"Docker container isolation is configured, but local sovereign image '{image_name}' "
            "is not found in local Docker storage. Air-gap security policy strictly prevents automatic image pulling. "
            "Execution refused."
        )

    cmd = build_docker_run_args(
        image_name=image_name,
        memory_mb=memory_mb,
        cpus=cpus,
        pids_limit=pids_limit,
    )

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        return {
            "stdout": "",
            "stderr": f"Failed to launch Docker container: {exc}",
            "exit_code": -1,
            "timed_out": False,
            "output_limit_exceeded": False,
        }

    # Pass code strictly via stdin pipe (zero host filesystem mounts)
    try:
        if proc.stdin:
            proc.stdin.write(code)
            proc.stdin.close()
    except Exception as exc:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "stdout": "",
            "stderr": f"Failed to pipe code to container stdin: {exc}",
            "exit_code": -1,
            "timed_out": False,
            "output_limit_exceeded": False,
        }

    return _drain_process_with_limit(proc, timeout=timeout, max_bytes=_MAX_OUTPUT_BYTES)


def create_code_execution(sandbox_dir: Path, cfg: Optional[Any] = None) -> callable:
    """
    Create the code_execution execution function bound to sandbox_dir and configuration.
    """
    sandbox_resolved = sandbox_dir.resolve()
    sandbox_resolved.mkdir(parents=True, exist_ok=True)
    active_cfg = cfg or settings

    async def execute_code_execution(args: CodeExecutionInput) -> Dict[str, Any]:
        code = args.code.strip()
        if not code:
            raise ValueError("Code content cannot be empty.")

        # 1. Static security check (applies to both subprocess and container modes)
        validate_python_code_safety(code)

        timeout = min(max(1, args.timeout_seconds or _DEFAULT_TIMEOUT_SECONDS), _MAX_TIMEOUT_SECONDS)
        isolation_mode = getattr(active_cfg, "code_exec_isolation", "subprocess").lower()

        if isolation_mode == "docker":
            # Air-gap safe Docker execution path
            image_name = getattr(active_cfg, "code_exec_docker_image", "sovereign-code-sandbox:latest")
            memory_mb = getattr(active_cfg, "code_exec_docker_memory_mb", 512)
            cpus = getattr(active_cfg, "code_exec_docker_cpus", 1.0)
            pids_limit = getattr(active_cfg, "code_exec_docker_pids_limit", 64)

            # Pre-flight availability check: fail closed without silent fallback
            if not check_docker_daemon_available():
                raise RuntimeError(
                    "Docker container isolation is configured, but Docker daemon is unreachable or stopped. "
                    "Air-gap security policy prevents fallback to subprocess. Execution refused."
                )
            if not check_docker_image_available(image_name):
                raise RuntimeError(
                    f"Docker container isolation is configured, but local sovereign image '{image_name}' "
                    "is not found in local Docker storage. Air-gap security policy strictly prevents automatic image pulling. "
                    "Execution refused."
                )

            raw_res = await asyncio.to_thread(
                _run_docker_worker,
                code=code,
                image_name=image_name,
                timeout=timeout,
                memory_mb=memory_mb,
                cpus=cpus,
                pids_limit=pids_limit,
            )
        else:
            # Hardened local subprocess execution path
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

            env = get_sanitized_subprocess_env(sandbox_resolved)

            try:
                raw_res = await asyncio.to_thread(
                    _run_subprocess_worker,
                    tmp_path=tmp_path,
                    cwd=sandbox_resolved,
                    env=env,
                    timeout=timeout,
                )
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

        exit_code = raw_res.get("exit_code", 0)
        timed_out = raw_res.get("timed_out", False)
        output_limit_exceeded = raw_res.get("output_limit_exceeded", False)
        stdout_text = raw_res.get("stdout", "")
        stderr_text = raw_res.get("stderr", "")

        # Exit code classification & 0xC000013A handling
        exit_code_name = classify_exit_code(exit_code)
        exit_code_description = None
        if (exit_code & 0xFFFFFFFF) == 0xC000013A:
            exit_code_description = "Process terminated by console control event / Ctrl+C (STATUS_CONTROL_C_EXIT)"
        elif exit_code_name:
            exit_code_description = f"Process terminated abnormally ({exit_code_name})"

        # Evaluation of success
        is_ctrl_c = ((exit_code & 0xFFFFFFFF) == 0xC000013A)
        success = (exit_code == 0) and not timed_out and not output_limit_exceeded and not is_ctrl_c

        logger.info(
            "code_execution | mode=%s success=%s exit_code=%d timed_out=%s output_exceeded=%s stdout_len=%d",
            isolation_mode, success, exit_code, timed_out, output_limit_exceeded, len(stdout_text)
        )

        return {
            "success": success,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "output_limit_exceeded": output_limit_exceeded,
            "exit_code_description": exit_code_description,
            "isolation_mode": isolation_mode,
        }

    return execute_code_execution
