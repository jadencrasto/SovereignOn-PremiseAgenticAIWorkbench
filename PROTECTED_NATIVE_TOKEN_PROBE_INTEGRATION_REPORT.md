# Protected native token probe integration

**Status: BLOCKED; requested native integration is not implemented. Production NO-GO.** The service does not execute the storage probe and `ServiceTokenProbe.NativeExecution()` remains unsupported. The work in this report adds only an offline, synthetic approval-gate model and does not establish VM identity, approval, storage access or Windows token security.

## Changes and results

- `prototype/windows_service_foundation/ProtectedProbeGate.cs` models a fixed operation and target, Protected profile, VM and computer identifiers, manifest digest, snapshot reference, short expiry, and in-process nonce consumption. It accepts injected values only and is not wired to SCM, Windows APIs or storage.
- `prototype/windows_service_foundation/ProtectedProbeGateTests.cs` tests that model; `Program.cs` exposes only the offline `protected-probe-gate-selftest` selector. Normal service startup and the existing Protected fixture are unchanged.
- `dotnet build .\prototype\windows_service_foundation\ServiceFoundation.csproj --no-restore --disable-build-servers`: PASS, 0 warnings, 0 errors.
- `ServiceFoundation.dll protected-probe-gate-selftest`: 15 passed, 0 failed, including matching synthetic input, VM/name mismatch, digest errors, missing approvals, missing snapshot, profile mismatch, wrong operation/target, expiry, repeated and concurrent requests.
- `ServiceFoundation.dll service-token-probe-selftest`: 20 passed, 0 failed. `protected-fixture-selftest`: 11 passed, 0 failed. `scm-selftest`: 33 passed, 0 failed, ABI PASS. `selftest`: PASS.

The model's booleans are caller-provided test data and do not prove a reviewed package, operator approval, VM isolation or snapshot availability. Its nonce set exists only in one object in one process; it is not durable and does not prevent replay after restart or across processes. Its target-string comparison is not filesystem identity or reparse-point protection. The tests do not inspect real tokens, execute a storage probe or validate native cancellation. No existing approval or security gate relies on this model.

## Required integration and exact outstanding work

The K fixture runs inside the SCM service process and checks its Windows token, but its current worker opens an outbound observation-only pipe and performs no storage probe. The N probe is in the separate SecurityFoundation executable and uses `WindowsInspector`, `ExpectedIdentity`, retained filesystem handles and a fixed synthetic fixture policy. An interactive session of the same account is not a restricted service token. The intended solution remains a reviewed fixed in-process service worker sharing the actual N implementation, without arbitrary paths, executable launch or application requests.

The native path is not enabled because it still lacks an independently anchored Protected-run approval and replay store, an exact binding between that approval and the pinned manifest/package/VM/snapshot, a safe way to retain and verify approved synthetic root identities through the operation, and a reviewed output channel bound to the actual service process. The existing manifest pins the old service command, so an alternate service mode also requires an independently reviewed manifest and revised validation. The current synchronous native filesystem opens are not proven forcibly cancellable; a managed timeout does not establish completion or safe handle release. These are unfinished engineering tasks, not a claim that the Windows API makes the requested integration impossible.

The synthetic gate intentionally cannot select native execution. In particular, its process-local replay test is not a substitute for an independently protected, restart-aware one-use approval. Do not treat a VM marker, service flag, self-reported token, same-account process, PID or hash supplied by the service as an independent trust anchor.

## ACL and identity verification still required

The actual service process must be independently observed to have the pinned broker user, expected service SID in enabled and restricted groups, expected integrity, no forbidden privileges and no thread impersonation. The broker-token probe must perform fixed synthetic checks through retained reviewed handles. Distinct ordinary and alternate users must run their own access checks under their actual effective tokens. An operator/admin is a provisioning authority and may bypass ordinary ACLs; do not infer denial from ACL strings. Native positive and negative results are **NOT RUN**.

Windows 10 Home support for the complete provisioning, restricted service token, effective ACLs and cross-account token inspection remains unverified in the actual VM. Missing UI is not proof of missing underlying APIs. Service registration, identity and ACL provisioning, and the exact snapshot and per-run Protected authorization require separate review and approval in a disposable VM. Preserve Lifecycle's fixed service name/configuration separately. Native egress calibration and network-sensitive tests remain blocked.

No native service, SCM, token, ACL or VM tests were executed. No service, account, firewall, ACL or host configuration was changed. Existing package and snapshot evidence is not independent certification. Production remains **NO-GO**.
