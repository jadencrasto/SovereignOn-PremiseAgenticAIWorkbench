# Batch 2B.3I.3 — Endpoint ownership and version validation

**Production: NO-GO.** Version handling is corrected. Continuous endpoint ownership is **not implemented or verified**; the broker still closes and recreates the pipe instance between clients.

## Version handling

The server emits integer JSON `version: 1`; its C# client validates it as an integer. The runner's diagnostic previously required `[int]` (`Int32`). Measured offline, Windows PowerShell 5.1 `ConvertFrom-Json` returns `System.Int32` for this value and PowerShell 7 returns `System.Int64`, so the latter displayed `version=invalid` despite a valid response. The former runner also used a coercive comparison for enforcement.

`tests/backend/run_batch2b3h8_dotnet_ipc.ps1` now requires exactly the six expected envelope properties, an `Int32` or `Int64` value equal to 1, boolean flags and outcome, and result/error types consistent with the outcome. Strings, null, floating point values, unsupported versions and extra/missing properties fail. PowerShell's JSON parser can collapse duplicate keys; the existing C# client independently rejects duplicate or extra keys before emitting the sanitized JSON, so the runner alone is not a duplicate-key validator.

`tests/backend/test_batch2b3i3_version.ps1` extracts the actual runner functions and uses synthetic JSON. It checks valid numeric 1, missing, string, unsupported, null, float, boolean, extra field and malformed JSON, plus the displayed version. No pipe or service is opened.

## Endpoint ownership and lifecycle

Current `CreateNamedPipe` sets first-instance and remote rejection, uses one maximum instance and an explicit DACL without `FILE_CREATE_PIPE_INSTANCE`. Disposing the sole server handle on each cycle leaves a turnover gap. A separate anchor and serving instance would require increasing the instance limit and ensuring the broker can create later instances while untrusted clients cannot. Microsoft documents that subsequent instance creation is access-checked for `FILE_CREATE_PIPE_INSTANCE`; changing the present same-account DACL risks granting that right to other same-account processes. First-instance creation does not authenticate the server.

Another possible design is to keep the original server instance and disconnect/reconnect it sequentially. Microsoft documents `DisconnectNamedPipe` before reconnecting; however the interaction among .NET's stream state, cancelled pending operations, client disconnect, partial data, shutdown and safe handle reuse has not been verified in this environment. Implementing that change without native lifecycle tests would risk weakening the working development transport. No anchor, ACL change or ownership guarantee is claimed. The existing launcher uses best-effort process-tree cleanup; a Windows job object with kill-on-close, race-free assignment and compatibility with enclosing jobs needs independent review and isolated tests before introduction. No lifecycle change was made.

References: [named-pipe instances](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-instances), [DisconnectNamedPipe](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-disconnectnamedpipe), [pipe access rights](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights), [CreateNamedPipe](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createnamedpipew).

## Verification

* `dotnet build prototype/windows_broker_dotnet/Broker.csproj --no-restore`: PASS, 0 errors, 0 warnings on this incremental build.
* `dotnet prototype/windows_broker_dotnet/bin/Debug/net10.0/Broker.dll selftest`: PASS.
* `powershell -NoProfile -ExecutionPolicy Bypass -File tests/backend/test_batch2b3i3_version.ps1`: PASS, 9 synthetic cases and display check.
* `pwsh -NoProfile -File tests/backend/test_batch2b3i3_version.ps1`: PASS, same cases.
* An initial Windows PowerShell invocation without the process-scoped execution-policy argument failed with `AuthorizationManager check failed`; no host policy was changed. Both successful scripts parsed the runner via the PowerShell parser.
* Native IPC, endpoint turnover/squatting, process-tree termination and security tests: NOT RUN by Codex. Operator's earlier successful functional results predate this change.

## Operator test preparation

After review, use an isolated disposable Windows environment and synthetic traffic. Run the existing bounded compatibility and launcher runners using their documented commands. They exercise functional commands, duplicate startup, disconnect, stall and restart but **do not establish endpoint ownership across turnover or attack resistance**. For a future anchor prototype, add a separate bounded supervisor that holds the same server handle across many disconnects, attempts a competing first-instance creation at each turnover, records categorical outcomes, tests shutdown/connect and cancellation races, kills only job-owned test processes, and checks restart. Test wrong and malformed versions via an isolated synthetic client, plus duplicate and squatter processes. Such native ownership tests are not yet implemented or run.

No server authentication, protected storage, production FastAPI integration or authorization was enabled. Same-account endpoint squatting and client impersonation exposure remain unresolved.
