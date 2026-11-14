# Batch 2B.3K — Protected service integration

**Production NO-GO. Native SCM and protected endpoint verification NOT RUN.** Batch K adds an observation fixture to the service foundation. A successful observation carries `authority=false`.

No earlier Batch K specification was found in the repository. The supplied Batch K scope governs this implementation. J.4's LocalService fixture is for SCM lifecycle testing. The K fixture uses a separate startup mode and a separately provisioned identity. Both use the fixed development service name, so provision them in separate VM snapshots or successive disposable VM configurations.

## Implementation

| File | Change |
| --- | --- |
| `prototype/windows_service_foundation/ProtectedFixtureConfig.cs` | Validates the exact startup shape, fixed endpoint prefix, random-name format and distinct account SIDs. Builds a fixed SDDL policy from the expected broker, service and observer SIDs. |
| `prototype/windows_service_foundation/ProtectedFixture.cs` | Implements the SCM-owned, one-connection observation worker and Windows pipe creation. |
| `prototype/windows_service_foundation/NativeScm.cs`, `Program.cs` | Select the new worker only through explicit protected-fixture dispatch and use the existing `ScmController` for lifecycle and cleanup. Protected startup rejects J.4 fault-injection arguments. |
| `prototype/windows_service_foundation/ProtectedFixtureTests.cs` | Exercises the actual worker and SCM controller with injected pipe ownership and connection boundaries. |
| `prototype/windows_security_foundation/SecurityFoundation.csproj`, `ServiceIdentity.cs`, `EndpointBinding.cs`, `Program.cs` | Share the startup definition and verify the protected manifest, observed SCM configuration, process, token, installation and connected pipe. |
| `tests/backend/run_batch2b3k_protected_fixture.ps1` | Runs the bounded, read-only protected endpoint observer after the existing VM, workspace and probe hash checks. |

Startup checks the actual Windows identity against the configured dedicated broker user. It rejects a built-in Administrators group and requires a restricted token and membership in the fixed service SID. The native path uses `DuplicateToken` at SecurityIdentification and `CheckTokenMembership`, whose restricted-token check includes the restricting SID list. The independent observer also checks the SCM SID type, account, token group and privilege evidence. [CheckTokenMembership](https://learn.microsoft.com/windows/win32/api/securitybaseapi/nf-securitybaseapi-checktokenmembership)

`CreateNamedPipeW` creates one outbound-only, overlapped instance under `\\.\pipe\` with `PIPE_REJECT_REMOTE_CLIENTS` and `FILE_FLAG_FIRST_PIPE_INSTANCE`. The protected DACL is owned by the broker user. The user and service SID have `0x0012019F`. The observer has `0x00120081`, which permits data and attribute reads, `READ_CONTROL` and synchronization. The observer mask excludes writes, `FILE_CREATE_PIPE_INSTANCE` and security control. The verifier checks the exact three allow rules, owner and reviewed descriptor hash. [CreateNamedPipeW](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createnamedpipew) · [Named-pipe access rights](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights)

The SCM controller publishes RUNNING only after opening the endpoint. `WaitForConnectionAsync` has a 30-second deadline and uses the overlapped handle and SCM cancellation. After one connection, the worker retains the same handle until STOP, SHUTDOWN or terminal failure. There is no read, write, application response or command dispatch. The outbound server cannot read requests. There is no managed disconnect or handle reuse. The shared controller drains managed work before disposal and refuses to acknowledge clean shutdown when its five-second shutdown deadline expires. Its running lifetime is bounded to 60 seconds. Synchronous Windows calls can exceed managed deadlines. The observer has an independent process deadline. [WaitForConnectionAsync](https://learn.microsoft.com/en-us/dotnet/api/system.io.pipes.namedpipeserverstream.waitforconnectionasync?view=net-10.0)

This design keeps the instance open for a single observation, including after the client closes. Sequential client turnover is unsupported. A second observation requires restarting the fixture. Endpoint names can be occupied before startup or after exit. First-instance failure prevents startup and conveys no server trust. The observer compares retained process objects and rechecks SCM, token, process liveness and the same connected handle. Its result is a bounded observation.

## Offline verification

| Check | Result |
| --- | --- |
| .NET 10.0.404 service and security foundation builds with `--no-restore` | Both succeeded with 0 errors and 0 warnings |
| Service `protected-fixture-selftest` | 11 passed, 0 failed |
| Original service `selftest` and `scm-selftest` | Foundation PASS, 33 SCM cases passed, 0 failed, ABI PASS |
| Original J.4 mock harness | 17 passed, 0 failed |
| PowerShell parser | 10 backend scripts parsed, 0 errors |
| Protected fixture runner without approval | Refused with exit 1 and `approval_required` |
| Native service registration, RUNNING/STOPPED, token and DACL enforcement, pipe ownership | NOT RUN |

The 11 fixture cases cover valid configuration and descriptor, unsafe configuration, rejected identity, occupied endpoint, duplicate startup, invalid SCM arguments, STOP during pending connection, SHUTDOWN after connection, interrupted connection, deadline and unexpected worker failure. Cases assert SCM status order and exact resource disposal counts. No native pipe was opened by these cases. The companion LMN report records cross-layer binding and policy cases.

## Approved disposable VM procedure

1. Use an independently approved disposable Windows VM with a rollback snapshot. Install the reviewed .NET 10 runtime and stage both reviewed four-file build sets. Keep the binary and runtime installation, service configuration, staging tree and trust material outside ordinary users' write control.
2. Using the VM's independently reviewed provisioning procedure, configure `SovereignDevBrokerFoundation` as a demand-start own-process service under a dedicated nonadministrator local logon account and restricted service SID type. Use the following exact binary-command shape. Obtain both account SIDs from provisioning and choose a fresh 32-lowercase-hex endpoint suffix for each fixture setup. Grant only required logon privileges and configure the service token to exclude the privileges rejected by L. Keep the observer separate and non-elevated. It requires SCM query/read-control, process query-limited/synchronize, process-token query, and installation read/metadata access. Default cross-account process/token security can block this observation. A missing read grant fails closed and requires a separately reviewed VM provisioning step.

   ```text
   "<protected-install>\ServiceFoundation.exe" service-protected-fixture --approved-vm-fixture SovereignAI-identity-dev-<32 lowercase hex> <broker-user-sid> <observer-user-sid>
   ```

3. Stage `expected-protected-service.json` using the L report's schema and add `ObserverSid`. Pin the exact quoted binary command, account, restricted SID, pipe name, integrity, trusted owners, NTFS file identities and hashes, SCM descriptor and expected pipe descriptor. Hash canonical UTF-8 Owner+Access SDDL as described in the L report. Derive the expected ACL from provisioning and the fixed rule masks. Deliver the reviewed manifest SHA-256 and four probe binary hashes independently of the endpoint. Stage `.sovereign-lmn-disposable-vm` with the reviewed VM UUID in the probe workspace.
4. Have the VM operator start the reviewed service through SCM, then run the observation below as the pinned observer within the 30-second connection window. `VerifiedEvidence` is the expected positive finding. The runner checks the VM UUID/name, marker and probe hashes, supervises only its observer subprocess, and performs no service installation, startup or cleanup.

   ```powershell
   pwsh -NoProfile -File .\tests\backend\run_batch2b3k_protected_fixture.ps1 `
       -ApproveDisposableVm -Workspace $reviewedWorkspace -VmUuid $approvedVmUuid `
       -ComputerName $approvedVmName -ProbeHashesJson $reviewedProbeHashesJson `
       -ExpectedManifestSha256 $reviewedManifestHash -ExpectedFinding VerifiedEvidence
   ```

5. Independently verify SCM STOP and SHUTDOWN with a pending connection and with a connected observer. Verify access from a genuinely different SID, an occupied endpoint, startup after prior instance exit, and inability to claim the same name after ordinary observer close while the service is still running. Restart the service between connection scenarios. Exercise an unexpected exit and a new PID. Deliberate negative observations can use the runner's `-ExpectedFinding` parameter. Capture categorical output only and restore the VM snapshot after the run.

The next milestone is independent VM provisioning and native validation of this exact fixture, including effective cross-account inspection rights and access from distinct Windows tokens. Service authority, authenticated command dispatch and production storage remain disabled.
