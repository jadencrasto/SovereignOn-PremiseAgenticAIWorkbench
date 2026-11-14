# Batch 2B.3H.11 — Development pipe peer identity

## Evidence and change

The operator observed a connected client receive valid `DENIED` JSON. This establishes a negative peer-identity decision in the existing server, but does not by itself establish whether the returned username was null or different. The old client specified `TokenImpersonationLevel.Identification`. Microsoft documents that `NamedPipeServerStream.GetImpersonationUserName()` returns null when the client has not written or did not connect with `Impersonation`; its published applicability includes .NET 10. The server reads the request before calling it. This documents an API contract mismatch and a plausible cause of the observed denial; the operator's output does not independently prove the exact returned value or demonstrate that this change resolves the denial.

The development client now requests `TokenImpersonationLevel.Impersonation`. The server still compares the OS-supplied username against its own name, case-insensitively. The comparison was extracted to a small function that explicitly rejects null, empty, or whitespace-only identities on either side. Mismatched identities remain denied; API exceptions remain fail-closed through the server's existing error handler. No identity values are logged. Files changed: `prototype/windows_broker_dotnet/Program.cs` and this report. The runner, endpoint ACL, remote-client rejection, first-instance flag, protocol and production security controls were not changed.

## Security implications

Microsoft documents that `Impersonation` permits a connected server to impersonate the client's security context on the local system. This is broader than identification. The current development client does **not authenticate the server**: first-instance behavior and a random endpoint are not proof of server identity. A malicious process that acquires the endpoint first could potentially impersonate the client locally. Use only an isolated operator-controlled development environment and synthetic requests; do not use sensitive credentials or data. This tradeoff is not acceptable as a production authentication design. Username comparison does not provide SID-level authorization, trusted broker identity, same-account process isolation, independent writer exclusion, or storage safety.

Sources: [GetImpersonationUserName, .NET 10](https://learn.microsoft.com/en-us/dotnet/api/system.io.pipes.namedpipeserverstream.getimpersonationusername?view=net-10.0); [TokenImpersonationLevel, .NET 10](https://learn.microsoft.com/en-us/dotnet/api/system.security.principal.tokenimpersonationlevel?view=net-10.0); [Windows named-pipe client impersonation](https://learn.microsoft.com/en-us/windows/win32/ipc/impersonating-a-named-pipe-client).

## Verification

* `dotnet build prototype/windows_broker_dotnet/Broker.csproj --no-restore`: **PASS**, exit 0, 0 errors and 6 CA1416 warnings.
* `dotnet prototype/windows_broker_dotnet/bin/Debug/net10.0/Broker.dll selftest`: **PASS**, exit 0. The offline self-test includes four synthetic identity decisions: unavailable peer rejected, empty identities rejected, mismatch rejected, and matching identities accepted case-insensitively, as well as the existing protocol checks. These do not exercise Windows authentication or pipes.
* Native pipe, real identity behavior, ping, and lifecycle tests: **NOT RUN by Codex**. They require independent operator execution outside the managed AppContainer. A successful test would demonstrate development functionality only.

## Operator test

After reviewing the security implications, from the repository root in an isolated operator-controlled Windows PowerShell:

```powershell
dotnet build .\prototype\windows_broker_dotnet\Broker.csproj --no-restore
if ($LASTEXITCODE -eq 0) { powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\backend\run_batch2b3h8_dotnet_ipc.ps1 }
```

The actual native test outcome remains unknown. Production release remains **NO-GO**.
