# Batch 2B.3J — Protected broker service foundation

**Production NO-GO.** This is a partial foundation, not a running Windows service, trusted endpoint, storage boundary or production authority. The prior development broker and launcher were not modified. The installed .NET 10 reference packs did not contain `System.ServiceProcess.ServiceController.dll` or `Microsoft.Extensions.Hosting.WindowsServices.dll`; no dependency was installed or downloaded. This inspection does not prove that no other installed SDK component could support a service: native SCM APIs or independently provisioned packages remain alternatives requiring separate review.

## Implemented files and verification

`prototype/windows_service_foundation/ServiceFoundation.csproj`, `NuGet.Config` (empty package sources), and `Program.cs` provide a standalone lifecycle and deny-only evidence contract. `selftest` runs synthetic state/denial cases; all other invocations exit unsupported. It never registers with SCM, creates a pipe, opens a database, reads documents or authorizes an operation. Startup rejects missing/invalid configuration or evidence and still rejects synthetic complete evidence; stop is a synchronous state transition. No real service startup, SCM stop callback, cancellation or operation deadline exists yet. The foundation should not be mistaken for a functional service host.

* `dotnet build prototype/windows_service_foundation/ServiceFoundation.csproj --configfile prototype/windows_service_foundation/NuGet.Config`: PASS, 0 errors, 0 warnings, with package sources cleared.
* `dotnet prototype/windows_service_foundation/bin/Debug/net10.0/ServiceFoundation.dll selftest`: PASS, exit 0. Synthetic checks cover invalid/missing evidence, complete synthetic evidence still denied, repeated startup, stop, and operation denial. They do not verify OS identity, asynchronous cancellation, service failures, or actual storage policy.
* Explicit `run` invocation returned `SERVICE_FOUNDATION category=unsupported development_only=true` and failed; execution shell reported exit 1. The program requests exit code 2.
* Existing H.8 PowerShell runner parser: PASS. Native service, IPC adversarial tests and service-control integration: NOT RUN. No host service or security setting was changed.

## Boundaries and future server authentication

The application must run with no write access to broker-owned roots. A separately provisioned restricted broker identity must own or have narrowly delegated access to those roots, validate callers and authorize individual requests. The OS, not a Python mutex or application claim, must enforce restrictions against independent writers. The service logon account, restricted service SID, actual process token, protected executable/configuration, pipe endpoint owner and protected storage ACL are distinct evidence; none independently proves the rest.

Before sensitive data, a future client must independently establish protected SCM configuration and expected service identity, verify the actual connected pipe server process with a retained process handle and liveness checks (including PID reuse and replacement), validate protected executable file identity and configuration, check endpoint security and fail closed on any absent evidence. An endpoint name, claimed service name, PID, or same-account SID alone is insufficient. This sequence is a design, not implemented. Same-account attackers can alter unprotected binaries and squat endpoints; administrators and privileged token holders can bypass discretionary ACLs. Existing inherited/duplicated handles, memory mappings and backup privileges must be inventoried and excluded before claiming isolation. The current development client permits local impersonation by an endpoint and must not carry sensitive data.

## Future storage access matrix

| Resource | Application identity | Broker service identity | Other local users | Admin | Recovery identity |
|---|---|---|---|---|---|
| SQLite state | no direct access | controlled read/write | deny | privileged bypass | separately controlled backup/restore |
| Vector index | no direct access | controlled read/write | deny | privileged bypass | controlled backup/restore |
| Imported documents | no direct access | controlled read/write | deny | privileged bypass | controlled backup/restore |
| Generated artifacts | no direct access | controlled read/write | deny | privileged bypass | controlled backup/restore |
| Audit records | no direct writes | append via reviewed boundary | deny | privileged bypass | controlled export/restore |
| Independent freshness witness | no access | no ability to rewrite history | deny | privileged bypass unless external | separate authority |

This matrix is a goal, not observed enforcement. Protected roots must disable unsafe inherited grants and exclude application write/delete/ownership changes; effective permissions, inherited handles and privileged bypass need native verification. Writer exclusion must cover document files, SQLite, vectors, artifacts and audit state under one enforced broker protocol, including revocation and crashes; SQLite transactions alone cannot protect other stores. A witness must be independently protected against rollback and broker modification.

## Disposable-VM procedure for independent review — NOT AUTHORIZED TO EXECUTE

Obtain a separately approved disposable Windows VM, snapshot, isolated network boundary and synthetic-only data. The following are proposed operator actions, not completed steps. Identity names, protected root, ACL entries and installed binary hash must be independently selected and verified before provisioning; an executable SCM host does not exist yet. Do not copy these placeholders into a host shell.

1. Record baseline in the VM: `whoami /all` (token privileges), `sc.exe query type= service state= all` (service inventory), and `Get-Acl -LiteralPath '<synthetic-root>'` (existing ACL if present). Treat output as sensitive; do not publish identities.
2. After review, provision a dedicated restricted test identity and service SID using an approved VM-only identity procedure. Account creation and exact service configuration cannot safely be prescribed until the service host and identity model exist.
3. Provision a synthetic test directory and remove inherited grants only after reviewing ownership and recovery access. Example **template, not runnable as written**: `icacls '<synthetic-root>' /inheritance:r` removes inherited ACEs; `icacls '<synthetic-root>' /grant:r '<broker-identity>:(OI)(CI)M'` grants broker modify access. Independently add narrowly scoped admin/recovery access and verify application denial; a mistake can lock out recovery. No command is authorized on the host.
4. Protect installed binary/configuration and register a reviewed future SCM-compatible executable with explicit least privilege. Template `sc.exe create SovereignDevTest binPath= '<reviewed-service-executable>' start= demand` creates a service and changes VM configuration; do not run while the current host is unsupported. Configure and verify the restricted service SID and logon identity separately.
5. From a genuinely separate application identity attempt create/write/delete against synthetic files and expect denial; from the service identity perform permitted synthetic writes. Inspect actual effective access, inherited ACEs and pre-existing handles. Run distinct-SID, same-account squatter, fake-server and process-replacement tests with bounded owned process cleanup.
6. Once server authentication exists, test it with valid and invalid endpoint/process/file identity; kill the service during requests, verify bounded stop and restart and ensure no incomplete request authorizes content. The present foundation cannot perform these tests.
7. Revert the entire disposable VM to its pre-test snapshot, verify no service/account/storage survives, and preserve sanitized results separately. Reversion is preferred over attempting piecemeal host cleanup.

## Next milestone

Independently choose and provision an offline service-host dependency or review a narrowly scoped native SCM adapter; implement start/stop callbacks with finite shutdown, injected lifecycle tests and no endpoint/storage access. Then implement authenticated endpoint binding and verify distinct identities, before implementing any protected storage operation. Remaining blockers include service binary trust, caller authentication, endpoint ownership gap, writer bypass, telemetry isolation and rollback witness.
