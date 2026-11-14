# Batch 2B.3I — Windows broker security review

**Production decision: NO-GO.** The operator reports successful native development IPC, including commands, negative requests, duplicate attempt, disconnect, stall and restart. This is prior functional evidence, not a new adversarial test or proof of OS isolation.

## Trust boundary and broker identity

The application client, broker process, OS endpoint, future storage authority and independent freshness witness are distinct trust boundaries. The prototype has no production storage authority or witness. Same-account SID equality identifies an account, not a trusted process. First-instance protection prevents another instance while the handle is held but does not authenticate the server; a squatter can create the endpoint first or during the current close/recreate gap. The client accepts sanitized replies from an unauthenticated endpoint and grants local impersonation capability. Its `trusted_broker=false` marker is not a security mechanism. Do not send sensitive data or credentials.

Strong server verification remains unsupported and disabled as an authority: no verified launcher/endpoint binding exists. A future design needs a trusted launcher retaining a process handle across endpoint verification, verified pipe server process identity bound to that handle without PID reuse races, endpoint ownership and lifetime checks, protected executable and configuration and a separately provisioned restricted service SID or OS identity. Same-user writable executable/configuration, already-open handles, process replacement and same-account attackers invalidate weaker claims. A dedicated service identity and administrator-managed protected installation/storage ACLs require separately approved provisioning. No service or permissions were installed here.

## Pipe and impersonation review

Static inspection: `CreateNamedPipe` uses duplex, overlapped, first-instance and remote-client rejection, one instance, explicit protected DACL with one current-user SID ACE and mask `0x00100183`. The mask contains read/write data, read/write attributes, read control and synchronize; it excludes `FILE_CREATE_PIPE_INSTANCE` (`0x4`). Server creation is performed with its own returned handle; client uses the same explicit mask. No generic rights are explicitly granted. Disconnect and close use handle/stream operations; their actual effective-access requirements, owner/default mandatory label, low-integrity, AppContainer and another-account behavior remain unverified on real handles. No assumption is made that a pipe path gives filesystem reparse protections.

The client requests impersonation, permitting an endpoint to impersonate locally. The broker synchronously calls `RunAsClient` to read the client's OS token SID, checks for residual thread identity afterward and compares the captured process SID. Token level and reversion must be tested using actual Windows tokens; a PID or client JSON identity is not an authority. Less-powerful identity mechanisms require separate Windows verification and were not substituted.

## Hardening and lifecycle

Previously, an exception during `RunAsClient` bypassed the cleanup check and could leave the code continuing without establishing thread reversion. `Program.cs` now performs the cleanup check after success or exception and exits the broker if cleanup cannot be verified. This is a fail-closed improvement, not proof of actual OS reversion. Existing self-test simulations exercise a separate decision helper; they do not verify this native exception path or reversion. Further injectable tests are needed.

Connection, read, and write have separate cancellation deadlines and the runner supervises process lifetimes; cancellation tokens alone do not prove native completion. Idle connection timeouts and disconnect errors can consume the bounded 32-event diagnostic budget, hiding later errors. Pipe instances are closed and recreated between clients, creating an ownership gap; a duplicate failure observed during a held instance does not rule out a race. Shutdown just before a client connects, cancellation races and abrupt termination remain unverified. No diagnostic suppression was introduced.

## Verification and adversarial plan

* `dotnet build prototype/windows_broker_dotnet/Broker.csproj --no-restore`: PASS, exit 0, 0 errors, 8 CA1416 warnings.
* `dotnet prototype/windows_broker_dotnet/bin/Debug/net10.0/Broker.dll selftest`: PASS, exit 0; offline synthetic protocol and SID-decision checks only.
* PowerShell runner parser: PASS, exit 0. No new native IPC tests were run by Codex. Existing runner was inspected; its own child processes are bounded and cleaned up, but it does not test malicious clients or endpoint ownership.

In a separately approved disposable Windows environment, create synthetic isolated test identities outside this batch and run individual bounded, unique-endpoint scenarios: authorized client; genuinely different-SID rejection; same-account endpoint squatting; fake broker with valid-looking response; retained process-handle and replacement/exit races; low-integrity and AppContainer client; simultaneous clients; read/write cancellation and shutdown races; actual pipe-handle DACL/owner/mandatory-label and access checks; interrupted exchange and restart. Supervisors must record only categorical results, bound each process and terminate only their own process tree. Do not alter host-wide ACLs or services. All these adversarial tests are NOT RUN, and no claims of their results are made.

Next: add injectable tests for cleanup on exceptions and termination, then independently review a protected launcher/service identity and server-handle binding before implementing storage isolation. OS-enforced storage ownership, independent-writer exclusion, durable freshness and cross-store recovery remain prerequisites.
