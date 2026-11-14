# Batch 2B.3H.1 — Windows named-pipe broker

Date: 2026-11-12. Branch `phase1-5-recovery-security`. **NOT COMPLETE. Production NO-GO.** No named-pipe server or client was implemented or started, and no genuine interprocess communication or OS-backed authentication was demonstrated. The existing server and transport remain fail-closed. This batch adds a dependency decision and read-only preflight only; it does not fulfill the requested real broker acceptance criteria.

## Inspection and binding decision

The 2B.3H and 2B.3F reports, current IPC source, test, dependency declarations and working-tree state were inspected. The active isolated interpreter reports Windows CPython 3.13.7 AMD64; `find_spec` found none of `win32pipe`, `win32security`, `win32file` or `pywintypes`. Neither existing `requirements.txt` nor `pyproject.toml` specifies pywin32. No dependencies were installed or system configuration changed.

An established binding is preferable to hand-written ctypes signatures and native structure/handle management, provided its exact APIs and supported behavior are independently reviewed. `pywin32==312` is a **candidate development pin**, not a tested or security-approved dependency. PyPI lists `pywin32-312-cp313-cp313-win_amd64.whl`, matching the observed interpreter ABI and architecture; wheel availability does not prove its integrity in this environment, API coverage, secure use or compatibility with the rest of the project. The existing environment lacks this binding. Native Windows APIs via a reviewed extension or ctypes remain alternatives, but they require equivalent handle, security descriptor, cancellation and platform review. Merely adding the binding would not resolve the remaining identity and lifecycle design issues.

Added `prototype/windows_broker_ipc/requirements-ipc-dev.txt` as an optional, separately provisioned candidate pin. It is not included in production requirements and is not installed automatically. Added `preflight.py` to inspect module locations and package metadata without importing native pywin32 modules. It reports missing components, version match and Windows platform separately from `transport_available=False`; even a matching binding cannot enable transport. This preflight is diagnostic and its metadata lookup is not artifact verification or an IPC implementation.

## Endpoint, identity and authorization design

Named pipes remain the candidate transport. A future server must create a local-only pipe with `PIPE_REJECT_REMOTE_CLIENTS`, a restrictive explicit DACL and verified first-instance behavior. A trusted deployment policy must specify allowed client SIDs; no SID, username, PID, role or flag supplied in a request is authority. After receiving data, server-side token inspection/impersonation must verify the client, reject absent/anonymous/insufficient tokens and reliably revert on every path. `FILE_FLAG_FIRST_PIPE_INSTANCE` can reject a second creator, but a name squatter can create the first instance; this is a denial-of-service and server-authentication concern, not a verified ownership guarantee. The default pipe security descriptor is not accepted as an equivalent to a reviewed explicit DACL.

The client must independently verify the intended server before trusting its responses. `GetNamedPipeServerProcessId` gives a PID, not an authenticated broker identity by itself; PID reuse, process token, image/service identity, pipe namespace and the same-user attacker must be addressed in a reviewed trust model. There is currently no accepted design proving those properties under the requested no-service-install/no-host-ACL-change constraints. Same-user SIDs cannot distinguish arbitrary untrusted programs running as the same account. Explicit verified endpoint/identity policy, server authentication, bounded overlapped I/O, timeout/cancellation, safe handle ownership and cleanup, concurrent client sequencing and shutdown behavior remain unresolved. No reviewed executable named-pipe adapter was written: implementing only part of these controls or selecting an unverified ctypes/pywin32 call sequence would create a misleading security boundary. The disabled transport cannot be toggled on by installing the candidate package.

The existing protocol parser accepts only syntactically valid `ping`, `status` and `capabilities`, rejects extra fields and all other commands, and imposes a 1,024-byte maximum. These are in-process parsing properties, not authenticated IPC. No network listener, file path, SQL, Python deserialization or application route was added.

## Files added

- `prototype/windows_broker_ipc/requirements-ipc-dev.txt`: optional candidate pin, Windows-only marker.
- `prototype/windows_broker_ipc/preflight.py`: read-only diagnostic with explicit unavailable security capabilities.
- `tests/backend/test_batch2b3h1_preflight.py`: mocked, offline dependency-detection checks.
- This report. No existing production code, simulator, authorization gate, ACL, firewall, system account, storage root, `unused` directory, VM or offline evidence was changed. No commit, push or merge occurred.

## Testing and results

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -I tests/backend/test_batch2b3h1_preflight.py -v
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -I tests/backend/test_batch2b3h_ipc_offline.py -v
```

**9 passed, 0 failed, 0 skipped**: 3 preflight tests and 6 existing protocol tests. Tests use only Python's standard library and mocks. The matching-binding case is a synthetic metadata fixture and explicitly confirms the transport remains unavailable. `git diff --check` exited 0 with existing line-ending warnings; it does not inspect these untracked files. No real Chroma, Ollama, Docker, VM or full suite was run. No OS-level network isolation is asserted.

**OS integration: 0 run; 10 requested scenarios not run:** real separate-process connection, authorized ping, unauthorized SID rejection, endpoint squatting, duplicate instance, malformed/oversized messages over a pipe, interrupted client, shutdown/restart, timeout/cancellation, and verification that a running broker does not access production storage. No pipe broker exists to test; no distinct independently approved OS identity or isolated adversarial environment was available. No tests were skipped by the executed test runner; the ten scenarios are unexecuted, not simulated passes.

## Next steps

Review the candidate pin and acquire a verified wheel into a separately provisioned, isolated test environment without changing the host or global Python. Specify mutually authenticated identity and endpoint policy for that environment, then implement the complete local-only pipe and cancellation lifecycle behind a disabled-by-default boundary. Independently test the ten scenarios using a real distinct identity and preserve evidence before considering any production integration. No security gate should be opened because of parser tests or a matching dependency. **Production remains NO-GO.**

References: [pywin32 upstream](https://github.com/mhammond/pywin32), [pywin32 312 wheel metadata](https://pypi.org/project/pywin32/312/), [CreateNamedPipeW](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createnamedpipew), [ImpersonateNamedPipeClient](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-impersonatenamedpipeclient), and [GetNamedPipeServerProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeserverprocessid).
