# Batch 2B.3H.9 — .NET named-pipe diagnostic

## Status and release decision

The cause of the operator's `Ping/readiness failed` is **unresolved**. The previous runner discarded readiness exceptions and the server suppressed exceptions, so that output cannot identify a failing Windows API, access check, or stage. Native IPC was **NOT RUN by Codex**, as instructed. Functional IPC has not been demonstrated by this batch. Production release remains **NO-GO**.

## Confirmed defects and changes

* The readiness loop suppressed client failures and never observed the server's READY signal. The runner now waits up to seven seconds for READY and reports broker startup/readiness, premature exit and exit code, plus client exit code and sanitized diagnostics.
* The broker swallowed per-connection exceptions. It now reports a fixed stage, category and numeric Windows error or HRESULT. It never prints exception text, SID, account names, or request contents. Diagnostics are capped at 32 messages per process.
* Waiting for a child to exit before draining redirected output could deadlock. A small in-runner asynchronous capture helper drains both streams concurrently, retains at most 4096 characters per stream, and limits the first readiness line. Only allowlisted diagnostic lines are printed. Process waits and cleanup remain finite and target only the processes the runner starts.
* The server previously used one 2.5-second deadline across connect, read and write; a slow connection could consume the request budget. Each stage now has its own 2.5-second deadline linked to the independently bounded server lifetime. The client similarly has separate connect, write and read deadlines. Cancellation tokens and stream disposal alone are not evidence that every native cancellation completes safely; operator testing and further review remain necessary.

Files changed: `prototype/windows_broker_dotnet/Program.cs`, `tests/backend/run_batch2b3h8_dotnet_ipc.ps1`, and this report. No DACL, access mask, peer verification requirement, production gate, storage integration or supported command was relaxed.

## Connection review and remaining hypotheses

The server calls `CreateNamedPipe` with duplex, overlapped and first-instance flags, one instance, `PIPE_REJECT_REMOTE_CLIENTS`, and an explicit DACL for the current user's SID with mask `0x00100183`. The client requests that same mask through the rights-based `NamedPipeClientStream` constructor and specifies `TokenImpersonationLevel.Identification`. The server wraps the native handle in `NamedPipeServerStream`, waits for connection, reads a frame, calls `GetImpersonationUserName()` and compares the resulting name with its own account name before responding. This is a development same-account check, not authenticated server identity or isolation against same-account processes.

Potential causes include an access check or access-right mapping failure, handle wrapping or client-constructor behavior, impersonation/name lookup failure, a process startup/readiness race, the original shared deadline, or a gap as the single pipe instance is closed and recreated. None is established as the ping failure. The exact effect of the DACL on native server creation and the client's requested access, and the compatibility of Identification impersonation with the peer-name operation in the operator's environment, require observed diagnostic codes. The first-instance flag is not server authentication and the gap between instances is not continuous ownership. No speculative permission change was made.

The READY signal establishes only that `CreateNamedPipe` and wrapping returned; it does not prove a client can connect. The runner provides stage categories such as `server_create`, `client_connect`, `server_read`, `peer_verification`, `server_write`, `client_write`, `client_read`, and `response_validation`. Win32 errors are reported as native codes when available; other exceptions report HRESULTs, which must not be misread as native error codes.

## Verification

* `dotnet build prototype/windows_broker_dotnet/Broker.csproj --no-restore`: **PASS**, exit 0, 0 errors, 6 CA1416 Windows-platform warnings.
* `dotnet prototype/windows_broker_dotnet/bin/Debug/net10.0/Broker.dll selftest`: **PASS**, exit 0; offline protocol self-test checks ping, unsupported command and duplicate field handling. It does not open a pipe.
* PowerShell parser and synthetic bounded capture test: **PASS**, zero parser errors and bounded capture retained the expected first three characters.
* Native named-pipe integration, peer verification, duplicate ownership, cancellation, stalled client and restart: **NOT RUN by Codex**. No claim is made about the operator's next run.

## Operator command

From an ordinary, operator-controlled Windows PowerShell in the repository root, after reviewing the script:

```powershell
dotnet build .\prototype\windows_broker_dotnet\Broker.csproj --no-restore
if ($LASTEXITCODE -eq 0) { powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\backend\run_batch2b3h8_dotnet_ipc.ps1 }
```

The test uses a random development pipe, synthetic requests and child processes it owns; it does not access production storage. The server has a maximum 60-second lifetime and the runner waits for normal expiry before restart. Share the sanitized output to identify the observed failing stage. The script does not authenticate the server, establish a protected storage authority, or validate isolation from another process under the same account. Even a successful ping is only functional IPC evidence.
