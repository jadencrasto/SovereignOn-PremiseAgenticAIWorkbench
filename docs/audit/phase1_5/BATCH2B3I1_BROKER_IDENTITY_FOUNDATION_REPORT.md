# Batch 2B.3I.1 — Broker identity and cleanup

Production remains **NO-GO**. Server authentication and process-handle binding remain unsupported; no server process inspector was added. There is no independently trusted expected process handle in the current client and native endpoint/process lifecycle behavior cannot be verified in the managed environment. Exposing an inspection-only API before its handle, PID reuse, disconnect and replacement races are validated would not establish a trustworthy binding. No PID, account name or pipe name is accepted as authentication.

## Changes and evidence

`prototype/windows_broker_dotnet/Program.cs` now routes the live peer-verification control flow through injectable scope, token acquisition, SID extraction, thread-token and process-identity checks. Both success and exceptions reach the cleanup check. The live path calls the same termination-decision helper as the offline tests; a failed cleanup decision returns from the server before a response or next accept. Tests inject the termination callback rather than terminating the test process. This checks control flow, not actual Windows token reversion, native exception behavior, or process-wide isolation.

The offline self-test executes ten synthetic cases through that same verifier: match, mismatch, missing token, token exception, SID exception, scope exception, cleanup failure after success, cleanup failure after exception, thread-check exception and changed process identity. Each negative denies; cleanup failures request termination. A false thread check models a residual token. The existing standalone SID and protocol checks still run. Tests do not independently instrument the asynchronous serve loop or exercise real Windows handles; this remains a verification limitation.

Idle connection cancellation is now treated as an expected event; it no longer consumes the routine diagnostic budget. Security-decision diagnostics have a separate bounded budget from routine and successful events, though sufficiently many security events can still exhaust their own budget and the runner's bounded capture can truncate later output. Client disconnect remains a sanitized read error and is not automatically hidden.

## Trust anchor direction

Recommend **a trusted launcher first for 2B.3I.2**, solely for isolated development verification, followed by a protected service identity for production architecture. The launcher must hold the process handle returned at creation; compare the connected endpoint's actual server process using `GetNamedPipeServerProcessId`, establish and retain a corresponding process handle, compare kernel process identity while both handles are alive, detect process exit and endpoint replacement across verification/use, and reject missing evidence. PID equality alone is vulnerable to reuse and does not prove executable identity. Same-account writable binaries/configuration, a launcher that can be replaced, squatting before creation, pre-existing handles and races defeat an unprotected launcher. Handle binding cannot authenticate an independently trusted binary unless installation/executable file identity and configuration are protected.

A separately provisioned restricted service identity with administrator-controlled executable, endpoint and storage ACLs can create a stronger independent authority, but requires explicit provisioning, service lifecycle, isolation from pre-existing writable handles, restricted writer access, and OS-level validation. A service SID alone does not authenticate a process or guarantee storage isolation. Neither design is implemented or asserted secure here. Existing development client remains unauthenticated and allows a connecting endpoint to impersonate it locally: use only isolated synthetic runs without sensitive data or credentials.

## Verification

* `dotnet build prototype/windows_broker_dotnet/Broker.csproj --no-restore`: PASS, exit 0, 0 errors, 8 CA1416 warnings.
* `dotnet prototype/windows_broker_dotnet/bin/Debug/net10.0/Broker.dll selftest`: PASS, exit 0, including the ten synthetic cases and existing protocol checks; the command reports one aggregate pass, not individual test-runner counts.
* PowerShell parser for `tests/backend/run_batch2b3h8_dotnet_ipc.ps1`: PASS, exit 0.
* Native pipe integration, token reversion, server process inspection, endpoint replacement, distinct SID and adversarial tests: NOT RUN by Codex. Prior operator functional results predate this refactor; run the existing bounded runner independently before claiming compatibility.

The pipe ACL, protocol, limited commands and production gates are unchanged. No VM, host policy, service, production storage or protected workflow was touched.
