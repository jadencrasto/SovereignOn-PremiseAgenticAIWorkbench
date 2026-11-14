# Batch 2B.3I.2 — Development launcher inspection

**Status: partial; production NO-GO.** A development process-observation launcher is implemented, but secure server authentication, continuous binding and the full native test plan remain unsupported. Prior native broker success predates the latest verifier changes; current compatibility is not established.

## Implementation

`prototype/windows_broker_dotnet/Launcher.cs` adds an explicit `inspect-launch` invocation. It accepts no paths, commands or PIDs; it starts only the adjacent `Broker.exe` with a random development endpoint and a ten-second broker lifetime. It retains the .NET `Process.SafeHandle`, waits at most five seconds for READY, connects with Identification-level impersonation and a three-second deadline, and sends no protocol data. `GetNamedPipeServerProcessId` observes the connected server, while `GetProcessId` and `WaitForSingleObject` inspect the retained child handle before and after observation. Missing, changing, exited, or failed observations reject. The handle is owned by the .NET Process object and disposed with it. Whether .NET exposes the exact original CreateProcess handle or obtains another handle internally has not been established; the implementation does not claim that property.

This is **not authentication**: matching a PID while a child is live is evidence about a connection, not trust in the executable, launcher or configuration. The binary in the output directory may be writable by the same account. Handle lifetime, PID reuse, endpoint disconnect or replacement, and liveness races must be independently tested; no continuous binding to later messages exists. A same-account adversary can squat the endpoint before the broker or use its close/recreate gap. The existing broker creates and closes one first instance per client, so first-instance ownership is not continuous. A permanent anchor would require a reviewed multi-instance ACL design and was not implemented. The current application client still requests impersonation and lacks authenticated server identity; do not send secrets to it.

`Program.cs` exposes the separate manual `inspect-launch` and `launcher-selftest` modes. The existing broker protocol and permissions are unchanged. `tests/backend/run_batch2b3i2_launcher.ps1` runs only this inspection in a supervised process, requires PowerShell 7 for best-effort owned-tree termination, and has a 20-second deadline. The launcher itself attempts to kill only its own child on exit. Cleanup failures are reported categorically. A job object with kill-on-close would be needed for robust descendant cleanup if the launcher itself is terminated or the tree changes; process-tree killing is best-effort. Use only an isolated disposable environment and review the script before execution.

## Offline results

* `dotnet build prototype/windows_broker_dotnet/Broker.csproj --no-restore`: PASS, 0 errors, 9 CA1416 warnings.
* `dotnet prototype/windows_broker_dotnet/bin/Debug/net10.0/Broker.dll selftest`: PASS.
* `dotnet prototype/windows_broker_dotnet/bin/Debug/net10.0/Broker.dll launcher-selftest`: PASS; synthetic match, mismatch, absent/failed observation, early exit, changing endpoint observation and zero PID checks. These mocks do not prove Windows handle behavior. Explicit PID reuse, cleanup/disposal failure, invalid configuration, exit after full verification and OS handle-acquisition failure tests are not implemented.
* PowerShell parser: PASS on initial runner version; final small conditional edit not separately parsed.
* Native IPC, process binding, adversarial mismatch, duplicate, restart and launcher cleanup: NOT RUN by Codex. The new runner checks one observation only and does not exercise mismatched process, unexpected exit, restart or duplicate instances. It is not a complete acceptance test.

## Operator procedures

Only after safety review in an isolated environment, run from the repository root (PowerShell 7 required for the new runner):

```powershell
dotnet build .\prototype\windows_broker_dotnet\Broker.csproj --no-restore
pwsh -NoProfile -File .\tests\backend\run_batch2b3i2_launcher.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\backend\run_batch2b3h8_dotnet_ipc.ps1
```

No native result is claimed until operator output is reviewed. A protected service identity remains contingent on separately provisioned restricted identity, protected executable/configuration and storage roots, endpoint ACLs, robust handle and writer isolation, witness/rollback design and independent adversarial testing. Relevant Windows API references: [GetNamedPipeServerProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeserverprocessid), [process handles and identifiers](https://learn.microsoft.com/en-us/windows/win32/procthread/process-handles-and-identifiers).
