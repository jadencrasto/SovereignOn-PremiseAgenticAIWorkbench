# Batch 2B.3H.8 — .NET development broker implementation

Date: 2026-11-12. Branch `phase1-5-recovery-security`. **Development source compiles; native IPC NOT YET VERIFIED; production NO-GO.** A .NET server/client and operator-run test script exist. The operator must independently run the native test; a build or offline protocol self-test is not a successful IPC exchange or proof of security.

## Components and environment

Observed .NET SDK `10.0.404` and local .NET 10 reference packs on Windows x64. Added `prototype/windows_broker_dotnet/Broker.csproj`, `Program.cs` and `NuGet.Config`; the latter clears external package sources for this project. The project targets `net10.0` and has no third-party packages. The existing Python prototype remains disabled and unchanged. Nothing is connected to FastAPI, storage, tasks, artifact reads or code execution. No VM, Chroma, Ollama, Docker or full suite was started; no commit, push, merge or deployment occurred.

`Program.cs` supplies manual `serve` and `client` modes plus an offline protocol `selftest`. Names must match `SovereignAI-dev-` followed by 32 lowercase hex digits. Supported protocol commands are `ping`, `status` and `capabilities`; other commands are denied. JSON frames are newline-delimited, requests limited to 1,024 bytes and responses to 2,048 bytes. Duplicate/unknown fields, bad version and malformed JSON are rejected. Responses indicate `development_only: true` and `trusted_broker: false`. No command dispatches storage, files, SQL, prompts or execution.

## Endpoint and lifecycle

The server invokes Windows `CreateNamedPipe` through P/Invoke with `PIPE_REJECT_REMOTE_CLIENTS`, first-instance and overlapped flags, one instance and an explicit SDDL DACL for the current Windows user SID. The ACE and client constructor request specific mask `0x00100183`, excluding the `FILE_CREATE_PIPE_INSTANCE` bit; the current user is obtained from the server process, not the request. The connected peer name is obtained from Windows pipe impersonation and compared with the server account name. This is a same-account development check, not a robust SID-based multi-user authorization system. The client does **not** authenticate the server, and same-account attackers or pipe-name squatters remain in scope as unmitigated risks. No sensitive information should be sent.

The Windows handle is wrapped by `NamedPipeServerStream`; the client uses `NamedPipeClientStream` with specific `PipeAccessRights`. Both use async connect/read/write and cancellation tokens, with a 2.5-second per-operation deadline. The server has a requested finite 1–60 second lifetime and disposes each instance. The runner supplies independent process deadlines and kills only child processes it created if they fail to exit. Cancellation tokens are not proof that all underlying Windows operations terminate or that disposal and all races are safe. Server instance recreation between clients may introduce a gap or race in first-instance ownership. Endpoint ACL behavior, impersonation, cancellation, disconnect, restart, remote rejection and duplicate-instance behavior are unverified until native tests; there has been no independent security review. A successful ping would not prove those properties or authorize production use.

## Build and operator test

From the repository root in the operator's normal Windows PowerShell, after reviewing the source and test script:

```powershell
$env:DOTNET_GENERATE_ASPNET_CERTIFICATE = 'false'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
dotnet build .\prototype\windows_broker_dotnet\Broker.csproj --configfile .\prototype\windows_broker_dotnet\NuGet.Config
.\tests\backend\run_batch2b3h8_dotnet_ipc.ps1
```

The runner expects the default `bin\Debug\net10.0\Broker.dll` output. It creates a random development pipe name, starts the new server, runs independent clients with deadlines, checks ping/status/capabilities, unknown command, malformed/oversized input, duplicate instance, disconnect/stall, timed server exit and restart, and attempts cleanup of its own processes. It is syntax-checked but **has not been run**; startup timing, PowerShell version compatibility, endpoint rights and cleanup remain unverified. It uses finite process waits, but .NET/OS cancellation safety is not established by source inspection. If any step fails, preserve the sanitized output and report it rather than adjusting access rights blindly. No operator action is required until the user elects to test.

## Build, offline tests and side effect

The final source compiled using SDK 10.0.404 with the local source-cleared NuGet configuration, temporary output/intermediate paths and `DOTNET_GENERATE_ASPNET_CERTIFICATE=false`: **build succeeded, 0 errors, 6 CA1416 platform warnings**. `dotnet Broker.dll selftest` passed one offline protocol check covering ping, unsupported command and duplicate JSON property; it creates no pipe. PowerShell's parser accepted the runner syntax. These are not native IPC tests. Native integration: **NOT RUN**, as instructed. Full-suite results are unknown.

The first successful build in the managed environment occurred before certificate generation was disabled and .NET first-run output reported **“Installed an ASP.NET Core HTTPS development certificate.”** No certificate trust command was run. Whether that certificate is visible outside the managed environment or what certificate-store entry was affected was not verified; this was an unintended potential host-security side effect and must not be treated as no change. It was not removed because doing so without identifying the exact entry could affect an existing certificate. Subsequent builds set `DOTNET_GENERATE_ASPNET_CERTIFICATE=false`. An earlier build attempt using `C:\workspace` for CLI home failed with access denied; the temporary-directory build succeeded. The SDK also reported a workload-verification issue and inability to add the temporary tools directory to PATH; neither prevented the build.

## Next steps

Review the code and operator runner, then separately execute the native test from the operator's known non-AppContainer environment. Diagnose any failure without broadening the ACL. Independently review Windows DACL identity, pipe-instance race, async cancellation, handle cleanup, and a real broker/server trust model before considering any protected content or production integration. **Production remains NO-GO.**
