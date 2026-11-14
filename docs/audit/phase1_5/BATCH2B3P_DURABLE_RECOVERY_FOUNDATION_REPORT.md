# Batch 2B.3P — Durable synthetic recovery

**Production NO-GO.** Windows local process-restart tests ran on generated synthetic files. Native VM isolation, an independently administered witness and power-loss behavior require separate evidence.

## Implementation

| File | Change |
| --- | --- |
| `prototype/windows_security_foundation/DurableFixture.cs` | Generated local-Temp fixture identified by an opaque GUID. Pins ordinary Windows directories, rejects reparse points and substituted file names, checks file identity constraints on opened handles, holds the exclusive file-sharing lease, bounds each file to 64 KiB and cleans up known synthetic files by handle. |
| `prototype/windows_security_foundation/DurableRecovery.cs` | Bounded canonical append-only records with deployment anchor, transaction UUID, generation, store commitment, predecessor hash and checksum. The journal distinguishes intent, commit and recovery abort. It validates five synthetic store snapshots and the witness before publication and latches uncertain state in a flushed quarantine marker. |
| `prototype/windows_security_foundation/StorageBoundary.cs`, `SyntheticStateJournal.cs` | Pass the current synthetic request through the existing journal interface. Restore previously consumed transaction IDs, committed revisions and generation into the existing coordinator. Continue to use its grant issuance, reservation, authorization and final fence. |
| `prototype/windows_security_foundation/RecoveryTests.cs`, `Program.cs` | Run an isolated recovery supervisor and fixed child modes. The child accepts only a fixture GUID and enumerated test arguments. The supervisor bounds execution, observes abrupt exit, starts a new process, verifies recovery output, and deletes generated fixtures. |
| `tests/backend/run_batch2b3p_recovery.ps1` | Short, non-elevated recovery test command using the local reviewed Debug build. |

### Durable ordering and reconciliation

1. Acquire the exclusive fixture lease, read the existing journal and require its fixed deployment anchor. Validate bounded records, exact canonical encoding, generation continuity, uniqueness of intent transaction IDs and hash linkage. Reconcile with the witness and all journaled committed store files. Reconstruct only the last verified snapshot. Require an authorized synthetic recovery context and current-generation capability before making a rollback decision.
2. Reserve a transaction through the shared synthetic policy. Append and flush the intent and verify the written bytes. Stage transaction-specific document, SQLite-like, vector-like, artifact and audit files. The previous generation stays intact. Flush and verify each new file and verify the combined commitment.
3. Recheck the same authorization and generation fence. Compare-and-advance the witness from the verified predecessor. Append a commit that binds the new witness link, flush and reread it. Recheck the store bytes and witness before publishing the new generation in the coordinator.
4. On restart, an intent without a commit can be aborted if the witness still matches the previous committed generation. Remove only its validated staged files and append a flushed abort record. Replaying the same journal is idempotent. A missing commit after witness advancement is ambiguous and causes quarantine. Missing or corrupt files, a torn record, duplicate or out-of-order transitions, conflicting generations, witness absence or disagreement and failed rollback also fail closed. Existing quarantine blocks reuse. If persistence of quarantine cannot be confirmed, the current coordinator denies and the fixture needs operator disposal.

The local lease excludes cooperating fixture processes and is released on process exit. It is not OS authority over another process that ignores the lease. The test harness uses generated files and a separate witness subdirectory. No real SQLite, ChromaDB, documents, artifacts, FastAPI, accounts, services or ACLs are used by the recovery suite. Production policy remains `Disabled`.

### Witness and persistence boundary

`IDurableFreshnessWitness` provides verified read and compare-and-advance. Its evidence includes a deployment ID, generation, monotonic sequence, committed transaction ID, store commitment and previous/current links. The fixture validates its chain from the deployment anchor and the local commit pins the witness's exact link. Restoring either side in isolation, missing a witness or conflicting with its observed head blocks publication. The fixture provider runs under the same identity and can be restored or rewritten along with the data. It supplies no independent rollback resistance. A production provider would need authentication, atomic compare-and-advance and administration outside the broker's recovery domain. No production provider is enabled.

`RandomAccess.FlushToDisk` invokes the platform flush mechanism. The implementation checks API completion, reads back flushed bytes through the file handle, and then verifies them from a new process. These observations do not establish persistence across sudden power loss, file or directory metadata persistence on every filesystem, correct device-cache behavior, or atomic visibility across several files. The test leaves ambiguity quarantined rather than inferring success. [RandomAccess.FlushToDisk](https://learn.microsoft.com/en-us/dotnet/api/system.io.randomaccess.flushtodisk?view=net-10.0) · [FlushFileBuffers](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers)

## Run the offline recovery suite

```powershell
dotnet build .\prototype\windows_security_foundation\SecurityFoundation.csproj --no-restore
pwsh -NoProfile -File .\tests\backend\run_batch2b3p_recovery.ps1
```

The suite executes locally without elevation. Each crash child exits with the fixture code at the selected milestone. The mid-write case writes a durable prefix and exits before the remainder. A separate child then opens the same generated workspace and verifies durable state. The competing-recovery case holds the OS lease in a separate child, observes `Busy`, terminates only the fixture holder and verifies recovery in a new process.

| Test group | Observed result |
| --- | --- |
| Six crash milestones | Before intent left generation 0 clean. After intent, during write and after data flush recovered by aborting at generation 0. Before commit after witness advancement quarantined. After commit recovered generation 1. Repeated recovery reproduced the resolved state. |
| Normal commits, all five stores, restart and duplicate | Generation 5 with five validated resource entries survived restart. A persisted transaction ID refused replay. |
| Later-generation partial write and revocation | Aborting a partial later write preserved the prior committed generation. Revoking after data flush blocked publication and left a durable abort visible after reconnect. |
| Ten damage and disagreement cases | Torn/corrupt journal, missing segment, missing journal, conflicting generation, missing/rolled-back/conflicting witness and missing/conflicting store all quarantined. Each quarantine remained effective after another process restart. |
| Failed rollback, unauthorized/stale recovery and competing lease | Failed rollback latched quarantine. Denied and stale recovery left the pending journal untouched. The lease refused a competing process and a fresh process recovered after release. |

**Recovery suite: 23 passed, 0 failed.** Each case checks fixture cleanup. These are process-exit tests. Hardware power loss and a same-identity adversary remain outside their coverage.

## Full targeted verification

| Command or suite | Result |
| --- | --- |
| `dotnet build --no-restore` for SecurityFoundation, ServiceFoundation and Broker | 3 builds passed, 0 errors and 0 warnings |
| ServiceFoundation `selftest`, `scm-selftest`, `protected-fixture-selftest` | Foundation PASS, 33 SCM and 11 K cases passed, ABI PASS |
| `test_batch2b3j4_native_scm.ps1` | 17 passed |
| SecurityFoundation `selftest identity`, `endpoint`, `storage`, `integration` | 24, 21, 27 and 33 passed respectively. Windows inspection ABI PASS |
| Broker `selftest` and `launcher-selftest` | Both passed |
| `test_batch2b3i3_version.ps1` | 9 passed |
| `test_batch2b3o_vm_workflow.ps1` and PowerShell parser | 17 workflow cases passed, 15 scripts parsed, unattended real entry guard passed |
| `run_batch2b3p_recovery.ps1` | 23 passed, 0 failed |

The journal has bounded history and does not compact or repair damaged data. Manual disposal is the fixture recovery path after quarantine. Native VM access enforcement, actual SQLite and vector-engine crash ordering, device-level persistence, an independent witness and operator recovery of real data remain unverified or unimplemented. Keep the production gate closed.
