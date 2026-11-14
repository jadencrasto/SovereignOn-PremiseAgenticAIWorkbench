# Batch 2B.3N — Protected storage isolation foundation

**Production NO-GO.** The policy and transaction model are synthetic only. Production decisions unconditionally return `Disabled`. Native access enforcement was not exercised.

## Implementation

`prototype/windows_security_foundation/StorageBoundary.cs` adds opaque resource identifiers and explicit operations for documents, SQLite state, vector indexes, artifacts, audit records, recovery and the freshness witness. Requests carry no paths, SQL, payloads or generic commands. The decision requires a verified caller and synthetic broker binding, current request context and recovery evidence, an exact grant bound to the same context instance and request, safe resource evidence, and a matching witness generation.

The in-memory coordinator reserves one writer, rechecks revocation, resource and witness evidence at completion, and consumes the reservation and grant on finish. A synthetic document/index compound grant explicitly covers both resources and requires the companion's own protection evidence. Transaction and partial-write fixtures check rollback. A rollback failure quarantines the instance. No operation can carry a production backend through this contract. `SecurityTests.cs` covers the actual policy and coordinator.

## Windows enforcement required before real storage

An independently provisioned restricted broker identity must hold the only ordinary mutation rights for protected stores. ACLs must cover parents, files, SQLite WAL/SHM and journal files, vector index files, and all aliases. A writable parent can invalidate a file-only deny. The application account and other local users must lack create, write, append, delete, delete-child, ACL modification and ownership changes across these boundaries. Protected ancestors and pinned handles are needed to prevent namespace substitution. Previously opened writable handles and mappings must be excluded before admission.

| Class | Application identity | Broker identity | Independently provisioned authority |
|---|---|---|---|
| Documents, SQLite state, vector index and artifacts | No direct protected access | Only approved operations within its store roots | Approved backup/recovery identity with separate controls |
| Audit records | No direct mutation | Append through a separate policy | Audit administration and export |
| Recovery state | No direct access | Controlled reconciliation while writes are fenced | Independent approval and restore review |
| Freshness witness | No direct access | Read verified observations without rewriting history | Separate durable authority outside broker write control |

OS administration and privileged backup/restore can bypass ordinary access checks. Real writer exclusion requires effective-access checks, restricted service-token execution, inventory of inherited handles, a durable fencing protocol, and independent freshness and rollback evidence across stores.

## VM access probe and verification

`StorageAccessProbe.cs` and `tests/backend/run_batch2b3n_native_access.ps1` implement a separate approved-VM probe using the shared bounded supervisor. The operator stages seven named synthetic files in `synthetic-storage-j4` under the protected staging workspace, plus an empty `.synthetic-only` marker. Use fixture names `document.fixture`, `sqlite.fixture`, `vector.fixture`, `artifact.fixture`, `audit.fixture`, `recovery.fixture` and `witness.fixture`. Stage no real content. Give the test identities enough metadata access to validate fixtures, then provision the exact expected mutation rights independently.

Run under each independently selected non-elevated test account with `-Role application` or `-Role broker` and `-ExpectedCallerSid`. Supply the same VM approval, identity, workspace and reviewed probe hashes as in the M report. The probe compares the actual caller SID, rejects reparse ancestors, opens existing fixtures with individual requested rights and closes the handles without writing. It also tests that neither identity can mutate the fixture directory or its parent. It requires application denial for all tested rights, ordinary broker file writes/deletes for its synthetic stores, append only for the audit fixture and no write to the witness. Sharing and path errors fail the test rather than counting as access denial. Testing a broker account interactively cannot substitute for testing the restricted service process token. This test uses flat pre-existing fixtures and does not establish directory provisioning rights for a production broker.

`SecurityFoundation.dll selftest storage` passed **27 cases, 0 failures**. Cases include production denial, exact context and grant binding, forged capability and bypass attempts, unsafe identifiers and traversal, reparse and inherited writes, single-resource versus compound privilege, missing and stale witnesses, competing writers, revocation, interruption, failed transactions, partial writes, rollback, quarantine and the native access expectation matrix. The isolated build completed with zero warnings and errors. All repository PowerShell test runners parsed. The two new VM runners refused unapproved invocations. Native access checks remain **NOT RUN**.

## Combined L–N completion matrix

| Requirement | Classification | Evidence or precise dependency |
|---|---|---|
| L expected identity, strict validation and negative authorization | Implemented and tested | Independently pinned immutable manifest, policy and 24 offline cases. |
| L SCM configuration, user/service SID, integrity, privilege and executable identity observations | Implemented, awaiting native verification | Local Windows APIs, retained handles and layout checks. Requires independently provisioned protected service. |
| L read-only installation/configuration permission inspection | Implemented, awaiting native verification | NTFS ancestry, final path and handle pins, owner/DACL and conservative mutation checks. |
| L loaded image and existing inherited or duplicated handle attestation | Not implemented | The documented path and current-file APIs do not attest prior mappings or all inherited writers. Needs protected deployment and independently reviewed OS evidence. |
| M expected-process and pipe rechecks, liveness, permission and timeout/cleanup cases | Implemented and tested | Shared verifier with 21 injected cases. No native result unlocks dispatch. |
| M local Identification connection and native server-process/descriptor inspection | Implemented, awaiting native verification | Bounded observation runner. Existing deny-only service has no pipe. A separately provisioned protected endpoint fixture is required for a positive native observation. |
| M native authenticated commands and new endpoint caller authentication | Not implemented | Native endpoint, independently enforced identity, and loaded-image/handle evidence are unverified. Existing broker retains its development SID gate. |
| N typed capabilities, caller, broker and recovery checks, writer and failure policy | Implemented and tested | 27 offline cases in the deny-only prototype. |
| N effective mutation-handle probes for synthetic resources | Implemented, awaiting native verification | Separate VM runner under independently selected caller identities. Native restricted-token and concurrent-process verification remains required. |
| N OS-enforced production writer exclusion, durable cross-store recovery and independent witness | Not implemented | Depends on verified identity and endpoint, provisioned ACLs, handle exclusion and independent durable storage evidence. The in-memory witness cannot establish these. |
| Regression and repository restrictions | Implemented and tested | Foundation and broker builds/self-tests passed. SCM 33, J.4 harness 17, IPC schema 9. Existing SCM host, IPC broker, launcher, production authorization and stores preserved. |

No service, VM, model, database or production application was started. Production authority stays disabled.
