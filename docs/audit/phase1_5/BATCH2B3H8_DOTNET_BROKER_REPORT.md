# Batch 2B.3H.8 — .NET broker prerequisite

Date: 2026-11-12. Branch `phase1-5-recovery-security`. **BLOCKED: no .NET SDK installed. Production NO-GO.** As requested, this batch stopped at the missing SDK prerequisite. No .NET broker, client, project, build or integration runner was created, and no functional IPC was demonstrated.

## Observed environment

`dotnet --info` reported Windows x64 (`win-x64`) host version 8.0.21 and **no installed SDKs**. `dotnet --list-sdks` returned no entries. `dotnet --list-runtimes` reported Microsoft.NETCore.App 6.0.16, 8.0.0 and 8.0.21; Microsoft.AspNetCore.App 8.0.0; and Microsoft.WindowsDesktop.App 8.0.0. These runtimes can execute suitable already-built applications but are not the .NET SDK needed to build the requested C# project. No `global.json` was found by `dotnet --info`.

## Prerequisite and remaining work

A suitable Windows x64 .NET SDK and its required target/reference packs must be made available to the development environment before the requested C# implementation can be compiled. One possible target for investigation is the .NET 8 SDK matching the installed runtime family, subject to checking its support lifecycle, Windows SDK/API compatibility, required reference packs and dependency availability before selecting a version. This is a prerequisite, not an instruction to modify this machine. No SDK or dependencies were automatically installed, and no existing Python prototype was deleted or enabled.

Once an appropriate SDK is available, implement and compile the requested development-only broker/client and bounded test runner, verify the exact Windows pipe ACL APIs against the chosen SDK, and have the operator run native integration tests in their independently verified environment. Neither an installed runtime nor a future build proves cancellation safety, client/server authentication, writer exclusion or production readiness.

## Tests and preservation

No broker code was created, no build or tests were run, and native IPC was not attempted. Test counts: **0 passed, 0 failed, 0 skipped; native integration not run**. Git status and the existing reports/protocol were inspected. Existing tracked and untracked work was preserved. No production gate, host account/ACL/firewall, VM, `unused` directory, production service, storage or database was modified. No commit, push, merge or deployment occurred. **Production remains NO-GO.**
