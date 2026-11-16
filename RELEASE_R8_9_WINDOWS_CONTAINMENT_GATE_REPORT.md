# R8.9 — Windows containment gate

**Outcome D. The existing implementation lacks an independently verifiable pre-resume boundary. Offline gate verification PASS. Native containment and live acceptance BLOCKED. Production NO-GO.** Started on `main` at `43d72b8036b27b7926a74d08af260f865259669b` with existing uncommitted work. Antigravity's R8.8 appendix records clean TypeScript checks, 99 frontend and 289 broad backend passes. Live execution authorization and independently verified containment were both absent. [R8.8 independent verification](RELEASE_R8_8_LOCAL_RELEASE_CANDIDATE_REPORT.md#antigravity-independent-verification--r88)

## First blocking interface

`tools/process_test_supervisor/native_fixture_entry.py:10` exits **3 / `EVIDENCE_INCOMPLETE` / `observer_handoff_unavailable`** for every supported scenario before constructing a controller. The existing `OuterController.run()` supplies a deadline/rejection callback to `WindowsJobDriver.start_contained()`. The current callback carries no independent acknowledgement. The local observer can query a borrowed process handle for identity and liveness. It lacks authenticated handle acquisition, independent same-job membership evidence and a channel back to the resume decision.

The smallest correction is the existing R5.5 P0-1/P0-2 handoff. Bind an authenticated observer's read-only process/job evidence to the operation and retained identities, then require its single-use acknowledgement before the controller authorizes resume. The cooperative descendant in P0-3 is required for the descendant claim. The current fixture uses ordinary `Popen`. The proposal is ready for independent review and still requires the review decision before implementation. The buildable protocol, independent package and Windows execution approval are separate prerequisites. No product, supervisor, fixture or launcher code changed in R8.9. [R5.5 three-interface design](RELEASE_R5_APPLICATION_INTEGRATION_REPORT.md#r55---three-p0-security-interface-closure)

| Requirement | Current source and offline evidence | Missing proof and authority |
|---|---|---|
| Own root before user code | `backend/runtime/windows_job_driver.py` creates suspended without handle inheritance, assigns, queries membership/liveness and then resumes. `test_i52_windows_job_mock.py` covers order, assignment failure and invalid resume paths with injected calls. | Authenticated observer handoff and resume/abort serialization require the independent design decision before implementation. Native assignment, nesting and pre-resume evidence require a separately approved Windows run. |
| Own descendants and prove cleanup | `tools/process_test_supervisor/outer_controller.py` uses retained handles and a synchronous deadline. The driver requests owned termination and checks root liveness and active job count. Mock tests cover still-active descendants, interrupted cleanup, repeated cleanup and partial handle closure. | Implement the approved cooperative descendant handshake, retained identities and independent exit/accounting evidence. A root exit alone is insufficient. Verify descendant membership and release on the actual Windows build. |
| Controller death or blocked call | No independent deadline or OS cleanup boundary is provided by the controller or the polling `backend/runtime/lifecycle.py`. The latter defaults to `UnavailableDriver`. | An approved operator must own the external VM deadline and stop/reset authority. Test controller death/hang, observer handle lifetime and native termination separately. A reset records host containment and leaves guest cleanup unverified. |
| Preserve unrelated processes and reject bind races | `backend/runtime/windows_listener.py` correlates a listener with held identity and job membership, and the coordinator checks ownership around health. `test_i53_listener_mock.py` and `test_i5_lifecycle_state.py` cover foreign, changing or ambiguous listeners and fail-closed cleanup. `WindowsJobDriver.health_ready()` returns false. | Native IPv4/IPv6 listener attribution and an owned, health-checked startup path require reviewed integration and a separately authorized synthetic loopback run. Keep the unrelated control out of the workload job. |
| Launcher and execution policy | `scripts/dev.ps1` throws before startup. Its old `Start-Job` flow is unreachable. `tests/backend/test_a1_job_ownership.ps1` exercised the legacy primitive and was previously denied with `AuthorizationManager check failed`. This session's PowerShell 7.6.9 reports `RemoteSigned`, with `LocalMachine` set and other scopes `Undefined`. | The read-only result does not establish Windows PowerShell 5.1 behavior or script trust in the future test guest. Stop on any rejection and record the host, executable, command and error. No policy or trust change was made. [A1 evidence](APPLICATION_A1_READINESS_REPORT.md) · [PowerShell execution policies](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies) |

Windows Job Object assignment rights and nesting constraints apply to the target build. `KILL_ON_JOB_CLOSE` depends on closure of the last job handle. An observer-held duplicate can outlive the controller, so controller exit alone does not establish cleanup. [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) · [AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)

## Executed offline check

From the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_r55_native_package.py tests/backend/test_r55_outer_controller.py tests/backend/test_r55_process_observer.py tests/backend/test_i52_windows_job_mock.py tests/backend/test_i53_listener_mock.py tests/backend/test_i54_supervisor_policy.py tests/backend/test_i5_lifecycle_state.py tests/backend/test_i3_offline_preflight.py tests/backend/test_config_validation.py --tb=short
```

**88 passed, 0 failed or skipped in 2.58s, exit 0**, with one existing `asyncio_mode` configuration warning under disabled plugin autoload. The entry test checks the refusal in process, and the lifecycle and listener tests inject synthetic APIs. `git diff --check` passed. No native workload, server, VM or browser was launched.

## Independent Windows verification procedure

1. The designated Windows security reviewer and VM operator record a written design decision on the R5.5 three-interface proposal. Review the smallest handoff above, the observer's rights and handle lifetime, the single-use resume decision, the cooperative descendant trust assumption, and the independent process-event source with detectable evidence loss. Only an approved design permits implementation work on those interfaces.
2. After implementation and offline review, the operator must approve an exact manifest for the disposable run. Record Windows build and architecture, immutable reviewed Python/runtime/import closure and fixture hashes, signed-off controller/observer/collector paths and arguments, dedicated guest identities and rights, isolation, output bound, independent deadline and reset authority. The existing R5.5 proposal allows a **5-second controller deadline** and **30-second external host deadline**. Validate both bounds for the reviewed executable set. Use synthetic inputs, no external networking, host shares, protected data, real credentials or application services. Capture bounded evidence before reset. No current native command can produce a verified result.
3. Run the approved non-networked cases in the reviewed VM. The first five names below are the fixed entry options. The fault-injection cases require the manifest to identify the approved injection point and command once implemented.

| Case | Independent evidence required |
|---|---|
| `normal`, `early_exit` | Keep stable process/job identity evidence. Record suspended create, verified membership and one-use observer acknowledgement before resume. Record the root exit, complete accounting and survival of the same unrelated control. Classify any early exit before authorization explicitly. |
| `reject_containment`, injected partial assignment or cancellation | Show that the unacknowledged root or descendant never resumed, even if a partial create obtained a handle. Verify exit of every created process and unchanged unrelated control. |
| `descendant`, `stall`, repeated cleanup | Match process-creation evidence to retained root/descendant handles, verify membership before resume, and distinguish termination request from observed exit. Require signalled owned handles, complete zero-active accounting, bounded cleanup and unrelated-control survival. Repeated cleanup must never retarget stale handles. |
| Unexpected controller termination or hang | Inject death or hang before resume and during owned execution. Record observer/duplicate state, last-handle closure where claimed, independently observed owned exits and unrelated-control survival. If the external deadline requires reset or evidence is incomplete, record containment recovery and cleanup unverified. |
| Port collision and replacement | Run only after the non-networked gate, under a separate synthetic loopback approval with an exact test endpoint and command. Attribute a pre-existing listener to a separate control, then exercise a competing bind after the availability check. Verify startup refuses foreign/ambiguous ownership or handles bind failure, never signals readiness on the competing HTTP response, and independently cleans only owned descendants. |

Use PASS only for complete, consistent native evidence within the approved deadlines. A confirmed surviving owned process, unexpected execution, unrelated-process harm or actor outside scope is FAIL. Missing/ambiguous identity, incomplete evidence, unavailable host authority, policy denial or reset before guest cleanup evidence is BLOCKED. Return to the reviewed gate on any failure or uncertainty. Do not substitute the legacy PowerShell test, call the experimental controller directly, or retry a rejected test through another launcher.

## Separate request for local acceptance

**Scope requested after native and synthetic loopback verification.** An operator-controlled run of the existing authenticated chat-only development acceptance flow. Pin the reviewed backend executable and existing `backend/__main__.py` entry, frontend executable and `frontend/vite.config.ts` configuration, and isolated browser profile. Verify the pre-existing Ollama process and approved `qwen2.5:7b` and `llava:7b` model identities. Use disposable state and a local test operator account. Permit only monitored loopback on backend `127.0.0.1:8001`, frontend `127.0.0.1:5174`, and existing Ollama `127.0.0.1:11434`, with live commands and exact paths in the separately approved manifest. The disabled launcher still refuses startup. The existing runtime entry points identify the workloads for review. [R8.5 configuration and acceptance cases](RELEASE_R8_5_LOCAL_RUNTIME_ACCEPTANCE_REPORT.md#antigravity-handoff)

**Requested maximum:** 15 minutes including shutdown under an independent operator deadline, subject to the separate written approval. The operator owns verified cleanup of run-owned processes and preserves pre-existing Ollama and unrelated listeners. Retain sanitized process identities, listener attribution, readiness, authentication, synthetic chat/tool/attachment outcomes, logout, and observed descendant exits and listener release. Stop on missing approval, failed policy, changed ownership, out-of-scope egress, protected capability exposure, or a missed deadline. The request grants no execution permission.

**One next action** — Send the R5.5 three-interface proposal and this gate report to the designated Windows security reviewer and VM operator for their written design decision before implementing or scheduling native tests.

---

## Antigravity independent verification — R8.9

Executed 2026-11-16 on the existing repository checkout without commit, push, pull, merge or branch switch.

### 1. Branch and HEAD

- **Branch**: `main`
- **HEAD**: `43d72b8036b27b7926a74d08af260f865259669b`
- **Working tree**: 44 modified files, 19 untracked files (preserved exactly; no unauthorized branch or working-tree mutation).
- **`git diff --check`**: **PASS** (EXIT_CODE:0, pre-existing LF->CRLF warnings only).

### 2. Existing implementation inspected

Inspected the full containment chain across:
- `tools/process_test_supervisor/native_fixture_entry.py`
- `tools/process_test_supervisor/outer_controller.py`
- `tools/process_test_supervisor/process_observer.py`
- `tools/process_test_supervisor/policy.py`
- `backend/runtime/windows_job_driver.py`
- `backend/runtime/windows_listener.py`
- `scripts/dev.ps1`
- `RELEASE_R5_APPLICATION_INTEGRATION_REPORT.md` (R5.5 Three-P0 security interface closure)

**Architectural tracing of the 10 containment checkpoints:**
1. *Job Object creation*: `WindowsJobDriver.open_containment()` calls `native.create_kill_on_close_job()`, instantiating an unnamed, local Job Object inside the controller's process.
2. *Child ownership*: `OuterController` / `WindowsJobDriver` holds local process and primary thread handles in `job.roots` (`OwnedRoot(process, thread, role)`).
3. *Suspended creation*: `WindowsJobDriver.start_contained()` invokes `native.create_suspended()` with `CREATE_SUSPENDED`, ensuring no user-mode code executes at process launch.
4. *Job assignment timing*: `native.assign(job.handle, root.handle)` occurs immediately while the process is suspended, followed by `native.in_job()` verification.
5. *Independent observation*: **Absent.** `OuterController.run()` passes a local closure (`check_deadline`) to `before_resume`. `process_observer.observe_process()` only accepts an unauthenticated borrowed handle already present in the local process, labeled `local_handle_query_unverified`.
6. *Process & Job identification*: Process identity uses `(GetProcessId, GetProcessTimes)`. The Job Object has no independent correlation token or cross-process identifier.
7. *Authentication and binding*: **Absent.** No mutual authentication, named pipe token validation, `DuplicateHandle` transfer, or nonce-bound handshake exists between controller and observer.
8. *Pre-resume barrier*: `WindowsJobDriver.start_contained()` evaluates `before_resume()`. In `OuterController.run()`, this only checks deadline and scenario rejection (`check_deadline()`). It does **not** await or check an independent observer receipt.
9. *Observation failure handling*: `native_fixture_entry.py` detects missing handoff and immediately exits code 3 (`EVIDENCE_INCOMPLETE` / `observer_handoff_unavailable`) before creating a controller. In the driver, a false or failing `before_resume` raises `RuntimeError`, aborting resume.
10. *Controller crash/hang*: `KILL_ON_JOB_CLOSE` terminates the job only if all job handles close; an unclosed external duplicate delays termination. If the controller hangs in a blocking Windows call, no internal recovery exists; only an external VM host deadline/reset provides containment.

### 3. Actual R8.9 test results

Executed the exact offline lifecycle, supervisor, and configuration test suite from the repository root:
```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_r55_native_package.py tests/backend/test_r55_outer_controller.py tests/backend/test_r55_process_observer.py tests/backend/test_i52_windows_job_mock.py tests/backend/test_i53_listener_mock.py tests/backend/test_i54_supervisor_policy.py tests/backend/test_i5_lifecycle_state.py tests/backend/test_i3_offline_preflight.py tests/backend/test_config_validation.py --tb=short
```
- **Result**: **88 passed**, 0 failed, 0 skipped, 1 warning in 1.36s (`EXIT_CODE:0`).
- **Warning**: 1 PytestConfigWarning (`asyncio_mode` under disabled autoload).
- Verified:
  - Package structure and non-network import boundaries (`test_r55_native_package.py`).
  - Outer controller single-use lifecycle, deadline enforcement, and cleanup error handling (`test_r55_outer_controller.py`).
  - Process observer identity checking, state transition categorization, and unverified provenance attribution (`test_r55_process_observer.py`).
  - Windows Job Driver mock ordering, assignment failures, and invalid resume prevention (`test_i52_windows_job_mock.py`).
  - Listener attribution, foreign listener rejection, and IP binding validation (`test_i53_listener_mock.py`).
  - Policy classification covering all 20 standardized supervisor outcomes (`test_i54_supervisor_policy.py`).
  - Coordinator lifecycle state machine and preflight configuration checks (`test_i5_lifecycle_state.py`, `test_i3_offline_preflight.py`, `test_config_validation.py`).

### 4. Confirmed handoff deficiency

Executed `native_fixture_entry.py` across all supported scenarios (`normal`, `stall`, `descendant`, `early_exit`, `reject_containment`):
```powershell
$env:PYTHONPATH = '.'
python -m tools.process_test_supervisor.native_fixture_entry --scenario <scenario>
```
- Output for every scenario:
  ```json
  {"controller_claim": null, "observer_evidence": null, "reason": "observer_handoff_unavailable", "scenario": "<scenario>", "status": "EVIDENCE_INCOMPLETE"}
  ```
- Exit code: **3** (`EVIDENCE_INCOMPLETE`).
- **Deficiency Confirmed**: The entry point deliberately refuses to construct `OuterController` or launch any process because no independent, authenticated observer channel exists to verify Job Object containment before thread resumption.

### 5. Fail-closed behavior

Independently confirmed across code and execution:
- **No controller instantiated**: Entry point halts at line 16 of `native_fixture_entry.py` prior to invoking `OuterController`.
- **No subprocess or child spawned**: Process tree remains 100% clean; zero OS child processes created.
- **No kernel Job Object allocated**: Kernel remains untouched during unverified entry attempts.
- **Failure is unambiguously classified**: Emits structured JSON with `EVIDENCE_INCOMPLETE` and exit 3, distinct from success (0) or error (1/2).
- **No unrelated process harm**: No kill-by-PID or broad taskkill paths exist.
- **Launcher restrictions preserved**: `scripts/dev.ps1` line 9 unconditionally throws:
  `throw 'Development launcher disabled: child-process cleanup and listener ownership are not verified. See APPLICATION_A1_READINESS_REPORT.md.'`

### 6. Independent observation requirements

To establish an independently verified pre-resume boundary, the system requires:
1. **Authenticated controller and observer identities**: OS-verified pipe peer identity (`GetNamedPipeServerProcessId` / `GetNamedPipeClientProcessId`) correlated with dedicated test account SID.
2. **Handle duplication with least privilege**: Controller duplicates read-only `JOB_OBJECT_QUERY` and `PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE` handles to the observer process via `DuplicateHandle`; observer never receives thread handles or termination rights.
3. **Independent membership verification**: Observer evaluates `IsProcessInJob(child_handle, job_handle)` on its own duplicated handles prior to root resume.
4. **Single-use acknowledgment & serialization**: Observer returns a nonce-bound `ROOT_OBSERVED` token; controller consumes token under an operation lock and marks `RESUMING` as the irreversible linearization point before calling `ResumeThread`.
5. **Cooperative descendant handling**: Root creates child suspended; controller duplicates child process/thread handles and verifies membership; root releases child thread handle before descendant resume.
6. **External watchdog authority**: Hypervisor/operator external deadline (30s) with independent VM stop/reset authority to bound controller death or hang.

### 7. Minimal required design correction

The minimal correction required to unblock independent verification is the formal adoption and implementation of the **R5.5 Three-P0 Security Interface**:
- **P0-1 (Peer Authentication & Handle Duplication)**: Implement the local named pipe IPC with explicit DACL, handle duplication of query-only job/process handles, and authenticated peer token validation.
- **P0-2 (Serialized Resume & Abort State Machine)**: Implement the controller-side operation lock, pre-resume observer ACK consumption, `RESUMING` linearization point, and post-authorization abort handling.
- **P0-3 (Cooperative Suspended Descendant Protocol)**: Update the test workload fixture to create the descendant suspended, offer the handle via IPC, and release the raw thread handle only after controller/observer acknowledgment.

### 8. Reviewer decision required

The following actionable decision request is submitted to the **Designated Windows Security Reviewer** and **VM Operator**:

#### Decision 1: Windows Security Reviewer
- [ ] **APPROVE** the R5.5 Three-P0 Interface specification (P0-1, P0-2, P0-3) as the authoritative design for independent containment testing, accepting the bounded trust model (trusted controller job creator, cooperative root fixture, observer query validation).
- [ ] **REVISE** with specific required changes (e.g., alternative IPC mechanism, modified handle rights, or alternate event-tracing source for descendant parentage).
- [ ] **REJECT** native Windows Job Object containment testing in favor of external container/VM hypervisor-only isolation boundaries.

#### Decision 2: VM Operator
- [ ] **APPROVE** the disposable isolated VM test manifest (network-isolated guest, dedicated test logon SID, 5s controller deadline, 30s external host deadline, pre-reset evidence capture path).
- [ ] **AUTHORIZE** implementation of P0-1, P0-2, and P0-3 in `tools/process_test_supervisor` for offline review.
- [ ] **HOLD** all native execution until code review of the implemented protocol is complete.

### 9. Native verification status

- **Windows Native Containment**: **BLOCKED** (Outcome D: implementation lacks authenticated observer handoff).
- **Live Inference & Application Acceptance**: **BLOCKED** (prerequisites unmet).
- **Execution Restrictions Observed**: Zero native jobs spawned; zero application servers (Uvicorn, Vite) or background listeners launched; host security policies preserved.

### 10. Exact next authorized action

1. Await written design sign-off from the Windows Security Reviewer on the R5.5 Three-P0 Interface specification.
2. Upon approval, implement P0-1, P0-2, and P0-3 protocol modules within `tools/process_test_supervisor/` and `backend/runtime/windows_job_driver.py`.
3. Verify the implemented protocol offline with mocked IPC and synthetic handle adapters before requesting disposable VM execution.

### 11. Production status

- **Production Status**: **STRICTLY NO-GO**.
- Blocked by R5.5 native process supervisor containment and R6.4 durable provenance-key protection.
