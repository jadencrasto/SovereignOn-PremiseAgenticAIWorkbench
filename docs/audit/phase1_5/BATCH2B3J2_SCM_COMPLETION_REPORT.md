# Batch 2B.3J.2 — SCM completion audit

**Updated status: implementation and synthetic tests expanded; native verification remains NOT RUN. Production NO-GO.** Historical findings below describe the earlier partial attempt; the continuation and current limitations are recorded at the end of this report.

## Changes

`prototype/windows_service_foundation/NativeScm.cs` now allows a single transition from the initial unreported state to `STOPPED`, and after a rejected `START_PENDING` attempts `STOPPED` with service-specific failure code 1, sets process failure, and returns without attempting `RUNNING`. The lifecycle state changes to stopped only if the publisher returns success. Two synthetic tests check the attempted ordering and state for accepted and rejected final publication; duplicate STOPPED after acknowledged success is rejected. A failure response from the publisher is distinct from SCM acknowledgement; mocks cannot establish actual SCM status. Native `SetServiceStatus` calls do not expose a safely enforceable managed timeout. Microsoft warns against more than one STOPPED call because the first can close the RPC context, so no repeated final-report retry was added.

## Completion matrix

| Requirement | Implemented | Tested | Evidence / remaining work |
|---|---|---|---|
| Initial START_PENDING rejection and one STOPPED attempt | Yes | Synthetic transition tests only | Attempts and publisher acknowledgement checked, not actual ServiceMain control flow or SCM. |
| Normal status transition ordering | Existing | Synthetic transition test | Does not exercise native callbacks. |
| Registration, later status errors, invalid handle | Partial | No | Full injectable registration/status wrapper and callback failure tests remain. |
| STOP during initialization, repeated STOP, SHUTDOWN, callback exceptions | Partial | No | Existing callback signals event; races and exception handling need actual-controller tests. |
| Worker failure, cancellation, bounded cleanup | Not implemented | No | No lifecycle worker abstraction or injectable cancellation/cleanup. |
| Invalid startup arguments and repeated transitions | Existing | Partial | Invalid state tested; startup-argument rejection not injected. |
| Native SCM and ABI verification | Implemented candidate | Not run | Needs independently approved isolated VM and signature/layout tests. |
| Deny-only boundary and existing broker | Preserved | Existing foundation self-test | No production endpoint or storage enabled; separate broker untouched. |

## Verification

* `dotnet build prototype/windows_service_foundation/ServiceFoundation.csproj --no-restore`: PASS, exit 0, 0 errors, 0 warnings.
* `dotnet prototype/windows_service_foundation/bin/Debug/net10.0/ServiceFoundation.dll selftest`: PASS, exit 0.
* `dotnet prototype/windows_service_foundation/bin/Debug/net10.0/ServiceFoundation.dll scm-selftest`: PASS, exit 0; aggregate result includes two new synthetic status-publication cases.
* Existing H.8 PowerShell parser: PASS.
* Native SCM, real callback ABI, registration, shutdown, cancellation and OS identity: NOT RUN. No service was installed or started.

Next work must refactor ServiceMain and the handler to use a common injectable controller and add the complete 17-scenario matrix, with bounded cooperative managed work and a single terminal publication. This is a straightforward remaining implementation task, not an OS-only blocker. Do not run the VM registration plan until the remaining implementation and independent review are complete.

Reference: [SetServiceStatus](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-setservicestatus).

## Continuation: shared controller

`ScmController.cs` now contains the lifecycle used by native `ServiceMain` and offline tests: injected registration, status publisher, initialization, deny-only worker, cancellation and cleanup. A stop/shutdown callback signals a task completion source; the worker waits for cancellation and accesses no pipe, storage or network. A 60-second development lifetime and five-second initialization/cleanup limits are configured in native mode. These managed limits do not forcibly cancel native API calls or non-cooperative tasks. STOPPED is attempted at most once and tracked separately from publisher acknowledgement; failure produces a nonzero controller result. No further status is published after that attempt. Static callbacks remain rooted.

`NativeScm.cs` connects the real registration/publish functions and callback to this controller; `ScmTests.cs` runs the same controller with injected dependencies. `Program.cs`, broker, launcher and authorization gates were not changed in this continuation. A callback exception signals failure and stop without leaking exception details. The host remains development-only and deny-only.

## Synthetic test results

`dotnet build prototype/windows_service_foundation/ServiceFoundation.csproj --no-restore`: PASS, exit 0, 0 errors and 0 warnings. `dotnet prototype/windows_service_foundation/bin/Debug/net10.0/ServiceFoundation.dll selftest`: PASS, exit 0. `dotnet prototype/windows_service_foundation/bin/Debug/net10.0/ServiceFoundation.dll scm-selftest`: PASS, exit 0; **20 passed, 0 failed**. PowerShell H.8 runner parser: PASS. SCM self-test checks structure sizes (28 bytes for SERVICE_STATUS and two pointers for table entry); signatures were inspected in source, not validated through actual native callbacks.

| Required case | Synthetic result |
|---|---|
| 1 normal sequence | PASS, ordered publications |
| 2 registration failure | PASS, no publication |
| 3 START_PENDING failure | PASS, one failure-coded STOPPED attempt |
| 4 RUNNING failure | PASS |
| 5 STOP_PENDING failure | PASS |
| 6 STOPPED failure | PASS, not acknowledged |
| 7 STOP during initialization | PASS, injected synchronous signal |
| 8 repeated STOP | PASS |
| 9 SHUTDOWN | PASS, injected signal |
| 10 unexpected worker failure | PASS, injected synchronous throw |
| 11 initialization cancellation | PASS, cancelled task |
| 12 callback exception | PASS, injected signal-hook exception |
| 13 invalid configuration | PASS |
| 14 invalid/repeated transitions | PASS |
| 15 cleanup failure/cancellation | PASS |
| 16 no control before lifetime deadline | PASS |
| 17 failed registration/invalid handle | PASS, simulated registration false; no real handle |

Additional cases cover accepted/rejected failure STOPPED, initialization timeout and cleanup timeout. The tests exercise the controller, but do **not** execute the unmanaged ServiceMain callback, actual native status handles, native callback exceptions, or OS cleanup. They do not independently assert resource disposal through a disposable worker or exact error code for every test. Those remain test gaps, rather than native-only limitations. The non-cooperative timeout tests use never-completing synthetic tasks; deadlines bound the wait, not the lifetime of arbitrary work.

Native SCM registration, callback ABI, actual STOP/SHUTDOWN scheduling, status acknowledgement and identity separation remain **NOT RUN**. No service or OS policy was installed or changed. Further controller review and isolated Windows tests are required before operational use; no production capability is granted.
