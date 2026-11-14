# Batch 2B.3H — Windows broker IPC foundation

Date: 2026-11-12. Branch `phase1-5-recovery-security`. **Milestone PARTIALLY IMPLEMENTED; production NO-GO.** No separate-process broker or authenticated IPC connection was established. A Python process running the entry point terminates immediately with an unavailable status; it is not a running broker. The fail-closed fallback is required because the endpoint security, client identity, asynchronous timeout and exclusivity properties could not be implemented and verified to the requested standard in this environment. No production authorization or persistence gate was modified.

## Inspection and mechanism decision

Reviewed the 2B.3F/3G designs, release gate, simulator, working-tree status and relevant source and dependency references. The available isolated Python reports Windows, Python 3.13.7 and no importable `win32pipe` or `win32security` modules. This does **not** mean Windows APIs are inherently unavailable: a carefully reviewed native extension, ctypes implementation or separately provisioned binding may be possible. No package was installed and no OS identity or descriptor was changed.

Windows named pipes are the preferred candidate because they can be local-only and Windows can provide access tokens and security descriptors. They are not enabled in this batch. A safe implementation still needs a reviewed, version-specific design and OS tests for a restrictive pipe DACL; `PIPE_REJECT_REMOTE_CLIENTS`; first-instance exclusivity and namespace squatting; an explicitly configured allowed SID policy; peer authentication using the connected client's token; failure-safe impersonation and reversion; bounded framing; overlapped connect/read/write with timeout, cancellation and shutdown; handle lifetime and simultaneous client behavior; and client verification of the intended broker. The exact DACL/SID policy is not implemented or asserted here. An allowed SID must be provisioned by trusted configuration rather than taken from the request; same-user SID alone may not distinguish an untrusted process running as that same user. A pipe client can influence impersonation level, and an impersonation failure must result in denial.

Neither Python `multiprocessing` authentication/pickle nor a loopback socket, client-supplied PID/name/role, PID file or unauthenticated shared secret is substituted. The lack of a verified network boundary and separately controlled test identity also precludes claims about hostile clients or egress. Microsoft documents that synchronous pipe operations can block indefinitely and that overlapped I/O needs proper handle/event management; a nominal timeout is not sufficient proof of cancellation safety.

## Files and contracts

- `prototype/windows_broker_ipc/protocol.py`: strict UTF-8 JSON parser for exactly `{"version":1,"command":"..."}`, 1,024-byte request limit, duplicate-key rejection, no arbitrary fields or deserialization. Only `status`, `capabilities` and `ping` parse; all other strings return a typed unsupported-command error. The parser is not an endpoint or an authorization decision. Response parsing accepts only the constant unavailable response, capped at 2,048 bytes.
- `prototype/windows_broker_ipc/transport.py`: `UnavailableTransport` never creates a pipe, socket or file and refuses to serve.
- `prototype/windows_broker_ipc/client.py`: client facade returns `TRANSPORT_UNAVAILABLE`; no connection or server authentication occurs.
- `prototype/windows_broker_ipc/server.py`: manually invoked entry point prints a content-free structured `startup_refused` diagnostic and exits 2 immediately. It does not launch or listen.
- `prototype/windows_broker_ipc/__init__.py` and `tests/backend/test_batch2b3h_ipc_offline.py`: isolated package marker and standard-library protocol tests.
- This report. Existing in-memory simulator remains separate and is not exposed via IPC.

The parser refuses `begin`, `finish`, `interrupt`, ingestion, retrieval, file/index mutation, recovery, authorization, artifacts, task resume, code execution and every other unrecognized command. There is no live endpoint to issue even the three candidate commands, and no success response is accepted. Structured protocol denials are tested in-process only; they are not sent to external clients.

## Authentication, authorization and lifecycle status

**Unsupported/unverified:** OS-backed client authentication, restrictive endpoint DACL, server identity, per-command authorization, request timeout, cancellation, concurrent clients, broker ownership, duplicate-instance refusal, endpoint cleanup, restart and active-request shutdown. Since no endpoint is opened, there are no stale handles or endpoints created by this implementation, but this is not a verified real lifecycle implementation. Unauthorized-client rejection under a genuinely distinct OS identity was **not run**, not simulated as proof. There is no production storage initialization, SQLite schema change, FastAPI integration or privilege escalation.

## Verification

Commands, with external pytest plugin autoload disabled for the test scripts:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -I tests/backend/test_batch2b3h_ipc_offline.py -v
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -I tests/backend/test_batch2b3g_broker.py -v
python -I prototype/windows_broker_ipc/server.py
```

Results: **18 passed, 0 failed, 0 skipped** (6 new parser/unavailable-transport tests, 12 existing in-memory simulator tests). The standalone server entry point printed `startup_refused` and the Python exit code was **2**, as designed. These do not test IPC. An initial PowerShell tool invocation reported shell exit 1 even though the entry point calls `sys.exit(2)`; a second invocation printed `$LASTEXITCODE` and explicitly propagated it, confirming Python exit 2. `git diff --check` exited 0 with existing line-ending warnings and does not examine the newly untracked files.

**Not run:** all 8 requested OS integration scenarios: (1) server/client IPC in separate processes, (2) authenticated ping, (3) unsupported-command response over IPC, (4) malformed/oversized requests over IPC, (5) unauthorized client under a genuinely distinct identity, (6) restart/endpoint cleanup, (7) duplicate instance, and (8) no-production-storage-access verification of a running broker. No running broker exists to test. The complete suite was not collected or run; its not-run count is unknown. Standard-library tests did not initialize Chroma, Ollama, Docker, the VM or the full application. No OS-level network isolation is asserted.

## Safe manual invocation

From the repository root, without elevation:

```powershell
python -I prototype/windows_broker_ipc/server.py
```

Expected: the structured `startup_refused` message and exit code 2. There is no listening service to connect to or shut down; running the command does not start a broker. The source is intentionally disabled rather than asking an operator to run an unverified pipe server. No service installation or host-security changes are required.

## Remaining work and decision

Next milestone: obtain a reviewed implementation dependency or native binding and a controlled, independently isolated Windows test environment with a separately provisioned test identity. Specify exact pipe name namespace, DACL, allowed SID and same-user threat model, authenticated server identity, first-instance behavior, bounded framing, overlapped cancellation and handle cleanup before enabling a listener. Conduct real separate-process and distinct-identity adversarial tests, including endpoint squatting and shutdown races, and independently review the results. The stronger storage, anti-rollback, source identity, revocation and authorization requirements from 2B.3F remain open even after a future IPC connection works. **Production remains NO-GO.**

References: [Microsoft named-pipe security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights), [client impersonation](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-impersonatenamedpipeclient), [CreateNamedPipe](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createnamedpipew), and [overlapped pipe I/O](https://learn.microsoft.com/en-us/windows/win32/ipc/synchronous-and-overlapped-input-and-output).
