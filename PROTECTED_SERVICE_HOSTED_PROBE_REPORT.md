# Service-hosted protected storage probe

**Decision: BLOCKED for native activation. Production remains NO-GO.** The service project now compiles the existing N storage-probe implementation and a fixed in-process component, but no SCM mode, public command, configuration or environment switch can invoke that component. Genuine service-token execution has **not** been demonstrated or enabled.

## Implementation

- `prototype/windows_service_foundation/ServiceFoundation.csproj` links the existing `StorageAccessProbe.cs` and the six security-foundation source files needed to compile its identity, token and filesystem dependencies. Both projects compile the same source; ACL-checking logic was not copied. The service binary now contains additional code, but the existing SCM worker and fixture startup selection are unchanged.
- `prototype/windows_service_foundation/ServiceHostedStorageProbe.cs` contains a private, currently unreachable Windows method intended to run synchronously in the current process. It rejects a thread impersonation identity, reads the process primary token through the existing inspector, checks the broker role, both service-SID group lists, privilege policy and expected integrity, and calls the shared `StorageAccessProbe.Run` with a fixed synthetic root and `exercise: false`. It does not start a child process. Its public-in-assembly dispatch always returns `Disabled` regardless of input; there is no activation switch. Its categorical evidence strings have fixed bounded size and `authority=false`.
- `prototype/windows_service_foundation/ServiceHostedStorageProbeTests.cs` tests disabled dispatch and exercises the actual shared probe's role and ACL expectation helpers using synthetic token data. `Program.cs` adds only the offline `service-hosted-probe-selftest` selector.

The private method has **no caller** and is not part of SCM lifecycle execution. Its fixed root is not a manifest-pinned filesystem identity. Its token checks and shared native probe have not been run in a service process. Cancellation checks occur before and after the synchronous call; they do not interrupt an in-flight Windows open or prove bounded native completion. It must not be exposed until those defects and the trust requirements below are resolved.

## Why activation remains blocked

The existing Protected manifest pins the observation fixture's existing command and installation, not a new probe mode and complete storage target identities. No independently protected, durable, one-use approval mechanism binds the reviewed package, manifest, VM, snapshot, fixed target and expiration across restarts. The previous synthetic gate's in-memory nonce set does not provide that guarantee. A protected evidence channel and independent binding to the actual SCM service process also remain to be established. The synchronous access checks need a reviewed finite-lifetime and cancellation strategy that retains resources until native operations finish. These are unfinished engineering tasks, not proof that the platform cannot support the design.

Current fixed-path and role checks are therefore not represented as a protected deployment gate. The model accepts an `ExpectedIdentity` but no independent manifest validation or approval is connected to the private method. Another ordinary user or administrator cannot be tested by substituting a SID: separate native tests must execute under each actual effective token. Windows ACLs, service-token privileges, path identity and the VM's isolation remain unverified.

## Safe host verification

Both service and security-foundation .NET projects built with `--no-restore --disable-build-servers`: **0 errors, 0 warnings** each. No native Windows service, storage or token operation was executed.

| Offline command on `ServiceFoundation.dll` | Result |
| --- | --- |
| `service-hosted-probe-selftest` | 12 passed, 0 failed: disabled operation, fixed target, real shared ACL expectation helpers, synthetic matching/mismatched token evidence and bounded output. |
| `protected-probe-gate-selftest` | 15 passed, 0 failed: synthetic VM, digest, approval, target, expiry and process-local replay decisions. |
| `service-token-probe-selftest` | 20 passed, 0 failed: synthetic identity and simulated timeout/cancellation decisions. |
| `protected-fixture-selftest` | 11 passed, 0 failed. |
| `scm-selftest` | 33 passed, 0 failed; ABI layout PASS. |
| `selftest` | PASS. |

No test invokes the private Windows method. Timeout cases are synthetic, not evidence that native I/O is cancellable. Target-substitution tests check synthetic inputs, not Windows object identities.

## Native prerequisites

Before a future native run, complete and review the activation implementation and independently approve the disposable VM identity, actual isolation, Protected provisioning, rollback snapshot and evidence capture. Review and pin the service executable, dependencies, SCM command, manifest and target object identities. Provision a nonadministrator restricted broker account and service SID, distinct ordinary/alternate identities, narrow process-inspection permissions and synthetic NTFS ACLs only in the approved VM. A separate durable authorization/replay design must be verified; do not reuse Lifecycle approval or overwrite its same-named service configuration. Run effective-access tests separately under the real service and ordinary tokens; admin bypass must be separately characterized.

Windows 10 Home support for the actual provisioning, service SID restrictions, relevant token inspection and ACL tooling is unverified on the operator's guest; the missing administrative UI alone does not establish an API limitation. No VM, host security settings, services, snapshots, ISOs or application gates were changed. Egress calibration and network-sensitive application tests remain separately blocked.

**Production NO-GO.**
