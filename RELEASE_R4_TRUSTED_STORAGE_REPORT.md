# R4.1 Shared trusted-storage boundary

**R4.1: PARTIAL. R4: PARTIAL. Production: NO-GO.** A common application-level denial now protects document and artifact write paths while the native writer is unavailable. It is not a positive trusted-storage provider, and no protected workflow was enabled.

## Existing infrastructure and changes

The existing quarantine registry and replacement intents use SQLite `BEGIN IMMEDIATE`, server-issued IDs, owner/clearance and revision checks. They record non-authorizing metadata; SQLite locking does not fence source files or Chroma. Document ingestion has a persistent-index gate, direct staging code and a separate retrieval authorization gate. Artifact generators have isolated temporary-file verification; registry dispatch blocks their production names before approval consumption, and publication is independently withheld by task/source provenance policy. These document and artifact policy checks remain separate.

The experimental .NET named-pipe broker, restricted service, handle/ACL checks and durable recovery components live under `prototype/`; they are not a production storage provider. Prior reports describe AppContainer access denials and a .NET peer-name mismatch; the SID-based code was added as a corrective hypothesis, not proof that the identity or token/reversion checks now work natively. Other reported tests cover synthetic protocols, selected local process-restart fixtures or preflight; their scopes do not establish protected service-token enforcement, endpoint authentication, filesystem writer exclusion or independent rollback resistance. The VM verification plan reports native protected identity, endpoint and storage runs pending. The egress audit records an earlier automatic rejection of an unattributed external telemetry request; no destination or component was established and the rejected request was not retried.

Two concrete integration defects were addressed:

1. Document ingestion and physical cleanup previously relied on a single `persistent_security_guarantees_available()` boolean, while artifact writing used a separate hard block. `require_protected_storage_writer()` is now an unconditional, non-configurable denial in the existing execution policy. The actual document service and blocked artifact dispatch both invoke it; a claimed `verified_persistent` backend and a forged true persistence flag do not reach document reservation or index/file mutation. Revocation remains a separate SQLite denial before cleanup is refused. The artifact block remains independently unconditional and precedes approval consumption. This is deliberately only the **unavailable** state: it accepts no path, fake evidence or configuration flag and cannot issue successful evidence. Identity, lease, staging, readback, commit and recovery states are not implemented by this denial.
2. The blocked artifact-list endpoint previously created and enumerated its sandbox before withholding each file, potentially returning a misleading empty success. It now returns sanitized 503 `ARTIFACT_UNAVAILABLE` before directory creation or enumeration while provenance revalidation is unavailable. Preview and download remain withheld. This changes the blocked-list API response; the artifact UI remains gated and was not executed here.

The common low-level future contract must be backed by independently authenticated broker/process and protected-root/handle identity, scoped allocation, writer exclusion, stable readback and recovery fencing. It must bind server-issued operation and current actor to the specific source revision or task/invocation, with separately enforced document authorization or artifact approval/source/publication policy. A stored operation ID, local mutex, path string, caller-supplied digest, mock capability or SQLite commit cannot supply that evidence. No common durable artifact operation or positive evidence provider is currently present; replacement intents remain document-specific and non-authorizing.

## Verification

From the repository root with `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'`:

```powershell
python -m pytest -q tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i10_artifact_integrity.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_r23_replacement_intents.py tests/backend/test_r22_source_transactions.py tests/backend/test_r31_artifact_contract.py
```

**86 passed, 1 warning in 14.12 s.** The warning is pytest's unknown `asyncio_mode` option with plugin autoload disabled. An earlier run of the same list without `test_r31_artifact_contract.py` passed 77 tests with the same warning in 13.78 s. Tests use temporary SQLite, synthetic users/providers, mocks and the existing offline harness; no real service or network socket was started. They check fabricated persistence flags, no document staging, revocation without index cleanup, artifact-list denial before filesystem access, blocked preview/download, artifact dispatch before approval consumption, forged receipt rejection, authorization freshness, source intents and verified calculation/conversion regressions. No test timed out. `git diff --check` passed for changed tracked source files.

## Evidence levels and next action

- **A — Synthetic validation:** PASS for the scoped denial, injected failures and existing non-authorizing operation checks; not a native security boundary.
- **B — SQLite integration:** PASS for the focused temporary-database tests, including separate-connection metadata contention and revocation. This does not establish an external-store lease or crash durability.
- **C — Native Windows enforcement:** NOT VERIFIED in this milestone. Verify the actual restricted service token, broker/server and caller identities, pinned handles, reparse/hard-link handling and exclusive mutation rights, including competing and previously opened handles.
- **D — Cross-store integration:** NOT VERIFIED. No real filesystem/SQLite/Chroma failure ordering, independent freshness witness, rollback or recovery was exercised.
- **E — Protected operational deployment:** NOT VERIFIED. Authentication, actual services, egress enforcement, process lifecycle and recovery require separate operational evidence.

Next, independently provision and verify the existing restricted broker in an authorized isolated Windows environment using reviewed identities, fixtures and an independent observer. Establish an authenticated broker-issued scoped lease and durable fencing/recovery plus an independently administered freshness witness before implementing any positive storage evidence or wiring protected RAG/artifact staging. Preserve source-specific authorization and task/approval-specific checks. No launcher, Ollama, Chroma, Protected service, VM, firewall, permissions, execution policy, frontend build or dependency installation was used; no commit, merge or push occurred.

## R4.2 — Development broker diagnostic and native release gate

**R4.2: PARTIAL. Native verification: BLOCKED. R4: PARTIAL. Production: NO-GO.** The existing development broker client had a concrete response-binding defect; its correction and in-memory self-test do not establish protected writer authority. No native pipe, service or protected storage test was executed.

### Broker and defect

`prototype/windows_broker_dotnet/Program.cs` is a net10.0 development-only, bounded newline-framed diagnostic server/client. It limits request/response sizes and timeouts, rejects extra/duplicate request fields, and exposes only ping/status/capabilities. The server attempts a synchronous connected-client SID observation under impersonation and rejects mismatch or unverified cleanup. It does **not** authenticate the server to the client, distinguish all same-account attackers, issue storage grants, open protected roots or perform protected writes. The separate `windows_security_foundation` components contain observation-only token/endpoint and synthetic storage/recovery policies; production authorization there remains disabled. The R4 shared Python writer denial remains unconditional and is not connected to broker ping/status.

The diagnostic client's response validator previously accepted any known success string for any command. A `pong` response to `status`, for example, could be reported as a successful status exchange. It now binds success to the exact requested diagnostic command and rejects a success for malformed/unsupported scenarios. Fixed negative protocol denials remain denials. New in-memory self-test cases check mismatched success values. This changes diagnostic correctness only; it does not authenticate a peer, establish operation ownership or grant storage access.

Prior H.5 observations found AppContainer/low-integrity context and Windows error 5; package rights, pipe namespace and managed-platform policy remained hypotheses, not a proven unique cause. H.12 observed available account-name values that did not match; H.13 changed the development comparison to OS-token SIDs with fail-closed cleanup checks, but real SID impersonation and reversion were not independently verified. Protected service identity, endpoint and storage probes remain separately pending. No ACL, impersonation level, firewall or identity check was weakened.

### Native verification matrix — all BLOCKED / NOT RUN

Shared prerequisites for **every row**: an independently approved disposable, isolated Windows VM with a restorable snapshot and synthetic-only workspace; independently pinned executable/manifest hashes and exact runtime/build versions; distinct provisioned application, broker and independent observer identities; reviewed non-inherited handles and a separately verified supervisor able to stop only owned processes and restore the snapshot. Candidate executables are the reviewed `Broker.dll` development diagnostic and the existing `SecurityFoundation.exe`/protected fixture (projects target `net10.0`; this session's compiler SDK was 10.0.404). **Exact release binary version, runtime patch and independent hashes are not pinned here**, so they must be recorded before execution. A proposed external hard limit is 30 seconds per bounded probe and 120 seconds for restart/recovery, followed by independently verified process termination and snapshot restoration; these limits and cleanup are acceptance requirements, not a claim that the current supervisor implements them. No real documents, models, credentials or production data may be used.

| Scenario | Fixture/account and evidence required for PASS; otherwise FAIL or BLOCKED |
| --- | --- |
| Authorized connection | Approved development-only pipe and pinned ordinary client; record server process/pipe identity and token evidence. Ping alone cannot pass the protected-writer gate. |
| Unauthorized caller | Separate independently provisioned identity; actual Windows access denial and no protected side effect. A missing or busy endpoint is not a pass. |
| Identity mismatch | Separately pinned mismatching token/service identity; native SID, token and cleanup observations show categorical denial. |
| Invalid/truncated protocol | Synthetic malformed, oversized and partial frames through reviewed fixture; bounded denial/disconnect and no sensitive output or mutation. |
| Protected-root identity | Independently pinned root and ancestor handles under actual restricted service token; volume/file IDs, ACL/effective access and handle lifetime match independently supplied expectations. |
| Reparse/junction substitution | Reviewer-staged synthetic reparse fixture; native handle/reparse evidence shows refusal and no outside-root access. The existing observer is not proof of a mutation-safe implementation. |
| File replacement during validation | Independently staged synthetic name/handle swap; stable object identity or refusal, with no write to either unintended object. |
| Concurrent writers | Separately observed independent processes/handles under provisioned identities; native effective access and exclusivity prevent a second writer, including handles opened before admission. SQLite locking alone is insufficient. |
| Broker interruption | Supervisor kills only its pinned synthetic broker during a bounded operation; independently inspect handles, pending state and recovery outcome; no success inferred. |
| Client interruption | Terminate only the reviewed client during a synthetic operation; prove cancellation/drain and handle release or explicit quarantine, without retrying uncertain effects. |
| Restart/stale ownership | Restart the fixture with a prior synthetic operation/generation; stale authority is rejected and incomplete state is not promoted without external evidence. |
| Handle cleanup and unrelated processes | Independent supervisor records handle closure and the continued identity/liveness of an unrelated synthetic process; no broad name-based termination. |
| No unintended writes | Independent before/after fixture inventory and handle/ACL observation show no writes outside explicitly approved synthetic objects; unknown changes fail the run and trigger snapshot review. |

The existing `tests/backend/run_batch2b3lm_native_inspection.ps1`, `run_batch2b3n_native_access.ps1`, and `run_batch2b3p_recovery.ps1` cover only their documented observation/fixture scopes; they do not by themselves implement all matrix scenarios or prove an independent supervisor, production writer exclusion or secure cleanup. The operator-run VM plans require pre-provisioned reviewed pins and separate per-run authorization. An unavailable supervisor, token, fixture or test implementation is **BLOCKED**, not PASS. No native run is authorized or implied by this matrix.

### Offline verification

From the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_batch2b3h_ipc_offline.py tests/backend/test_batch2b3h7_lifecycle.py tests/backend/test_batch2b3h5_rights.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i10_artifact_integrity.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a33_production_tools_offline.py
```

**63 passed, 1 warning in 12.10 s.** The warning is pytest's unknown `asyncio_mode` option with plugin autoload disabled. These are protocol parsing, mock lifecycle, candidate-rights, production denial, authorization and deterministic tool tests; they open no real pipe and are not native Windows evidence.

The corrected C# source was copied with its project files and existing local `project.assets.json`/generated NuGet props/targets to a temporary directory. With `DOTNET_CLI_TELEMETRY_OPTOUT=1`, `DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1`, `DOTNET_GENERATE_ASPNET_CERTIFICATE=false`, `DOTNET_NOLOGO=1`, `MSBuildEnableWorkloadResolver=false` and a temporary `DOTNET_CLI_HOME`, the following was run in one command after those copies:

```powershell
dotnet build (Join-Path $taskSource 'Broker.csproj') --no-restore --output (Join-Path $taskTmp 'out')
dotnet (Join-Path $taskTmp 'out/Broker.dll') selftest
```

The build passed with **9 CA1416 warnings, 0 errors in 22.41 s**; the in-memory self-test printed `Offline protocol selftest passed.` and exited 0. The SDK also reported inability to add its temporary tools directory to PATH and an issue verifying workloads; no install, restore or workload update was requested. This managed self-test uses fake delegates and memory streams; it does not call a native pipe or protected storage API. An initial build using an external intermediate directory failed with **8 duplicate-attribute errors** because existing generated `obj` files were included; copying the project to a temporary directory fixed that setup. An intermediate successful build had 9 CA1416 warnings, but a separate self-test command could not locate its output across command sandboxes; neither attempt established a passing self-test. No automatic approval review rejected these commands. `git diff --check` passed for the broker source.

### Decision and next action

Protected filesystem root/file identity, hard-link/reparse resistance, prior/open-handle exclusion, broker server identity, actual service-token authorization, writer leases, interruption/restart and cross-store recovery remain **NOT VERIFIED**. The diagnostic broker cannot currently enforce restricted writes, and its ping cannot change application capabilities. Keep document ingestion/retrieval, artifact writing/publication and the shared protected-write gate blocked. Before further broker feature work, obtain independently approved isolated Windows execution, reviewed binary hashes and a supervisor/cleanup boundary; then run only supported bounded observations and document unsupported matrix cases separately. No app, Ollama, Chroma, Protected service, launcher or VM was started; no host security setting was changed and no dependency installation or restore was requested. No commit, merge or push occurred.
