# Batches 2B.3L, M and N — Protected broker integration follow-up

**Production NO-GO.** The protected fixture, native observation paths and synthetic storage integration are implemented. Native SCM, connected caller impersonation and protected storage enforcement await approved Windows VM verification. The existing development broker and launcher were not edited. The Python application, production gates and real storage are outside these prototypes.

## Code changes and decisions

| Files | Implementation |
| --- | --- |
| `prototype/windows_security_foundation/ServiceIdentity.cs`, `WindowsInspection.cs` | Require all nonoptional manifest fields and reject duplicate or unknown keys, malformed evidence and a digest mismatch. Pin the protected SCM binary command including exact pipe and distinct observer SID. Sample native token ID and modification ID around token inspection, reject a changing snapshot and expose the IDs only to the verifier. |
| `prototype/windows_security_foundation/EndpointBinding.cs`, `Program.cs`, `SecurityFoundation.csproj` | Share protected mode validation with the SCM worker. Add the observation-only protected endpoint command and require the pinned observer account. Check the same pipe handle, retained process objects, token revisions, SCM configuration, exact ACL and liveness across snapshots. Keep native authority false. |
| `prototype/windows_security_foundation/CallerVerification.cs` | Add a separate, unconnected caller verification component for a future bounded read. It uses synchronous `RunAsClient`, `OpenThreadToken` with `OpenAsSelf`, and bounded `GetTokenInformation` reads. It requires Identification level and the independently pinned caller SID. Two samples must agree on SID, level, logon and revision. The thread is checked before and after each scope. Failure denies, and the native wrapper attempts reversion and terminates the process if it cannot establish a clear thread after its own impersonation. |
| `prototype/windows_security_foundation/StorageBoundary.cs` | Issue one-use synthetic capabilities against the existing policy. Bind them to the exact request and context. Reserve one writer, consume grants on finish or explicit interruption, fence against current generation and witness, and retain sticky quarantine after ambiguous rollback. Add a journal interface and a final fence recheck before publishing the new generation. |
| `prototype/windows_security_foundation/SyntheticStateJournal.cs` | Add a test-only filesystem journal for bounded synthetic snapshots. Its constructor accepts no path. It exclusively creates a generated child of the current local application Temp directory, pins ancestors and the root without delete sharing, and checks canonical paths, reparse status and hard-link count. Files have generated names and bytes. A commit flushes and reads back bytes through the owned handle before the final fence check. Rollback and teardown mark only owned handles for deletion and confirm absence. No recursive cleanup or path-selected write is exposed by the adapter. |
| `prototype/windows_security_foundation/StorageAccessProbe.cs`, `tests/backend/run_batch2b3n_native_access.ps1` | Add an approval-gated protected VM profile with distinct application, independent and restricted broker roles. Check actual token evidence, direct mutation rights, directory and sidecar creation rights, fixed WAL/SHM fixtures, canonical path and link count, witness mutation denial, and incompatible existing writable handles observable through sharing checks. An extra broker-only switch exercises one generated sidecar create, write, readback and deletion. |
| `prototype/windows_security_foundation/SecurityTests.cs` | Run the shared verifier, caller policy, coordinator and real temporary-file journal in the integration suite. |

L compares observations to independently delivered manifest bytes and SHA-256. The manifest must come from reviewed provisioning. File hashes are checked through retained handles after NTFS ancestry, object identity, link and ACL checks. Native inspection can pin current files and detect ordinary replacement and incompatible write sharing. Current-file evidence cannot establish the identity of a previously mapped image or runtime dependency, or rule out all inherited and duplicated writers.

M verifies the pipe and expected service without sending data. The observer uses Identification SQOS. The existing development client retains its previous behavior. The caller scaffold is deliberately disconnected from K's outbound-only pipe. Windows associates named-pipe impersonation with the last message read, so a future request-capable fixture would need a separately reviewed bounded read, serial connection ownership and native cleanup verification. [ImpersonateNamedPipeClient](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-impersonatenamedpipeclient) · [OpenThreadToken](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openthreadtoken) · [RevertToSelf](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-reverttoself)

In the integration tests, the verifier can hand a scoped synthetic binding to the shared storage policy while observation leases remain open. The policy rechecks that binding on issue, reserve and finish. A failed recheck or the end of the scope invalidates it. A matching native observation never issues that binding. `StoragePolicy.Production` always returns `Disabled`.

N's journal models protected state transitions and exercises real temporary filesystem I/O. It is an in-process, single-writer fixture with at most 64 generated snapshots of at most 64 resource entries each. It deliberately leaves quarantine latched and uses an injected synthetic freshness witness. It cannot recover state after process exit, prove power-loss durability, reconcile multiple real stores, or enforce exclusion against another Windows identity. The disposable directory and retained handles narrow substitution risk within the synthetic fixture. They do not attest ACLs or guarantee safety against an administrator or a process that already controls the test account. Deletion is by the original handle. [SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle)

## Verification

All commands ran offline on Windows with .NET SDK 10.0.404. The three project builds used `dotnet build <project> --no-restore` and returned **0 errors and 0 warnings**. The service and security foundation layout checks passed. The successful synthetic filesystem cases created and cleaned generated temporary directories and never accessed repository storage.

| Check | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| `ServiceFoundation.dll selftest` | 1 | 0 | 0 |
| `ServiceFoundation.dll scm-selftest` | 33 | 0 | 0 |
| `ServiceFoundation.dll protected-fixture-selftest` | 11 | 0 | 0 |
| `pwsh -NoProfile -File tests/backend/test_batch2b3j4_native_scm.ps1` | 17 | 0 | 0 |
| `SecurityFoundation.dll selftest identity` | 24 | 0 | 0 |
| `SecurityFoundation.dll selftest endpoint` | 21 | 0 | 0 |
| `SecurityFoundation.dll selftest storage` | 27 | 0 | 0 |
| `SecurityFoundation.dll selftest integration` | 33 | 0 | 0 |
| `Broker.dll selftest` and `Broker.dll launcher-selftest` | 2 commands | 0 | 0 |
| `pwsh -NoProfile -File tests/backend/test_batch2b3i3_version.ps1` | 9 | 0 | 0 |
| PowerShell parser | 10 scripts | 0 | 0 |
| New K runner and expanded N runner without approval | 2 expected refusals | 0 | 0 |

The 33 integration cases cover the protected manifest and storage probe token policy, then identity and binding failures for a missing anchor, unsafe descriptor, wrong server, restarted or exited process, reused PID, replaced endpoint, changed token and SCM command. They also cover native nonauthorization, missing caller, stale recovery, missing witness, successful synthetic operation, revocation, eight caller outcomes, cancellation and lease release, process exit after reservation, scope expiry, one-use interruption, and four filesystem cases for commit, partial rollback, quarantine and witness change during a write. They exercise the same shared components used by the prototypes. Native SCM, named-pipe, caller impersonation and protected storage probes were **NOT RUN**.

## VM-only protected storage profile

Use the independent approval and pinned probe setup from the Batch K report. In the staging workspace create `synthetic-storage-k` with an empty `.synthetic-only` marker, the seven fixed fixtures from the N report, and `sqlite-sidecars.fixture` containing `sqlite-wal.fixture` and `sqlite-shm.fixture`. All contents must be generated and each file at most 4 KiB. Provision the ACLs independently in the disposable VM. Allow metadata and synthetic read access needed for inspection. Deny application and independent identities mutation across all tested objects. Allow the restricted broker's file mutation rights on its fixture stores, append only on the audit fixture and no mutation on the witness. Allow file creation in the sidecar directory only to the restricted broker. Preserve denial of parent mutation, delete-child, DACL changes and ownership changes.

```powershell
pwsh -NoProfile -File .\tests\backend\run_batch2b3n_native_access.ps1 `
    -ApproveDisposableVm -ProtectedFixture -Workspace $reviewedWorkspace `
    -VmUuid $approvedVmUuid -ComputerName $approvedVmName `
    -ProbeHashesJson $reviewedProbeHashesJson -Role application `
    -ExpectedCallerSid $approvedObserverSid -ExpectedManifestSha256 $reviewedManifestHash
```

The protected storage probe uses the same independently pinned manifest as the endpoint observer. Its application role is the pinned observer identity. Repeat with `-Role independent` under a separately selected ordinary identity distinct from the pinned observer and broker. The `broker` role requires the pinned broker user and real restricted service SID in both enabled and restricted token lists and rejects an interactive copy of the broker user. A separately approved way to run the probe under that actual restricted token is still required. The K fixture intentionally does not launch the probe. After that VM setup, `-Role broker -ExerciseSyntheticWrites` adds a bounded create, write, flush, readback and removal inside the fixed synthetic sidecar directory. The supervisor has finite process deadlines. Abrupt process termination can leave a synthetic artifact, so snapshot restoration remains the complete rollback.

Repeat the read-only checks after an independently applied revocation and reconnect. For negative substitution checks, stage a reviewed alias or reparse point in a fresh snapshot. A data-sharing conflict is a rejection and never counts as an ACL denial. The probe does not enumerate all writable handles or section mappings, prove witness immutability against restore, or implement a privileged backup/recovery exercise.

## Combined completion matrix

| Requirement | Classification | Evidence or exact dependency |
| --- | --- | --- |
| K explicit protected service startup and SCM-owned endpoint | Implemented and offline-tested | Shared controller, strict protected mode, one observation, connection deadline, cleanup tests. |
| K Windows endpoint ownership and effective access | Implemented, awaiting native verification | First, local, single outbound instance and fixed DACL. Needs distinct approved Windows tokens and an actual SCM lifecycle. |
| K continuous sequential reconnect | Not implemented | The fixture retains its original instance for one observation and closes on terminal shutdown. Reuse would require independently verified disconnect and I/O lifetime semantics. |
| K protected VM observation configuration and runner | Implemented, awaiting native verification | Separate protected manifest and VM guard. Dedicated logon account, restricted SID, protected install, SCM ACL and cross-account process/token query access require independent provisioning. |
| L schema, trust anchor, token revision and installation checks | Implemented and offline-tested | Strict digest and schema, native read-only path, two token snapshots and retained file pins. Native effective-access evidence still required. |
| L loaded-image, inherited writer and runtime attestation | Not implemented | Current-file and token APIs do not attest historical mappings, all inherited/duplicated handles or trusted runtime loading. Requires protected deployment and independent evidence. |
| M bound observation and fail-closed caller component | Implemented and offline-tested | Process-handle comparison, ACL, liveness, token and configuration rechecks, plus synchronous caller policy. Native caller test needs a separately approved request-capable fixture. |
| M native authenticated dispatch | Not implemented | One-way observation emits `authority=false`. Independent trust-chain and Windows verification prerequisites are unmet. |
| N one-use capability, revocation, reservation, recovery gates, journal, fencing and quarantine | Implemented and offline-tested | Shared policy, one synthetic writer, guarded file snapshots, readback and rollback cases. Synthetic scope and witness only. |
| N protected VM rights and generated sidecar probe | Implemented, awaiting native verification | Requires approved distinct identities and an actual restricted-token probe host. Covers effective handle rights, alias/reparse refusal and visible sharing conflicts. |
| N native concurrent revocation, process interruption, backup privilege and durable witness verification | Not implemented | Needs separately provisioned restricted-token execution, a controlled independent writer and interruption harness, a reviewable crash protocol and a witness outside the restore domain. |
| Production storage, broker authority and app integration | Not implemented | Explicit NO-GO gate. Missing native identity separation, endpoint trust, storage enforcement and recovery evidence. |

The next milestone is independent execution of the K observation and lifecycle scenarios in the approved VM, then a separately reviewed restricted-token storage fixture and adversarial verification. Keep production operations disabled throughout.
