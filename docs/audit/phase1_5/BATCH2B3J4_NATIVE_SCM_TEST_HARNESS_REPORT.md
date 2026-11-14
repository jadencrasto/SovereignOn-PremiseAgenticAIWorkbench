# Batch 2B.3J.4 — Disposable-VM SCM verification harness

**Production NO-GO. Native scenarios were not run.** The harness and offline suite are ready for independent safety review. No service was installed or started during implementation.

## Implementation and safety

`tests/backend/run_batch2b3j4_native_scm.ps1` requires PowerShell 7 on Windows, an explicit approval flag, a local elevated session, a fixed local non-reparse workspace, the independently selected VM UUID and computer name, a matching workspace marker, and a recognized VM model. These checks enforce the selected target and reduce operator error. They cannot attest that a machine is disposable or that its snapshot is recoverable.

The runner verifies and holds read handles for the four reviewed build files, reports the executable SHA-256, and refuses a conflicting `SovereignDevBrokerFoundation` registration. It creates a demand-start own-process service under the existing Windows LocalService identity. It checks the registered image, service type, startup type and account before mutating the service. It starts only the fixed executable and passes only the reviewed test arguments. It checks bounded status polls and runs local `sc.exe` by system-directory path with an eight-second command wait, bounded output collection and bounded cleanup of the command process it created. The status parser expects English field labels and refuses unfamiliar output. The 12-second polling budget can extend by one bounded query already in progress. Local OS calls and storage faults can still require the VM operator's external deadline.

Cleanup runs only after confirmed creation and matching configuration. The runner verifies absence after deletion, reports a failed cleanup, and treats ambiguous creation as requiring snapshot reversion. Configuration checks do not serialize a competing administrator between SCM calls. Use a dedicated VM with no concurrent service administration and a protected staging directory. Deletion covers this SCM registration only. Snapshot reversion remains the complete rollback.

## Native scenarios

| Scenario | Implementation and evidence |
|---|---|
| Registration, RUNNING, normal STOP and STOPPED | Implemented and mock-tested. Native status and zero exit codes await VM. |
| Restart and repeated STOP | Implemented and mock-tested. Two starts and stops. Repeated STOP on STOPPED expects 1062. |
| Unexpected service-process exit | Implemented and mock-tested. An exact SCM-only development argument exits the worker process with code 23 after a short RUNNING window. Harness expects SCM 1067. The worker's exit action was tested offline with an injected callback. |
| Initialization failure and invalid configuration | Implemented and mock-tested. Fault mode throws in the real deny-only initializer. Unknown startup argument fails validation. Harness expects 1066 with service-specific 7 and 1, respectively. |
| Control during initialization | Implemented and mock-tested. Held initializer stays START_PENDING until its deadline. Harness expects SCM STOP refusal 1061 and terminal 1066/13. SCM does not advertise STOP or SHUTDOWN during START_PENDING. |
| Bounded supervision and cleanup | Implemented and mock-tested including query timeout, ownership mismatch, ambiguous creation and deletion failure. Native timing and removal await VM. |
| Service endpoint and protected storage | No such operations in the development worker. Run only with staged binaries and synthetic data. OS handle and activity observations await VM. |

Development startup arguments are parsed only by ServiceMain. `--j4-fail-initialization` injects an initializer error, `--j4-hold-initialization` waits for initialization cancellation, and `--j4-exit-process` simulates unexpected process exit. `--j4-invalid-configuration` is deliberately unsupported and tests rejection. Ordinary command-line entry points do not accept these as service launch switches. No fault mode enables an endpoint, grants authorization, opens protected storage or terminates another process. [ControlService](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-controlservice)

## Offline verification

`tests/backend/test_batch2b3j4_native_scm.ps1` passed **17 cases, 0 failures**. It exercises missing approval, unsupported platform and PowerShell, invalid VM prerequisites, missing binary and bad hash, service conflict, failed and ambiguous creation, startup and STOP timeouts, failed cleanup, configuration replacement, the full simulated sequence, status parsing, polling limits, and actual preflight/file checks with temporary synthetic files. A direct invocation without approval returned exit 1 with `category=approval_required`. Both new scripts and the existing H.8 and I.2 runners passed PowerShell parser checks. The .NET foundation build passed with 0 warnings and 0 errors, foundation self-test passed, and 33 controller cases passed with the ABI layout check.

## Operator instructions for an independently approved disposable VM

Review the scripts and SCM executable first. Stage `ServiceFoundation.exe`, `ServiceFoundation.dll`, `ServiceFoundation.runtimeconfig.json` and `ServiceFoundation.deps.json` from the reviewed .NET 10 Windows build in a dedicated local folder. The VM requires a compatible .NET 10 runtime. Obtain all four SHA-256 hashes from the independently reviewed artifacts before staging. Record the VM UUID and computer name from approved inventory and place the UUID as the only content of `.sovereign-j4-disposable-vm` in the staging folder. Confirm snapshot and isolation before authorizing installation. Use an external VM deadline and revert the snapshot after collecting the sanitized result.

The following template installs and starts a temporary SCM service. Fill in the independently verified values and run only in the approved VM from elevated PowerShell 7.

```powershell
$hashes = @{
    'ServiceFoundation.exe' = '<reviewed SHA-256>'
    'ServiceFoundation.dll' = '<reviewed SHA-256>'
    'ServiceFoundation.runtimeconfig.json' = '<reviewed SHA-256>'
    'ServiceFoundation.deps.json' = '<reviewed SHA-256>'
} | ConvertTo-Json -Compress
.\tests\backend\run_batch2b3j4_native_scm.ps1 -ApproveDisposableVm `
    -Workspace '<absolute VM staging path>' -ExpectedVmUuid '<approved VM UUID>' `
    -ExpectedComputerName '<approved VM computer name>' -ExpectedHashesJson $hashes
```

No native result is claimed. Identity provisioning, OS storage boundaries, protected installation, server authentication and SCM behavior remain separate verification gates.
