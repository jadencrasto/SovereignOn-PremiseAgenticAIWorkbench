# Batch 2B.3H.12 — Peer identity diagnostic

## Confirmed findings and remaining uncertainty

The operator reports real local IPC and a `DENIED` response even with the client configured for `TokenImpersonationLevel.Impersonation`. In the inspected server, `DENIED` follows a false result from a case-insensitive comparison of `GetImpersonationUserName()` with `WindowsIdentity.GetCurrent().Name`. It does not tell us whether the peer name was null, empty or nonmatching, or whether the self name was unavailable. The configured level is visible in source; the effective connected token and actual applied impersonation level were **not independently inspected**. Name-format differences are possible but unproven. A successful pipe exchange is functional IPC, not proof of correct identity or authorization.

The current server does not explicitly call `RunAsClient` during this check. `WindowsIdentity.GetCurrent()` is evaluated after `GetImpersonationUserName()` returns, but the server thread's effective identity during or after internal framework operations was **not measured**. There is insufficient evidence to say whether impersonation or reversion occurred internally or affected that name. Microsoft documents that `GetImpersonationUserName()` may return null before the client writes or without Impersonation-level connection; here the server reads a request before checking, and the client configuration requests Impersonation, but runtime token behavior remains unverified.

## Changes

`prototype/windows_broker_dotnet/Program.cs` now emits one content-free identity diagnostic after both names are obtained. It reports exactly the availability and equality booleans and one fixed reason: `PEER_UNAVAILABLE`, `SELF_UNAVAILABLE`, `IDENTITY_MISMATCH`, or `IDENTITY_MATCH`. If both are unavailable, the peer-unavailable reason takes precedence. Exceptions retain the existing sanitized failure handling and do not result in authorization. The diagnostic is covered by the existing per-process diagnostic cap. The decision still uses the same fail-closed comparator.

`tests/backend/run_batch2b3h8_dotnet_ipc.ps1` now allows only the exact fixed diagnostic format from the broker's captured stderr. It prints these lines after the broker is stopped, including on ping failure. Raw names, SIDs, tokens, descriptors, arbitrary error messages and client-controlled text are not printed by this diagnostic. No protocol response, pipe ACL, remote-client restriction, impersonation level, production gate or storage integration was changed.

## SID comparison review

An OS-backed SID from the connected client's token compared to an independently established expected SID avoids relying on account-name formatting; Microsoft identifies SIDs as the Windows security principal identifiers used in tokens. A future design could capture an expected process identity outside impersonation, inspect the connected client token inside a strictly synchronous scoped operation, reject missing tokens and all errors, and verify reversion even on exceptions. `RunAsClient` executes a delegate under the client identity on the executing Windows thread, so async work must not escape that scope. Cleanup and reversion guarantees, privilege behavior, server-thread identity, and real token acquisition require a dedicated Windows review and adversarial native tests before replacing the current check. No SID comparison or new impersonation operation is implemented in this batch. Even correct same-account SID comparison would not authenticate the server or distinguish malicious processes under that same account.

References: [GetImpersonationUserName](https://learn.microsoft.com/en-us/dotnet/api/system.io.pipes.namedpipeserverstream.getimpersonationusername?view=net-10.0), [RunAsClient](https://learn.microsoft.com/en-us/dotnet/api/system.io.pipes.namedpipeserverstream.runasclient?view=net-10.0), and [Windows security identifiers](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-identifiers).

## Offline verification

* `dotnet build prototype/windows_broker_dotnet/Broker.csproj --no-restore`: **PASS**, exit 0, 0 errors, 6 CA1416 warnings.
* `dotnet prototype/windows_broker_dotnet/bin/Debug/net10.0/Broker.dll selftest`: **PASS**, exit 0; includes four synthetic diagnostic categories and existing comparator/protocol checks. These do not query Windows tokens.
* PowerShell parser and allowlist smoke check: **PASS**, exit 0; an exact synthetic diagnostic was printed, while an appended arbitrary value was filtered.
* Native Windows IPC, actual peer identity, effective token level and impersonation cleanup: **NOT RUN by Codex**.

## Operator rerun

The client currently permits its connected server to impersonate it locally, and does not authenticate that server. Run only in an isolated operator-controlled development environment without sensitive data or credentials.

```powershell
dotnet build .\prototype\windows_broker_dotnet\Broker.csproj --no-restore
if ($LASTEXITCODE -eq 0) { powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\backend\run_batch2b3h8_dotnet_ipc.ps1 }
```

Share the sanitized `broker peer_identity_available=...` line and response diagnostics. The exact reason for the earlier denial remains unresolved until the new output is observed. Production remains **NO-GO**.
