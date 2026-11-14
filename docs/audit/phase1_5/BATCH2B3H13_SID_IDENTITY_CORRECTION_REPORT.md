# Batch 2B.3H.13 — SID-based development peer verification

## Finding

The native result establishes that both account-name values were available but did not compare equal. It does **not** establish that the broker and client had different Windows principals: account-name representations can differ, and the old implementation compared names rather than the OS security identities behind them. The broker now compares Windows user SIDs from access tokens. Microsoft documents `WindowsIdentity.User` as the SID for the user and that a SID uniquely identifies the account or group. This removes account-name formatting as the comparison input. It does not prove the next native test will pass; that outcome remains operator verification.

## Code changes

`prototype/windows_broker_dotnet/Program.cs` now:

* Captures the broker process SID once, before accepting clients, with `WindowsIdentity.GetCurrent(false).User`. The same canonical SID is used for the pipe DACL and the expected identity.
* Reads the connected client's SID in a synchronous `NamedPipeServerStream.RunAsClient` delegate with `WindowsIdentity.GetCurrent(true).User`. There is no `await`, I/O, request handling, or escaping task inside that impersonation scope.
* Compares the two SID strings ordinally, ignoring case. The strings are produced by Windows APIs only; neither the JSON request nor a process ID participates in identity verification.
* Fails closed for unavailable expected/client SID, token-acquisition or impersonation failure, a mismatch, unexpected exceptions, a residual thread impersonation token after the delegate returns, or a changed process SID after the scope. A response remains `DENIED` on every failed check.
* Emits a bounded, content-free diagnostic: peer/expected SID availability, match state, cleanup state, and a fixed reason. It never prints a SID, user name, access token, DACL, or request content.

The PowerShell runner permits only the exact fixed diagnostic form. It keeps the restrictive DACL, `PIPE_REJECT_REMOTE_CLIENTS`, first-instance handling, bounded operations, fixed JSON protocol, and the three non-authorizing commands.

## Windows APIs and scope

`RunAsClient` calls a delegate while impersonating the connected client. Microsoft documents that on Windows only the thread executing that delegate is affected. The code deliberately keeps the delegate synchronous and minimal. After it returns, it queries `WindowsIdentity.GetCurrent(true)` for a residual thread identity and independently reads the process identity with `GetCurrent(false)`; either an unexpected residual token or process-SID change makes verification fail. This is a defense check, not proof that every native cleanup path has been independently validated. Native testing is still required.

The current client-side `TokenImpersonationLevel.Impersonation` remains unchanged. The broker does not authenticate itself to clients. A process that gains this development endpoint first may receive a client token capable of local impersonation. Therefore the runner must be used only in an isolated operator-controlled development environment and without sensitive data or credentials.

References: [WindowsIdentity.User](https://learn.microsoft.com/en-us/dotnet/api/system.security.principal.windowsidentity.user?view=net-10.0), [RunAsClient](https://learn.microsoft.com/en-us/dotnet/api/system.io.pipes.namedpipeserverstream.runasclient?view=net-10.0), and [Windows security identifiers](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-identifiers).

## Offline verification

* `dotnet build prototype/windows_broker_dotnet/Broker.csproj --no-restore`: **PASS**, exit 0, 0 errors, 8 CA1416 Windows-platform warnings.
* `dotnet prototype/windows_broker_dotnet/bin/Debug/net10.0/Broker.dll selftest`: **PASS**, exit 0. It covers matching SID, mismatched SID, missing client SID, missing expected SID, simulated token-acquisition exception, simulated cleanup exception, plus existing protocol checks. All negative cases deny.
* PowerShell runner parser: **PASS**, exit 0.
* PowerShell SID diagnostic allowlist: **PASS**, exit 0; the exact synthetic line passed and the same line with trailing arbitrary content was filtered.
* Native named-pipe integration and real Windows token/reversion behavior: **NOT RUN by Codex**. No native functional result is claimed.

## Operator test

Run only from ordinary operator-controlled Windows PowerShell, outside the managed environment:

```powershell
dotnet build .\prototype\windows_broker_dotnet\Broker.csproj --no-restore
if ($LASTEXITCODE -eq 0) {
    powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\backend\run_batch2b3h8_dotnet_ipc.ps1
}
```

Expected successful ping output includes `peer_sid_matches=true` and `reason=SID_MATCH`. Any other fixed reason must be treated as a denial and investigated. A successful native ping establishes development-only functional IPC. It does not establish server authentication, multi-user authorization, writer exclusion, storage isolation, or production readiness.

**Production release decision: NO-GO.**
