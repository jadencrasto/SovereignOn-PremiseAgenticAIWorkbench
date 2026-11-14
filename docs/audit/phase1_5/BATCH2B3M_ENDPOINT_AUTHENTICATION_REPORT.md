# Batch 2B.3M — Broker endpoint verification

**Production NO-GO.** The endpoint verifier can inspect and compare a connected server against independently pinned service evidence. Native mode is observation-only, including on a match. Native pipe and token verification were not run.

## Implementation

`prototype/windows_security_foundation/EndpointBinding.cs` uses the Part L inspector and an injected pipe inspector. `Program.cs` adds a categorical observation entry point. `SecurityTests.cs` tests the same sequence with deterministic process and pipe leases.

1. Reject missing expected identity and commands outside ping, status and capabilities.
2. Verify SCM configuration and protected installation before opening the endpoint.
3. Connect only to the pinned local pipe. The observation transport opens the pipe read-only at Identification impersonation level. It has no request-write method.
4. Read the connected pipe server PID and owner/DACL on that same pipe handle. Compare an opened server process handle with the retained expected handle using `CompareObjectHandles`. Check liveness, token and SCM consistency. Refuse an unknown descriptor or an untrusted create-instance or control grant.
5. Reinspect the service, compare process objects across the snapshots, reread pipe evidence and recheck SCM configuration and liveness. Release every lease on completion or failure.
6. Only an injected synthetic transport may yield a development operation decision. Native results always emit `authority=false`. A PID or SID match alone cannot authorize a request.

The observation connection limits exposure from the existing development client's Impersonation level without changing that client's protocol or peer-SID verification. Retained handles reduce PID reuse ambiguity. They cannot make the process, service and endpoint snapshots atomic or prove the server will remain alive after verification. [CompareObjectHandles](https://learn.microsoft.com/en-us/windows/win32/api/handleapi/nf-handleapi-compareobjecthandles) · [GetNamedPipeServerProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeserverprocessid)

## Tests and native harness

`SecurityFoundation.dll selftest endpoint` passed **21 cases, 0 failures**. Coverage includes matching synthetic evidence, native nonauthorization, fake or same-account endpoints, wrong and reused PID, process exit, changed pipe or SCM config, missing trust, unsupported operation, inaccessible token/handle, wrong executable or service identity, unsafe pipe permissions, interrupted and timed-out connection, failed recheck, cancellation disposal and concurrent independent leases. The isolated .NET build completed with zero warnings and errors. Existing broker protocol, launcher and SCM offline tests passed.

`tests/backend/run_batch2b3lm_native_inspection.ps1` and `tests/backend/batch2b3lmn_vm_probe.ps1` provide the bounded VM harness for both L and M. The script requires explicit approval, a pinned VM UUID/computer name, a local non-reparse staging directory with `.sovereign-lmn-disposable-vm` containing the UUID, and four independently reviewed SHA-256 values for `SecurityFoundation.exe`, `SecurityFoundation.dll`, `SecurityFoundation.runtimeconfig.json` and `SecurityFoundation.deps.json`. Supply their hashes as a JSON object keyed by filename. Stage the independently authored `expected-service.json` and pass its reviewed digest separately. A compatible .NET 10 runtime is required in the approved VM.

```powershell
pwsh -NoProfile -File .\tests\backend\run_batch2b3lm_native_inspection.ps1 `
    -ApproveDisposableVm -Workspace '<reviewed absolute VM staging directory>' `
    -VmUuid '<approved UUID>' -ComputerName '<approved VM name>' `
    -ProbeHashesJson $reviewedProbeHashesJson -ExpectedManifestSha256 $reviewedManifestHash `
    -Probe observe-identity -ExpectedFinding VerifiedEvidence
```

Use `-Probe observe-endpoint` only once an independently provisioned protected service exposes the pinned synthetic endpoint. `-ExpectedFinding` supports deliberate negative observations. The helper supervises its own observer with a 15-second process wait and bounded termination. It never installs, starts, stops or deletes a service. Parser checks passed and invocation without approval returned exit 1 before probing.

The unchanged SCM foundation has no pipe endpoint. A positive native endpoint case therefore requires a separately provisioned service fixture with reviewed pipe ownership, token and ACLs. Same-account endpoint replacement and live token/handle behavior remain native adversarial checks. Server-side caller authentication for that future endpoint is not implemented here. The existing development broker retains its current SID checks. The storage simulator independently rejects a missing caller identity. No native message dispatch or production endpoint capability was enabled.
