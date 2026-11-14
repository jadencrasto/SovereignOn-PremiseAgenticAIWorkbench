# Batch 2B.3O — Windows VM verification launcher

**Production NO-GO. Native VM verification remains pending.** The launcher, profile checks, subprocess supervision and refusal paths were exercised offline. No service was installed, started or deleted during this work. The VM, accounts, ACLs and networking were outside the execution scope.

## One-time setup in the approved VM

1. Review the launcher, runners and binaries independently. Use an approved disposable Windows VM and keep an external console with an identified rollback snapshot. Stage PowerShell 7 and the reviewed Windows .NET 10 runtime using the approved offline process. Run the scripts from the reviewed checkout. The launcher uses `%SystemDrive%\SovereignVmVerification` as its fixed local workspace. Stage it on a fixed drive with protected ancestry and ordinary users unable to alter the scripts, trust files or binaries.
2. Stage the reviewed `ServiceFoundation` and `SecurityFoundation` builds. Each contributes its `.exe`, `.dll`, `.deps.json` and `.runtimeconfig.json` at the top of the workspace. Get the eight expected SHA-256 values independently of these staged copies. Obtain the VM hardware UUID, computer name and snapshot reference from the approved inventory and hypervisor. Put the UUID alone in `.sovereign-j4-disposable-vm` for Lifecycle or `.sovereign-lmn-disposable-vm` for Protected.
3. Select one profile per snapshot. **Lifecycle** requires `SovereignDevBrokerFoundation` to be absent. J.4 registers that name temporarily as a demand-start LocalService service and removes the test registration. **Protected** requires the independently provisioned dedicated restricted service under the same name. Use the exact quoted binary command, three distinct account SIDs, restricted service SID, protected install files and reviewed SCM and pipe descriptors from the [K procedure](BATCH2B3K_PROTECTED_SERVICE_INTEGRATION_REPORT.md). Stage `expected-protected-service.json` and its independently reviewed SHA-256. Use the [LMN storage procedure](BATCH2B3LMN_COMPLETION_REPORT.md) for `synthetic-storage-k`, the empty marker, fixed files, sidecar directory and synthetic ACLs. Review the observer's access to the actual service process and token. The launcher will stop if the provisioned configuration, selected identity or fixtures disagree with the pins.
4. From the approved VM checkout, run one setup command. Paste the reviewed pins once and confirm `SAVE <snapshot-reference>`. The script creates `batch2b3o.json` in the fixed workspace without replacing an existing configuration. It stores no credentials and no approval to execute tests. Ensure the referenced snapshot is available before running, and record the hypervisor's restore procedure in the VM's operator record. The configuration identifies snapshot restoration as the rollback action. If a restore predates setup, restage the reviewed config and artifacts.

   ```powershell
   .\tests\backend\setup_batch2b3o_vm.ps1 -Profile Lifecycle
   # Or, from the separate protected fixture snapshot
   .\tests\backend\setup_batch2b3o_vm.ps1 -Profile Protected
   ```

## Short commands for subsequent runs

```powershell
.\test-sih-vm.ps1                       # read-only preflight
.\test-sih-vm.ps1 -Run                  # all suites for this profile and role
.\test-sih-vm.ps1 -Run -Suite Service   # Lifecycle profile, elevated VM console
.\test-sih-vm.ps1 -Run -Suite Endpoint  # Protected profile, pinned observer
.\test-sih-vm.ps1 -Run -Suite Endpoint -Role Independent
.\test-sih-vm.ps1 -Run -Suite Storage -Role Independent
.\test-sih-vm.ps1 -Run -Suite Recovery
```

`-Run` displays the selected actions and requires `RUN <snapshot-reference>` each time. `-Unattended` allows preflight only. `-Suite All` orders Lifecycle as Service then Recovery. Protected observer runs Identity, Endpoint, Storage and Recovery. The independent role runs Endpoint, Storage and Recovery. The broker role runs Storage and Recovery. Explicit suites outside the profile or role fail before the prompt.

For a positive Protected Identity or Endpoint run, the approved VM administrator starts the reviewed fixture from the separate VM console just before confirming the run. `Start-Service SovereignDevBrokerFoundation` is the VM-only start command. Run the launcher under the pinned, non-elevated observer. The pipe accepts one connection with a 30-second wait, so restart the reviewed service between successful observation runs. The launcher reports `service_not_running` when this prerequisite is missing. The dedicated account must have already been provisioned with the exact service configuration and without production access. A failed preflight calls for checking the VM provisioning record.

For the independent endpoint case, start a fresh fixture and run from the pinned, distinct ordinary identity. The probe requests one read-only local pipe open and treats only Windows access denied as a successful rejection. An absent or busy pipe fails the check. Run the positive binding observation separately for the same reviewed fixture configuration. The storage broker case requires an independently approved way to execute under the actual restricted service token. An interactive sign-in as the broker user fails the native restricted-token check. `-Role Broker -Suite Storage -ExerciseStorageWrites` additionally creates, writes, flushes, reads and deletes one generated sidecar, subject to that same token check and per-run approval.

The last output line is a machine-readable JSON summary with planned suites, completed steps, a sanitized failure category and a snapshot reversion flag. The launcher exits nonzero on unexpected results. J.4 writes a fixed run marker only after approval and clears it only after checking cleanup. An interrupted run with that marker requires snapshot restoration. Failure after a potentially mutating step is conservatively flagged for reversion. The launcher supervises its child processes, while SCM-owned service cleanup is verified separately. The VM UUID, model, marker and typed approval reduce accidental host execution. They cannot attest that the machine is disposable or the hypervisor snapshot can be restored.

## Implemented coverage and prerequisites

| Requested scenario | Launcher behavior and evidence |
| --- | --- |
| 1–3 SCM registration, startup, STOP, restart and startup failure | Reuses J.4 under Lifecycle, which checks cleanup and refuses an existing registration. 17 J.4 offline cases passed. Native run pending. |
| 4–6 Restricted identity, service process, executable and protected endpoint | Reuses L/M and K with a protected manifest, pinned identity and read-only native observation. The observer must have the exact query access provisioned. Native run pending. |
| 7–8 Incorrect endpoint, replacement and name contention | `-Suite Endpoint -ExpectedEndpointFinding <category>` asserts a categorical rejection against a reviewer-staged fixture. Name contention and process replacement must be staged and timed separately in the approved VM. The launcher does not manufacture competing endpoints or claim the resulting native evidence. |
| 9 Different-account pipe restriction | New Independent endpoint mode requires a distinct pinned caller and exact access denial from the OS. Pending VM execution. |
| 10 Actual pipe DACL | K compares the observed owner and ACL on the connected handle against the reviewed descriptor and exact rule mask. Pending VM execution. |
| 11–15 Application and independent mutation denial, broker permissions, sidecars and witness | Reuses N's native access probes. Broker sidecar exercise requires explicit selection and an actual restricted service token. Native run and restricted-token execution provisioning remain pending. |
| 16 Revocation and reconnection | Recovery suite exercises durable synthetic revocation and restart. A native post-revocation assertion can rerun the bounded probes in a separately provisioned state. Native ACL/token revocation orchestration and evidence remain pending. |
| 17 Interrupted process recovery | Recovery suite exercises actual local child-process exits and restarts. Service-exit scenario remains in J.4 for native execution. |
| 18 Cleanup and snapshot readiness | Run marker, approved snapshot reference, bounded child supervision, fixed service cleanup check and structured reversion flag. Snapshot availability and restoration require independent confirmation. |

## Files and verification

- Added `test-sih-vm.ps1`, `tests/backend/setup_batch2b3o_vm.ps1`, `tests/backend/batch2b3o_vm_workflow.ps1` and `tests/backend/test_batch2b3o_vm_workflow.ps1`.
- Updated `tests/backend/run_batch2b3lm_native_inspection.ps1`, `run_batch2b3k_protected_fixture.ps1` and `batch2b3lmn_vm_probe.ps1` to reuse the protected manifest and surface the independent pipe-denial probe. Added the corresponding read-only mode in `prototype/windows_security_foundation/Program.cs` and `EndpointBinding.cs`.
- `pwsh -NoProfile -File tests/backend/test_batch2b3o_vm_workflow.ps1` passed **17 cases, 0 failed**. The cases include read-only preflight, missing config, digest and duplicate-key checks, incorrect VM, snapshot prerequisite, service conflict, incomplete provisioning, wrong/elevated caller, endpoint and storage root validation, real child timeout, fail-fast aggregation, cleanup, approval refusal, platform/runtime checks, suite order and failed-run recovery.
- PowerShell parser passed **15 scripts**. The real entry point refused `-Run -Unattended` with exit 1 before reading VM configuration. J.4 passed **17 cases** and I.3 passed **9 cases**. The .NET builds and regression counts are recorded in the P report.
