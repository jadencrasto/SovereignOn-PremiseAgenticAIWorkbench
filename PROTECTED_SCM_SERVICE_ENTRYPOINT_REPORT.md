# Protected SCM service entry point review

**Decision: BLOCKED. Production NO-GO.** A genuine SCM lifecycle entry point already exists, but a separately authorized protected token diagnostic was not made reachable in this batch. No additional synthetic authorization model or standalone mock dispatcher was introduced. Storage probing remains disabled. These are unfinished engineering and provisioning requirements, not a claim that Windows cannot support them.

## Actual host and token path

`Program.Main` calls `NativeScm.Dispatch` only for the explicit normal `service` command or the already-existing protected observation fixture configuration. `Dispatch` calls `StartServiceCtrlDispatcherW`; the registered `ServiceMain` constructs the shared `ScmController`, then chooses either `DenyOnlyWorker` or `ProtectedFixture.Create`. The protected observation fixture already performs some real token checks inside `ProtectedFixture.Open` when it is eventually run as an SCM service: process/thread identity through `WindowsIdentity.GetCurrent`, configured broker SID, no built-in administrator group, `IsTokenRestricted` and service-SID membership. It opens its existing observation pipe. That existing path is not a new token-diagnostic dispatch, does not establish all required effective token/privilege evidence and has not been verified in a native protected service.

`ServiceHostedStorageProbe.Dispatch` always returns `Disabled`; its private `InspectCurrentProcess` has no service-host caller and would invoke storage access after token checks. It cannot be used as the requested token-only diagnostic. `WindowsInspector.InspectToken` can inspect a real primary token and capture user, enabled and restricting groups, integrity, forbidden privileges and token statistics, but a token observation cannot independently authenticate the expected identity or authorize the operation. No storage probe was invoked.

## Precise blockers to safe reachability

1. `ExpectedIdentity.Read` checks a manifest against a digest supplied to it, but the SCM host has no independently protected source for that digest, per-run Protected authorization or trusted service configuration for a new token-only diagnostic. The existing protected fixture's `--approved-vm-fixture` argument and broker SID are startup inputs, not independent approval or a pinned trust anchor. The current manifest schema pins the **existing** service/fixture binary command, not a token-diagnostic mode. Accepting a new command-line SID, digest, VM UUID or self-reported boolean would manufacture trust.
2. There is no reviewed, authenticated evidence channel binding a diagnostic result to the intended SCM process and independently pinned executable/configuration. A service self-report, same-account SID, PID or pipe name does not establish that binding. Existing observation pipes do not expose an authorized diagnostic protocol.
3. A Protected-run authorization and durable replay policy cannot be inferred from the in-memory `ProtectedProbeGate` or Lifecycle approval. While storage probing unquestionably requires durable one-use authorization and separately pinned target identities, the authorization and output contract for **token-only** observation must also be specified without borrowing those untrusted inputs. Native token calls are not proven to complete within the controller's deadline; managed cancellation cannot safely terminate a blocked native call.

It would be possible to add a new command or have the existing fixture call `WindowsInspector.InspectToken`, but without an independently provisioned expected identity and an authorized diagnostic path that would violate the specified fail-closed boundary. Therefore no source code, entry point or authorization policy was changed merely to report progress. The next implementation must establish protected delivery of the expected manifest/digest and diagnostic authorization, a fixed token-only worker with no storage call, a process-bound evidence channel and lifetime behavior, and offline tests of that *actual* dispatch before any separately approved native test.

## Offline verification

`dotnet build .\prototype\windows_service_foundation\ServiceFoundation.csproj --no-restore --disable-build-servers`: **PASS, 0 warnings, 0 errors**.

| Existing offline selector | Pass | Fail | Scope |
| --- | ---: | ---: | --- |
| `scm-selftest` | 33 | 0 | Shared controller and ABI layout with injected behavior |
| `protected-fixture-selftest` | 11 | 0 | Fixture worker and synthetic boundaries |
| `service-hosted-probe-selftest` | 12 | 0 | Disabled storage dispatcher and shared synthetic token/ACL helpers |

Commands used `dotnet .\prototype\windows_service_foundation\bin\Debug\net10.0\ServiceFoundation.dll <selector>`. No native SCM registration/start, real protected token query, ACL/storage access, VM operation, application integration or host security configuration change occurred. These tests do not show that a new token diagnostic is reachable or verified. No new service-host dispatch tests were added because no safe dispatch exists.

## Native prerequisites

Independently provision and review a protected service identity, restricted service SID, executable/configuration and SCM security, an independently protected expected manifest/digest and authorized evidence consumer. Separately approve VM identity/isolation, a recoverable protected-profile snapshot and evidence retention; Lifecycle is a different fixture. Verify real token contents and cleanup under the actual service process, including account/SID lists, token privileges, integrity and thread impersonation, with distinct native negative cases. Windows Home support and required read rights remain unverified. Protected storage ACL testing, independent writer exclusion and production authorization remain separate gates. No service, account, ACL or policy modification is authorized by this report.
