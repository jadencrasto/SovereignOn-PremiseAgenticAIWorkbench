# Batch 2B.3H.6 — Native Windows IPC implementation status

Date: 2026-11-12. Branch `phase1-5-recovery-security`. **IMPLEMENTATION NOT READY; functional IPC not demonstrated; production NO-GO.** The requested usable broker, native integration runner and separate-process ping are not complete. The public development listener and client remain disabled because critical overlapped I/O and cancellation properties are unresolved. This respects the instruction not to enable a listener with an unresolved critical I/O property; it does not satisfy the implementation-phase acceptance criteria.

## Environment evidence and scope

The user supplied output from their ordinary PowerShell showing null AppContainer package SIDs, empty capabilities, matching parent/child user, logon and session, and medium integrity (`S-1-16-8192`). This is operator-reported evidence for those processes, distinct from the low-integrity AppContainer observed in the managed Codex environment. It makes an environment-specific explanation of the earlier error 5 plausible, but does not prove its exact cause or prove a future pipe connection will work. No native pipe integration test was run by Codex in this batch.

The requested interpreter `C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe` was verified and imports pywin32 312. The H through H.5 reports, draft, peer identity, protocol, candidate rights and working-tree changes were inspected. No VM, Chroma, Ollama, Docker, full application or full suite was started. No host ACL, firewall, account, production gate, `unused` directory, offline evidence or production database was changed. No commit, push, merge or deploy occurred.

## Changes and rights

- `prototype/windows_broker_ipc/dev_pipe.py`: changed the private unverified draft's user ACE and client requested access from generic read/write to the existing candidate `CLIENT_CANDIDATE=0x00100183`: `FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES | SYNCHRONIZE`. This omits `FILE_CREATE_PIPE_INSTANCE` (`0x4`), `WRITE_DAC`, `WRITE_OWNER` and generic masks. No OS effective-access test was performed. The draft still contains `PIPE_REJECT_REMOTE_CLIENTS` and first-instance flags, but these have not been validated with the new mask. The server is not an authenticated production authority; a client using the same user SID can be indistinguishable from another same-account process.
- `tests/backend/test_batch2b3h6_offline.py`: verifies an in-memory security descriptor contains exactly the candidate ACE, protocol response markings/denial and that public transport remains disabled. It never creates a pipe.
- `tests/backend/run_batch2b3h6_native_ipc.ps1`: **preflight-only**, not the requested functional IPC runner. It checks the designated interpreter/imports, invokes the existing read-only parent/child token diagnostic in bounded child processes, refuses AppContainer or different parent/child context and reports the transport blocked. It starts no broker and performs none of the ten requested IPC scenarios. It cannot be used to claim the implementation phase is ready. The PowerShell process launcher times out and kills only the diagnostic process it created; the token diagnostic itself starts its own short-lived child, so process-tree cleanup under an abnormal timeout still requires review.
- This report. These files do not integrate the draft with FastAPI or production storage.

## Unresolved lifecycle blockers

The existing private `_overlapped` code is not a verified state machine. Immediate completion byte counts versus pending completion are not established; writes do not verify partial counts; an unexpected exception or interrupt can close an event while I/O may still be pending; cancellation and completion-drain races are not proven; a connect that completes around timeout may be disconnected incorrectly; and message truncation, oversize responses, per-run endpoint naming, sequential disconnect/reconnect and bounded clean shutdown/restart are not implemented and verified as a coherent protocol. `CancelIo` plus an event wait and `os._exit` is not sufficient evidence of safe cancellation. No independent native execution was allowed in the managed environment to close those gaps, and no arbitrary workaround was introduced. The private `_serve_unverified_draft` and `_request_unverified_draft` remain unverified; do not call them directly.

The operator's instruction permits development-only server authentication to be weaker than production only for this milestone, but does not permit unsafe I/O or exposing production data. No implementation here supplies a trusted server identity, independent writer exclusion, durable storage, revocation or rollback protection. Even a future successful ping would establish only functional IPC.

## Verification

With the designated interpreter, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`:

```powershell
& 'C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe' -I tests/backend/test_batch2b3h6_offline.py -v
& 'C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe' -I tests/backend/test_batch2b3h_ipc_offline.py -v
```

Results: **9 passed, 0 failed, 0 skipped** (3 new in-memory descriptor/protocol/disabled-entry tests; 6 prior parser tests). These do not create an OS pipe or prove DACL enforcement. PowerShell parsed the runner with zero syntax errors. An attempt to launch the preflight-only script in the managed session was refused by PowerShell with `AuthorizationManager check failed` / `UnauthorizedAccess`; consequently the script's runtime behavior, quoting, timeout handling and operator-environment result are **not verified**. This was a PowerShell authorization failure, not evidence that Windows IPC itself was rejected or that an automatic approval review denied an action. No policy was changed to override it.

The native separate-process tests—ping, status, capabilities, denied/malformed/oversized requests, duplicate instance, stalled client, shutdown and restart—are **NOT RUN** by Codex, as requested. Their count is not a unit-test skip count. No native functional result is claimed.

## Operator commands and next step

The current script is only a blocked preflight; it is not a usable integration test. Once its PowerShell execution is permitted by the operator's existing policy, from the project root the intended invocation is:

```powershell
.\tests\backend\run_batch2b3h6_native_ipc.ps1
```

Expected after a successful interpreter/token preflight: `BLOCKED: bounded IPC transport is not implemented or verified; no broker was started.` It exits 2. This script has not been runtime-verified in either environment and should not be used to infer any IPC result. There are currently **no supported server/client commands** for a ping.

Next, implement and independently review a complete finite overlapped I/O state machine, exact cancellation/drain and buffer lifetimes, and a unique-endpoint test harness that starts and terminates only its own server and client processes. Exercise it in the operator's independently verified native PowerShell environment only after safety review, with synthetic data and no production integration. Then document actual results rather than marking a parser or preflight as functional IPC. **Production remains NO-GO.**
