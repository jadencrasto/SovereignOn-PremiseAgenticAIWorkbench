# Protected service-token probe foundation

**Status: BLOCKED for native use. Production NO-GO.** An offline policy and regression foundation has been added; it does not run a storage probe, inspect a Windows token, or prove the service token. The requested SCM-integrated native execution path remains unimplemented. No test approval or authorization gate was enabled.

## Files and implementation

- `prototype/windows_service_foundation/ServiceTokenProbe.cs`: a non-authorizing synthetic policy, fixed synthetic operation name, one-minute maximum simulated approval lifetime, bounded synthetic wait and fixed categorical output. `NativeExecution()` always returns `Unsupported`; it is not connected to SCM, a command-line service mode, filesystem operations or process launching.
- `prototype/windows_service_foundation/ServiceTokenProbeTests.cs`: 20 deterministic offline cases exercising that policy. Inputs and evidence are synthetic. A match is explicitly labelled `synthetic_match` and `authority=false`.
- `prototype/windows_service_foundation/Program.cs`: registers only the offline `service-token-probe-selftest` command. Existing service dispatch and Protected fixture are unchanged.

The synthetic `Approved` and `VmVerified` booleans are test inputs, not proof of a VM or approval. They must never be used as the trust anchor for native use. The simulated timeout bounds `Task.Delay`; it says nothing about cancellation or completion of a Windows filesystem call. No sensitive SID, account, path or token is emitted by this foundation.

## Token-correct design and remaining blocker

The existing K fixture executes inside the SCM-owned service process. Its startup checks the configured dedicated broker SID, excludes an administrators group, checks `IsTokenRestricted`, duplicates the token at identification level, and checks service-SID membership. The L/M inspector separately samples process-token identity, enabled/restricted SID groups, integrity and a forbidden-privilege set. The N storage probe requires the expected broker user, expected service SID in both enabled and restricted lists, no administrator group and no unsafe privileges. The independently pinned manifest supplies the expected service account, service SID, integrity, SCM configuration and installation identity. These observations are not interchangeable with an interactive `runas` token.

The intended narrow mechanism is a fixed, synchronous operation *inside the actual service process*, with no arbitrary executable or path selection. It must verify the process primary token and that the current thread is not impersonating, validate a separately pinned manifest and service configuration, verify the protected synthetic root and fixture identities, perform only reviewed bounded probe operations, and produce sanitized categorical evidence. SCM cancellation, timeout, outstanding native I/O and resource lifetime must be reviewed together; a managed timeout cannot forcibly cancel a blocked native call. The external observer must independently associate results with the intended service process, not trust a service-provided SID or PID.

The existing `StorageAccessProbe` is in a separate executable and depends on its manifest verification, Windows token inspector, protected-root handles and synthetic-journal helpers. Moving or sharing that code into the service and defining a trustworthy per-run VM approval and evidence channel require additional implementation and review. The existing K service flag by itself is not an independently verified VM boundary. Simply adding an SCM mode or launching an executable would not meet the requested fail-closed VM, identity and path requirements. This is an **unfinished implementation**, not a claim that Windows cannot support it. Until these dependencies are resolved, this change deliberately cannot invoke the native probe and does not close the readiness blocker.

## Expected ACL assertions for future native tests

| Identity | Expected scoped result; native status |
| --- | --- |
| Actual restricted broker service token | Permit reviewed synthetic store operations and sidecar creation; audit append only; no witness mutation or parent/ACL/owner mutation. **NOT RUN.** |
| Ordinary application user | Deny direct mutation, creation and deletion over fixtures and protected parents. **NOT RUN.** |
| Separately pinned alternate user | Deny direct mutation and unauthorized pipe access. **NOT RUN.** |
| Approved administrator/operator | Administrative setup and rollback only within separately granted scope. Administrators may possess bypass privileges; no general denial is assumed. **NOT RUN.** |

ACL denial must be distinguished from sharing, path and unrelated failures. Test actual restricted-token access, effective inherited ACLs, sidecar rights, reparse/parent substitution and pre-existing handles separately. The current fixture cannot demonstrate all of these.

## Host-side results

Executed on the existing .NET 10 SDK without registering or starting a service:

| Command | Result |
| --- | --- |
| `dotnet build .\prototype\windows_service_foundation\ServiceFoundation.csproj --no-restore` | PASS; 0 warnings, 0 errors. |
| `dotnet .\prototype\windows_service_foundation\bin\Debug\net10.0\ServiceFoundation.dll service-token-probe-selftest` | 20 passed, 0 failed. |
| Same executable, `protected-fixture-selftest` | 11 passed, 0 failed. |
| Same executable, `scm-selftest` | 33 passed, 0 failed; ABI layout PASS. |
| Same executable, `selftest` | PASS. |

The new cases cover token-policy match and mismatch, both service SID lists, administrator and privilege rejection, thread impersonation, integrity, approval and VM evidence, expiration, operation allowlist, sanitized output, synthetic timeout/cancellation and disabled native mode. They do not exercise OS token acquisition, actual ACLs, SCM integration, VM identity, probe invocation or native cancellation. No native SCM, token or storage test was run.

## Native prerequisites and approvals

Before any native test, independently review and approve a specific disposable VM, identity and isolation, a separate recoverable pre-provisioning snapshot, evidence retention, and a guest-only provisioning and rollback procedure. Provision the dedicated nonadministrator service account, restricted service SID, expected integrity/privileges, service logon right, protected binaries/configuration, narrowly scoped SCM and NTFS ACLs, distinct ordinary identities, and the independent manifest and digest. Verify cross-account inspection permissions without broadening them. Lifecycle's LocalService fixture uses the same fixed service name, so its configuration and approval cannot be reused as Protected evidence. Preserve the existing snapshots and avoid overwriting the Lifecycle workspace.

Windows 10 Home's exact service-logon configuration, token inspection rights and ACL tooling remain unverified in the actual VM. Missing administrative UI does not itself demonstrate an unavailable Windows API; if the approved security requirements cannot be met, use a separately approved compatible disposable VM rather than weakening them. Reassess the package signature, artifact pins and manifest before staging. If a native run becomes authorized, preserve sanitized results before reverting its approved snapshot; treat incomplete cleanup or uncertain restoration as requiring reversion.

Native identity/ACL results, independent witness and organizational verification remain outstanding. Egress calibration remains separately BLOCKED; no network-sensitive application tests are authorized. Production remains **NO-GO**.
