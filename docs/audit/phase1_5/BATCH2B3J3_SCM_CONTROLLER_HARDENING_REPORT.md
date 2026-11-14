# Batch 2B.3J.3 — SCM controller hardening

**Production NO-GO.** The shared controller and regression suite passed offline. Windows SCM registration, dispatch, callback delivery and status acknowledgement await authorized VM verification.

## Implementation

- `prototype/windows_service_foundation/ScmController.cs` owns one asynchronous worker, cancels and joins initialization or work, then cleans up and disposes it in order. It distinguishes publication attempts from acknowledgements and owns every status transition. Registration failure skips status calls. An initial publication failure permits one failure-coded terminal attempt without entering RUNNING. A final attempt, successful or rejected, closes publication.
- The control callback coalesces STOP and SHUTDOWN with an atomic flag and a continuation-safe signal. It performs no cancellation or cleanup on the SCM callback thread. An injected callback exception requests shutdown with a failure result. INTERROGATE returns success without republishing status.
- `prototype/windows_service_foundation/NativeScm.cs` maps controller status to the real `SERVICE_STATUS` structure. The P/Invokes specify exact entry points, native BOOL returns where applicable, and a two-entry dispatch table with a null terminator. The dispatcher and handler delegates remain rooted. Startup selectors for the J.4 cases are limited to the fixed development service.
- `prototype/windows_service_foundation/ScmTests.cs` uses tracked workers and the same controller that native ServiceMain calls. Tests check operation order, one acquisition and disposal, terminal attempts and acknowledgements, and actual status-field construction.

Shutdown waits five seconds in native mode. A shutdown timeout reports process failure and leaves the last acknowledged pending state. The controller does not publish STOPPED while work, cleanup or disposal is still outstanding. The shutdown task retains ownership and can complete deferred disposal if the outstanding operation cooperates. Process exit can end that opportunity. Native API calls and arbitrary tasks cannot be forcibly interrupted by managed deadlines. The development service's worker lifetime is 60 seconds.

## Exit status contract

An acknowledged normal STOPPED has Win32 code 0 and service-specific code 0. A failure-coded STOPPED uses `ERROR_SERVICE_SPECIFIC_ERROR` (1066) and the applicable code below. The internal result maps to process exit 0 only on an acknowledged successful STOPPED, and to 1 otherwise. A rejected final publication leaves the last acknowledged state intact. When the final status fails after an earlier failure, the earlier failure remains the internal cause.

| Case | Internal code | Final status behavior |
|---|---:|---|
| Normal termination | 0 | STOPPED acknowledged with 0/0 |
| Invalid startup configuration | 1 | Failure-coded STOPPED after START_PENDING |
| Registration exception or invalid handle | 2 | No status call |
| START_PENDING rejected | 3 | One failure-coded STOPPED attempt |
| RUNNING rejected | 4 | No acknowledged RUNNING. Failure-coded STOPPED |
| STOP_PENDING rejected | 5 | Failure-coded STOPPED after cleanup |
| STOPPED rejected after otherwise successful shutdown | 6 | Attempt contains success fields. No acknowledgement, process fails |
| Initialization exception | 7 | Failure-coded STOPPED without RUNNING |
| Unexpected worker completion or failure | 8 | Failure-coded STOPPED after cleanup |
| Cancellation callback fails | 9 | Failure-coded STOPPED after work and cleanup |
| Cleanup fails | 10 | Disposal still attempted. Failure-coded STOPPED |
| Disposal fails | 11 | Failure-coded STOPPED, no retry |
| Control callback exception | 12 | Failure-coded STOPPED |
| Initialization deadline | 13 | Failure-coded STOPPED if shutdown completes |
| Worker lifetime deadline | 14 | Failure-coded STOPPED if shutdown completes |
| Shutdown deadline | 15 | No STOPPED publication. Pending work retains ownership |

These rules follow Microsoft's one-call guidance for final STOPPED publication. [SetServiceStatus](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-setservicestatus)

## Completion audit

| Requirement | Evidence |
|---|---|
| Ownership, normal/error disposal, cancellation, timeout and no double disposal | Offline passed. Gates exercise disposal after normal stop, initialization and worker faults, cancellation callback failure, and deferred disposal after blocked initialization, work and cleanup. Disposal failure and blocked disposal are separate cases. |
| Exact error and acknowledgement behavior | Offline passed. Shared publisher records ordering, acknowledgements and status fields for normal, rejected and throwing paths. |
| STOP during initialization and immediately after RUNNING | Offline passed. Initialization gate and publication hook. |
| Repeated STOP, active SHUTDOWN and concurrent STOP/SHUTDOWN | Offline passed. Gate-controlled worker and cleanup. |
| Worker fault during STOP and cancellation during cleanup | Offline passed. Fault gate and control during held cleanup. |
| Initialization, worker lifetime and cleanup deadlines | Offline passed. Controlled tasks exercise the configured bounds. |
| Invalid handle, registration and native publisher failures | Offline passed with injected failures. Real Win32 errors await native testing. |
| SCM layout and process result | Layout checks passed for 28-byte status, field offsets and two-pointer table. Native ABI execution and actual SCM exit reporting await the VM. |
| Deny-only behavior | Foundation self-test passed. Worker has no endpoint or protected-content operations. |

`.NET SDK 10.0.404` build with `--no-restore` passed with **0 warnings and 0 errors**. Foundation self-test and SCM self-test passed with **33 passed, 0 failed** plus the ABI layout check. Both existing broker runner scripts parsed successfully. The broker and launcher implementation files were left intact. No Windows service or protected store was opened.
