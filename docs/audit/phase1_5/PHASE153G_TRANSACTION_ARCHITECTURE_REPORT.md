# Phase 1.5.3G transaction architecture

Date: 2026-11-07. Branch `phase1-5-recovery-security`; baseline HEAD `beeca449e127e01cb53576ea02c05522855766bd`. Existing uncommitted work is preserved. This design is not an authorization grant; verified task execution remains disabled.

## Existing trust and concurrency boundaries

`ConversationMemory` stores owner assignments in JSON. Cooperating operations acquire its in-process lock and a cross-process lock file; they reload JSON while locked. Deletion removes the record and a caller can later create a record with the same ID. At inspection time there was no conversation generation distinguishing these instances. A standalone SQLite owner mirror would therefore become stale unless all writers and recovery paths participate in a defined revocation protocol.

`TaskStore` and `ApprovalManager` use a shared SQLite database, but their methods start and commit separate operations; ordinary task updates use `INSERT OR REPLACE`. Existing task provenance explicitly permits only unknown/unverified records. Authentication uses request context and, in authenticated deployments, SQLite user/session records. The general agent can depend on arbitrary sources, so it cannot issue independent provenance. General task reads, approvals and resume remain fail-closed.

## Selected strategy and alternatives

Select **C**, subject to implementation of all prerequisites: keep JSON as the conversation authority and hold its cooperative lock while validating an immutable, server-created conversation generation and carrying out a *bounded, synchronous* SQLite transaction for a fixed arithmetic operation. The lock is never held across model inference, an agent loop, network access or arbitrary tools. Lock order must always be conversation lock, then SQLite write transaction; no path may acquire these in reverse. A stable generation must be introduced for new conversations; existing records without one remain ineligible until a separately reviewed, non-destructive migration. Deletion/recreation must produce a new generation. A current authenticated identity, session and clearance must be checked under an appropriate consistent authorization boundary immediately before sensitive actions; the implementation must account explicitly for local mode and concurrent role/session revocation. If these conditions cannot be established, deny the action.

A: moving all authoritative conversation ownership into SQLite could give a single transaction boundary, but requires a broader migration of every JSON ownership writer and careful recovery of pre-existing conversations. B: a SQLite mirror of JSON ownership could be transactional for tasks but cannot safely reflect JSON deletion/recreation without coordinated revocation and crash recovery; asynchronous mirroring is not authoritative. Neither is implemented here. C also depends on every cooperating JSON writer using the same lock, and it does not protect against direct external file changes, mismatched lock paths or privileged database modifications.

## Intended state machine (not enabled)

| State | Allowed transition | Conditions |
| --- | --- | --- |
| Pending approval | Completed and approval consumed | Current owner, clearance, session generation, exact plan/argument binding, pending unexpired approval and fixed policy revalidated in the same protected transaction. Compute only bounded, pure addition. |
| Pending approval | Cancelled | Authorized cancellation under the same protected boundary; invalidate the pending approval atomically. |
| Completed | Completed | Idempotent authorized read/retry returns the existing result; no second approval or calculation is recorded. |
| Cancelled | — | Terminal; do not execute. |

An intermediate execution claim is unnecessary for this tiny pure operation if approval consumption, result and final state commit together. A crash before commit rolls all of them back; a crash after commit leaves the completed result. This does not imply exactly-once external side effects. The existing quarantine remains separate and is never promoted merely because dependencies are empty.

## Planned migration and transaction boundary

No verified provenance schema, ownership mirror, session-generation migration, or production migration is enabled by this document. Any future additive SQLite schema must store versioned operation, arguments and digest, immutable task/owner/conversation-generation binding, creation clearance, approval ID and execution state; it must leave existing rows unverified. Review compatibility with existing `INSERT OR REPLACE` and task deletion before enabling it. Stop writers and take a consistent SQLite backup including WAL state before deployment. Rollback must account for post-migration records and cannot silently discard them.

The transaction primitive introduced in this pass is deliberately non-authorizing and not wired to application routes or the general agent. Its tests use synthetic disposable databases. The integration must independently validate trusted identity, role and clearance, implement generation/revocation, enforce plan and approval binding, and protect against all conflicting existing task/approval writes before use.

## Implemented foundation

`backend/agent/sqlite_transaction.py` supplies a dedicated SQLite connection, enables foreign keys, uses `BEGIN IMMEDIATE`, commits on success and rolls back on exceptions. Its lock wait is finite and restricted to at most five seconds. It does not authenticate or authorize, manage JSON locks, create provenance, add a task state machine, or consume an actual application approval. It is not called by any application route. No schema migration was added or run; this prevents an unused table or unreviewed schema from being mistaken for an authoritative binding. No runtime data was migrated.

`tests/backend/test_phase153g_sqlite_transaction.py` tests the primitive against a disposable existing-schema `TaskStore`. Its synthetic SQL models a conditional approval claim and task update in one transaction; it is test code, not a new approval implementation. It checks two competing writer threads, rollback after an injected exception and retry after reopening, a mismatched synthetic digest, foreign-key enforcement, a contended writer, and invalid timeout values. The rollback test simulates interruption with an exception; it is not an operating-system process-kill or power-loss test. It does not demonstrate a durable task lifecycle, current identity, clearance or conversation revocation.

## Tests and limitations

Windows, Python 3.13, synthetic temporary stores and installed dependencies. The command below ran with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and a temporary `sitecustomize.py` on `PYTHONPATH`. That file wraps Python socket `connect` and `connect_ex` to deny non-loopback destinations and permits literal loopback addresses for Windows asyncio. It is an application-level guard, not OS-level isolation.

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_sqlite_transaction.py tests/backend/test_phase153f_deterministic_policy.py tests/backend/test_phase153e_lifecycle.py tests/backend/test_phase153d_provenance.py tests/backend/test_phase153c_task_security.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase15_registry_continuation.py tests/backend/test_approval.py tests/backend/test_tasks.py tests/backend/test_task_recovery.py -q -p no:cacheprovider --tb=short
```

Final run: **76 passed, 0 failed, 0 skipped, 1 xfailed; 5.05 seconds**. The unchanged xfail is the existing artifact-collision guard. The run includes the existing synthetic L3-to-L1 task-detail test, quarantine tests and approval tests. An earlier run before the additional contention test had 75 passed and 1 xfailed. No real model or browser was used. The previously rejected full-suite command was not retried, and formal air-gap verification remains blocked.

The following required verified-path cases remain **NOT IMPLEMENTED / NOT RUN**: stale conversation generation, owner revocation, authenticated clearance downgrade on the new path, forged verified provenance, approved-plan mutation on that path, actual approval issuance/claim/consumption, application restart recovery and a real verified task lifecycle. Legacy quarantine and the existing downgrade guard passed in the targeted suite, but do not establish those missing guarantees. Existing compatibility tests were not rerun.

## Verdict and changed files

**PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** Option C is a conditional design with explicit prerequisites, not a demonstrated end-to-end authorization solution. Before enabling it, implement and test immutable conversation generations; cooperative revocation and lock ordering; fresh authenticated identity/session/clearance validation, including local mode; an additive verified provenance migration; atomic integration with existing task and approval records; cancellation and crash recovery; and the requested positive and negative route tests. Continue to withhold unknown and legacy content.

Files added in this pass: `backend/agent/sqlite_transaction.py`, `tests/backend/test_phase153g_sqlite_transaction.py`, and this report. Existing changes were preserved. No commit, push, merge or deployment occurred.

## Continuation: new conversation generation identifiers

New sessions created explicitly or implicitly now receive a random `conversation_generation` in their existing JSON record. It survives restart and changes on deletion followed by recreation of the same session ID. Existing sessions are not rewritten or assigned a generation; unknown fields remain preserved. The new field is not an authorization credential and is not used by task execution. In particular, old records preserve unknown fields, so the mere presence of a similarly named field cannot establish trustworthy server creation. A reviewed trust/migration rule and atomic validation under the conversation lock are still necessary. No task or approval route has been enabled.

Additional files changed: `backend/agent/memory.py` and `tests/backend/test_phase153g_conversation_generation.py`. Synthetic tests cover restart, delete/recreate, legacy preservation and implicit creation. With the same loopback-only Python socket guard and environment flags described above, the following targeted command ran:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_conversation_generation.py tests/backend/test_phase153g_sqlite_transaction.py tests/backend/test_phase151_conversation_records.py tests/backend/test_phase153d_provenance.py tests/backend/test_phase153c_task_security.py tests/backend/test_phase153_stabilization.py tests/backend/test_approval.py -q -p no:cacheprovider --tb=short
```

**41 passed, 0 failed, 0 skipped, 1 xfailed; 4.20 seconds.** This includes the existing downgrade guard and unchanged artifact collision xfail. This test run is separate from, and overlaps with, the earlier 76-pass run. It does not verify revocation during execution, transactional task binding, or OS-level isolation. The verdict remains **PARTIAL / NO-GO**.

## Trusted-binding continuation

Reinspection confirms that arbitrary unknown JSON fields survive loading and saving, so a legacy record can contain a syntactically valid `conversation_generation`. Its presence cannot distinguish server issuance from pre-existing data. The cross-process JSON lock protects cooperating memory operations, but task creation is a separate SQLite operation and currently stores only the asserted session ID. Existing task detail and approval/resume remain fail-closed. A trustworthy binding would require an independently protected issuance record or authenticated generation, reliable revocation and reconciliation, and an implementation that holds the JSON lock while committing the task and binding in SQLite. A digest or UUID by itself is insufficient. No schema, production data, task creation path or authorization behavior was changed in this continuation; trusted issuance and the requested binding remain unimplemented.

Added a synthetic regression demonstrating that a legacy record can preserve a plausible generation field while cross-owner conversation mutation remains denied. With `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`, the command `python -m pytest tests/backend/test_phase153g_conversation_generation.py tests/backend/test_phase153g_sqlite_transaction.py -q -p no:cacheprovider --tb=short` returned **16 passed, 0 failed, 0 skipped, 1 pytest configuration warning; 1.13 seconds**. These isolated unit tests do not establish trusted generation provenance or task authorization. The L3-to-L1 guard and artifact xfail were not rerun in this continuation; their earlier results above remain historical. Full-suite and OS-level isolation verification remain blocked. The only files changed in this continuation are this report and `tests/backend/test_phase153g_conversation_generation.py`. **PARTIAL / Phase 1.5.4 NO-GO.**

## Authoritative-registry investigation

Baseline remains `beeca449e127e01cb53576ea02c05522855766bd`, branch `phase1-5-recovery-security`; existing uncommitted work was preserved. `ConversationMemory.create_session` and `_get_or_create` create records, `_authorize` can claim ownerless legacy records in local mode, and `delete_session` removes records. All operate under the memory decorator's cooperating file lock, but none coordinates with task SQLite. `backend/main.py` constructs memory separately from the task store. Task provenance is still unverified. JSON generation and owner fields cannot by themselves establish server-issued registry identity.

**Registry implementation remains incomplete.** No registry table, migration, active authorization identity, task binding, or change to application behavior was added. A design must cover explicit and implicit creation, legacy claiming, deletion, startup recovery, and every writer before an active row is trusted. A candidate protocol is: acquire the conversation lock before the SQLite transaction; create a pending registry identity with a fresh generation; persist JSON; conditionally activate the exact pending identity in a subsequent SQLite transaction while retaining the lock. Pending rows are never authorizing. On deletion, revoke first under the lock before deleting JSON; failed or interrupted deletion leaves access revoked. Recovery must quarantine inconsistent or ambiguous states and must not silently promote legacy JSON. The exact schema, uniqueness constraints, concurrent stale-writer behavior, user/session revocation and crash-recovery implementation still require review and tests. No JSON/SQLite pair is claimed atomic. No transaction may span LLM calls or external tools.

Targeted synthetic command, with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`: `python -m pytest tests/backend/test_phase153g_conversation_generation.py tests/backend/test_phase153g_sqlite_transaction.py tests/backend/test_phase153d_provenance.py -k 'not new_unverified_metadata_does_not_grant_task_detail_access' -q -p no:cacheprovider --tb=short`. Result: **24 passed, 0 failed, 1 deselected, 1 pytest configuration warning; 1.66 seconds**. The deselected route test needs an asyncio environment; this run exercised no verified registry or new application integration. The downgrade and artifact guards were not rerun; previous results are historical. No full suite, live service, or OS-level egress isolation was verified.

Only this report changed in this continuation. **PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No commit, push, merge or deployment occurred.

## Conversation lifecycle consolidation

On branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`, preserved all prior uncommitted work. In `backend/agent/memory.py`, explicit `create_session` and implicit `_get_or_create` now both call `_lifecycle_create_if_missing`, the single code path for assigning new ownership and generation. `delete_session` calls `_lifecycle_delete`; `_authorize` delegates the pre-existing local-only legacy owner claim to `_lifecycle_bind_legacy_owner`, which independently checks the current principal and local-mode flag. Initial loading and synchronized reload call `_lifecycle_load`. The JSON structure, unknown fields, local legacy policy and task/artifact fail-closed rules remain unchanged. The generation remains untrusted for authorization.

Entry points remain `create_session`, message append methods (which may create), `delete_session`, authorization of ownerless legacy records, constructor loading and synchronized reload. Public synchronized operations retain the existing in-process and cooperative file locking. Constructor loading retains its existing unlocked initial read; `_lifecycle_load` reconstructs `_store` directly, and internal Python code or non-cooperating processes can still modify state without the lifecycle interface. Content/title mutations remain separate because they do not change conversation identity. These are not a secure registry or revocation system. No JSON/SQLite synchronization, registry migration, verified task binding, authenticated freshness check or crash-recovery protocol was added. Such integration must address initial load, direct internal access and non-cooperating writers before relying on this interface.

New tests `tests/backend/test_phase153g_conversation_lifecycle.py` cover explicit and implicit creation, restart, local legacy binding, cross-owner deletion denial, malformed legacy generation preservation and a synthetic two-thread create/delete race using independent memory instances. Existing generation tests cover delete/recreate and unknown-field preservation. Windows/Python 3.13, temporary fixtures: with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`, `python -m pytest tests/backend/test_phase153g_conversation_lifecycle.py tests/backend/test_phase153g_conversation_generation.py tests/backend/test_memory.py tests/backend/test_phase151_conversation_records.py -q -p no:cacheprovider --tb=short` returned **22 passed, 0 failed, 0 skipped, 1 pytest configuration warning; 1.83 seconds**. No full suite or external service was run. This is a thread-level test, not proof of cross-process crash safety or formal network isolation.

Files changed in this continuation: `backend/agent/memory.py`, `tests/backend/test_phase153g_conversation_lifecycle.py`, and this report. The broader registry and trusted task workflow remain unimplemented. **Phase 1.5.3G PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No commit, push, merge or deployment occurred.

## Opt-in SQLite registry foundation

This continuation adds `backend/agent/conversation_registry.py` and integrates it as an **optional, non-authorizing dependency** of `ConversationMemory`; application startup does not instantiate or wire it. No existing conversation is promoted and no task or approval code consumes registry state. New tests opt in using isolated SQLite and JSON paths. This is not an authoritative deployed registry or a verified task binding.

The additive `conversation_security_registry` table contains conversation ID, owner, unique generation, `pending`/`active`/`revoked` state, per-ID revision, creation and revocation timestamps. A partial unique index prevents two pending/active identities for one ID. The opt-in create path holds the existing conversation lock, commits a pending row, writes JSON atomically, rereads and compares the owner/generation, then activates the same pending row. Failed writes or activation leave pending state and never authorize access. The opt-in delete path revokes the live row before deleting JSON. Revoked rows cannot be reactivated by the provided transition. Recreated IDs receive a new generation and incremented revision. Registry writes use bounded `BEGIN IMMEDIATE` transactions after acquiring the conversation lock; no LLM or external tool runs inside them.

On opt-in construction, reconciliation acquires the conversation lock, reloads JSON and revokes active identities that no longer match owner/generation or lack a JSON record. Pending rows stay pending; missing and legacy JSON are never promoted. This is conservative but requires manual recovery of pending records, including a failed JSON write: no automatic retry or pending cleanup exists. Non-cooperating writers, filesystem rollback, tampering with SQLite, database replacement, lock-path aliases and partial or corrupt JSON remain outside the proven model. A matching JSON field is not independent proof of provenance. Registry methods are internal Python APIs and do not perform authorization; they must not be exposed as such.

Tests on Windows/Python 3.13, synthetic temporary paths: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, then `python -m pytest tests/backend/test_phase153g_registry.py tests/backend/test_phase153g_conversation_lifecycle.py tests/backend/test_phase153g_conversation_generation.py tests/backend/test_memory.py tests/backend/test_phase151_conversation_records.py -q -p no:cacheprovider --tb=short`: **26 passed, 0 failed, 0 skipped, 1 pytest configuration warning; 2.06 seconds**. Coverage includes new registration, restart, deletion/recreation, revoked transition denial, JSON-write failure, activation failure, pending quarantine, legacy spoofing and mismatched-active revocation, along with existing lifecycle tests. It does not demonstrate registry-enabled concurrent create/delete, process-kill recovery, authenticated clearance, multi-process behavior or application startup integration. The L3-to-L1 and artifact guards were not rerun; prior results remain historical. Full-suite and formal network isolation remain blocked.

Files changed in this continuation: `backend/agent/conversation_registry.py`, `backend/agent/memory.py`, `tests/backend/test_phase153g_registry.py`, and this report. Prior work is preserved. **PARTIAL / Phase 1.5.4 NO-GO** until startup wiring, recovery/coordination, authorization, registry-enabled concurrency and compatibility are reviewed and verified. No commit, push, merge or deployment occurred.

## Registry startup integration

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; prior uncommitted work preserved. The lifespan first validates configuration, creates runtime directories and opens auth/audit stores in `tasks_db_path`; after model-router construction it initializes one `ConversationRegistry` in that same database, creates conversation memory, attaches and reconciles the registry, and exposes the same instance as `app.state.conversation_registry`. TaskStore is opened later against the same database. The registry is non-authorizing and no task or artifact policy reads it. The additive table/index setup uses `CREATE ... IF NOT EXISTS` inside the existing SQLite write transaction. Errors propagate out of startup: there is no silent fallback. `ConversationMemory.attach_security_registry` refuses double attachment and non-persistent memory.

An optional `conversation_memory_path` setting provides explicit isolated JSON storage; its default retains the existing memory location/behavior. Tests and development configurations should set both this path and `tasks_db_path` to disposable paths. When pytest's implicit in-memory mode is active and no explicit storage is supplied, startup now fails rather than pretending that registry reconciliation works. A test may supply an explicitly persistent synthetic `ConversationMemory` instead. Existing legacy conversations are not inserted into the registry or promoted.

Added tests for two concurrent same-ID creates using separate memory instances, revocation failure preserving JSON, and rejection of activation after revocation. A synthetic real-app lifespan test confirms one attached instance, explicit and implicit creation, and a second startup with isolated JSON/database. Ollama health checks and the vector store were substituted; this is not live-model or live-vector evidence. It does not exercise authenticated browser workflows. The user-requested registry-enabled create/delete race, truly concurrent activation/revocation, subprocess termination, partial-deletion restart and explicit startup-failure regression remain **NOT RUN**. The earlier JSON/SQLite crash-recovery and non-cooperating writer limitations remain. The provided test for activation after revocation is sequential, not a concurrency proof.

Windows/Python 3.13, synthetic paths, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and a temporary Python socket wrapper allowing literal loopback only: 

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_registry.py tests/backend/test_phase153g_conversation_lifecycle.py tests/backend/test_phase153g_conversation_generation.py tests/backend/test_phase153g_sqlite_transaction.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py tests/backend/test_phase15_app_integration.py::test_real_app_lifespan_sessions_and_multipart -q -p no:cacheprovider --tb=short
# 34 passed, 1 xfailed; 11.62 seconds
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_registry_startup.py tests/backend/test_phase153g_registry.py -q -p no:cacheprovider --tb=short
# 8 passed; 8.56 seconds
```

The first run includes the existing L3-to-L1 guard and unchanged artifact collision xfail; the runs overlap. Socket wrapping is not OS-level isolation. The rejected full-suite command was not retried, no real model or external service was used, and formal air-gap verification remains blocked.

Files changed in this continuation: `backend/config.py`, `backend/main.py`, `backend/agent/memory.py`, `tests/backend/test_phase153g_registry.py`, `tests/backend/test_phase153g_registry_startup.py`, and this report. **PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No commit, push, merge or deployment occurred.

## Registry concurrency and failure recovery

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; existing uncommitted changes preserved. No application source was modified in this continuation. Cooperating lifecycle operations take the per-instance `RLock`, then the shared JSON lock file, reload JSON, and make bounded SQLite `BEGIN IMMEDIATE` calls. Pending registration commits before JSON replacement; activation commits afterwards. Deletion commits revocation before JSON replacement. These are distinct durable operations, not a distributed atomic transaction. Direct registry methods serialize through SQLite but do not take the JSON lock and are not authorization APIs.

| Case | Evidence and limit |
| --- | --- |
| Same-ID creation | Existing registry test uses two threads and separate memory instances; one active row. |
| Create/delete race | New bounded two-thread test with separate memory instances completes, restarts and checks ownership and revoked generations. A single scheduling run is not exhaustive. |
| Activation/revocation race | New bounded two-thread direct-registry test ends revoked regardless of tested ordering; reactivation fails. |
| Delete/recreate | Existing test verifies changed generation, revision increment and non-resurrection. |
| Cross-owner mutation | New test denies create/reuse and delete by another owner. |
| Restart | New tests cover pending registration, failed JSON deletion and concurrent operations; no task authorization is granted. |

Failure injection: pending registration failure writes neither JSON nor registry row; existing tests cover failed JSON creation and failed activation leaving pending, and failed revocation preserving JSON. A new JSON deletion failure test confirms revocation remains after restart, but the old JSON conversation remains readable to its existing owner under the current JSON policy. This is not a successful deletion and must not be mistaken for registry-authorized access. Reconciliation failure causes actual application startup to fail; an active mismatched row can remain in SQLite if revocation itself fails, but startup does not complete and the registry is not an authorization source. A disposable subprocess exits with code 23 immediately after committing pending registration; reopening leaves it pending. This tests one defined interruption point, not abrupt power loss or every filesystem durability boundary. No production process or storage was touched.

Targeted Windows/Python 3.13 run, synthetic temporary storage, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and a Python-level socket wrapper allowing literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_registry_recovery.py tests/backend/test_phase153g_registry.py tests/backend/test_phase153g_registry_startup.py tests/backend/test_phase153g_conversation_lifecycle.py tests/backend/test_phase153g_conversation_generation.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py -q -p no:cacheprovider --tb=short
# 30 passed, 1 xfailed; 12.20 seconds
```

The run includes the L3-to-L1 disclosure guard, unverified approval/resume denial and unchanged artifact-collision xfail. An earlier narrower run before the final added cases returned 20 passed and one pytest configuration warning. Socket wrapping does not prove OS-level isolation. No rejected full-suite retry, model inference or external service was used.

Remaining gaps: cross-process create/delete and activation/revocation races, process interruption at other boundaries, power-loss durability, registry/JSON recovery when non-cooperating processes modify storage, authenticated clearance and registry-based task binding remain unverified. Pending identities may require manual intervention. Conversation authorization still uses JSON, and registry rows must not be treated as granting rights. Tests added/changed: `tests/backend/test_phase153g_registry_recovery.py` and `tests/backend/test_phase153g_registry_startup.py`; this report was updated. **PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No commit, push, merge or deployment occurred.

## Proposed registry-backed conversation authorization policy

For any identity proven registry-managed, access should require the current server principal to be its owner, a live active registry row, and an exact current JSON/registry owner and generation match. Pending, revoked, missing or mismatched identities must be unavailable for listing, reads, updates and deletion; no administrator bypass is proposed. Legacy access must be a separate explicit policy, without inferring trust from a JSON generation field. Current document-derived content clearance must continue to be enforced at the existing server history boundary. JSON and SQLite checks must remain valid through the sensitive operation; revocation and reads must use a shared coordination protocol. This policy is a target, not yet implemented.

### Investigation result

**PARTIAL / NO-GO.** No application authorization code was changed. `list_sessions`, `is_owned_by`, `_authorize` and history methods use JSON ownership and do not enforce registry state. `chat.py` history and deletion routes call these memory methods; agent paths also use memory history and append methods. The existing list/detail handler performs multiple separate calls, so it does not itself hold a lock across all steps. Direct `ConversationRegistry.revoke` can run without the JSON conversation lock while a memory operation is in progress. An authorization check followed by a JSON read/write would therefore have a revocation race unless all relevant callers are coordinated or an appropriate shared transactional guard is held through the operation. Long-running streams and in-flight context are not addressed by a one-time check.

The legacy boundary also needs an independent durable distinction: if a managed row is missing, existing JSON records, including arbitrary preserved generation fields, cannot prove whether the record was historically managed or legacy. Allowing all no-row records as legacy could bypass a missing-row denial; denying them all would change the explicitly permitted existing legacy behavior. Registry absence or wholesale replacement cannot be safely interpreted as a trusted legacy indicator without an additional trusted migration/enrollment and recovery design. No blanket legacy promotion or silent access-policy change was made.

Added `tests/backend/test_phase153g_registry_authorization_guard.py` with two strict expected failures demonstrating that a revoked conversation remains listed and its history remains readable. These are **open security gaps**, not passing authorization tests. With `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`, `python -m pytest tests/backend/test_phase153g_registry_authorization_guard.py tests/backend/test_phase153g_registry_recovery.py tests/backend/test_phase153g_registry.py -q -p no:cacheprovider --tb=short` returned **14 passed, 2 xfailed, 0 skipped, 1 pytest configuration warning; 3.24 seconds**. The L3-to-L1 and artifact guards were not rerun in this continuation; their previous results remain historical. No real model, full-suite retry or OS-level network-isolation verification was performed.

Files changed in this continuation: this report and `tests/backend/test_phase153g_registry_authorization_guard.py`. No commit, push, merge or deployment occurred. Before enabling the target policy, implement a trusted legacy enrollment/managed-identity distinction and a single enforceable coordination protocol across all registry revocations and conversation operations, then add route, concurrent revoke/read, clearance and restart tests. Existing task/artifact fail-closed controls remain unchanged.

## Phase 1.5.3G.1 lifecycle coordination

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`. Existing uncommitted changes were preserved. Memory-driven pending registration, activation, deletion revocation and reconciliation revocation now pass through `_lifecycle_registry_mutation`, which refuses calls unless the persistent memory decorator has acquired its conversation lock. The lock order remains the per-instance reentrant lock, shared JSON lock file, then a short SQLite transaction. SQLite is not held during JSON writes or unrelated agent work. Errors propagate. Registry SQL activation remains conditional on the matching pending identity, so revoked rows cannot be reactivated by stale activation.

Direct calls to `ConversationRegistry.begin`, `activate` and `revoke` remain possible and do **not** acquire the conversation lock. They are unsupported as conversation-lifecycle or authorization boundaries; this change does not close races involving them. `_lock_depth` is an internal cooperative programming guard, not a defense against arbitrary Python code, external SQLite writers, alternate lock paths, or filesystem tampering. Registry remains non-authorizing; legacy JSON policy is unchanged. No task, approval, resume or artifact access was enabled.

New synthetic tests check out-of-lock mutation denial, registry lock-error propagation, and a second memory instance's reconciliation while creation is paused after JSON persistence. Existing tests exercise creation/deletion and direct activation/revocation races with bounded test waits. Windows/Python 3.13, temporary storage, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1` and a Python-level socket wrapper allowing literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_lifecycle_coordination.py tests/backend/test_phase153g_registry_authorization_guard.py tests/backend/test_phase153g_registry_recovery.py tests/backend/test_phase153g_registry.py tests/backend/test_phase153g_conversation_lifecycle.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py -q -p no:cacheprovider --tb=short
# 27 passed, 3 xfailed; 7.77 seconds
```

Two xfails are the unchanged revoked-conversation list/history security guards; the third is the unchanged artifact-collision guard. The L3-to-L1 task-disclosure guard and unverified approval/resume tests passed. Thread tests are not cross-process race proof; no cross-process coordination test or OS-level isolation verification was performed. The rejected full suite was not retried. This continuation changed `backend/agent/memory.py`, added `tests/backend/test_phase153g_lifecycle_coordination.py`, and updated this report. **PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No commit, push, merge or deployment occurred.

After the above run, the lock guard was tightened to verify the calling thread owns the tracked lifecycle lock, not merely that another thread holds it. The final focused rerun `python -m pytest tests/backend/test_phase153g_lifecycle_coordination.py tests/backend/test_phase153g_registry_authorization_guard.py tests/backend/test_phase153g_registry_recovery.py tests/backend/test_phase153g_registry.py -q -p no:cacheprovider --tb=short`, with pytest plugin autoload and bytecode disabled, returned **17 passed, 2 xfailed, 1 pytest configuration warning; 5.47 seconds**. The broader 27-pass run preceded this final guard tightening; it was not repeated afterward.

## Phase 1.5.3G.2 conservative classification

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; prior uncommitted work preserved. Added internal `ConversationRegistry.classify`: an observed latest revoked row returns `revoked`, while active, pending, missing, malformed, corrupt or unreadable registry state returns `unknown_quarantined`. This is a conservative diagnostic result only, never an access grant or change to existing conversation authorization. It does not offer `verified managed` or `known legacy` because the available SQLite and JSON stores cannot prove those claims under rollback, replacement or missing metadata. In particular, no row is not evidence of legacy status, and a JSON generation field is not trusted. A later row for a recreated conversation yields unknown, not the earlier revoked identity.

SQLite is opened read-only for classification; absent files/tables and database errors produce unknown rather than implicitly creating storage. No schema migration or data mutation is performed by classification. It does not detect restoration of an older valid database; instead an apparently active row remains unknown. A trusted monotonic external anchor or a different verified recovery/trust design would be needed before making a freshness claim. External database or filesystem tampering, row deletion, old snapshots, cross-process writes and database corruption remain outside the demonstrated trust boundary. Unknown does not currently block JSON conversation reads because this foundation is not wired into authorization; the two revoked-conversation authorization guards remain expected failures.

Tests use synthetic isolated data for new and legacy records, forged JSON fields, missing rows/databases, corrupt files, revoked identities, delete/recreate state, restart, backup/restore of an older SQLite snapshot and concurrent transitions. Windows/Python 3.13, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`: `python -m pytest tests/backend/test_phase153g_classification.py tests/backend/test_phase153g_registry_authorization_guard.py tests/backend/test_phase153g_registry.py tests/backend/test_phase153g_lifecycle_coordination.py -q -p no:cacheprovider --tb=short` returned **15 passed, 2 xfailed, 1 pytest configuration warning; 4.30 seconds**. The xfails are the unchanged revoked-conversation access guards. The first run returned 14 passed, 1 failed and 2 xfailed because an open SQLite connection prevented deletion on Windows; the classifier and test fixtures were corrected to close connections, and the final result above is after that correction. L3-to-L1 and artifact-collision guards were not rerun; earlier results are historical. No full-suite retry, outbound request, real-model test or OS-level isolation verification occurred.

Files changed: `backend/agent/conversation_registry.py`, `tests/backend/test_phase153g_classification.py`, and this report. **PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No verified managed or known-legacy state, registry-backed authorization, task approval/resume or artifact access was enabled. No commit, push, merge or deployment occurred.

## Phase 1.5.3G.3 observed-revocation containment

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; earlier uncommitted changes preserved. `ConversationMemory.list_sessions` (including `session_count`) excludes sessions whose latest registry identity is observed revoked, and `get_history` denies them with a generic `PermissionError`. These checks run inside the existing synchronized memory method before legacy ownership binding or history retrieval. The chat list and history routes and agent history construction use these methods. `ConversationRegistry.observed_revoked` performs a read-only query and propagates storage errors; memory converts errors to a generic denial without exposing paths. The diagnostic `classify` still maps registry failures to unknown, but it is not used for this decision. No row, pending and active remain under the existing JSON ownership/legacy policy; they are not classified verified.

The original two strict expected-failure markers were removed only after their unchanged assertions passed. Tests additionally cover active and legacy access, cross-owner denial, injected registry read errors, restart after revocation with failed JSON deletion, and a bounded thread test where memory-driven deletion pauses after revocation while another memory instance attempts listing/history. The JSON remains on disk after deletion failure but listing and history deny it. The first test run returned **33 passed, 1 failed, 1 xfailed** because the race test incorrectly expected empty history where the new policy correctly returned denial; the expectation was corrected, not the authorization check.

Final targeted run on Windows/Python 3.13, isolated synthetic storage, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and Python socket wrapper permitting literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_registry_authorization_guard.py tests/backend/test_phase153g_classification.py tests/backend/test_phase153g_registry_recovery.py tests/backend/test_phase153g_registry.py tests/backend/test_phase153g_lifecycle_coordination.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py -q -p no:cacheprovider --tb=short
# 35 passed, 1 xfailed; 8.26 seconds
```

The xfail is the unchanged artifact collision; the L3-to-L1 task disclosure guard and unverified task denial tests passed. Python socket wrapping is not OS-level isolation. No full-suite retry or real model was used.

**Limitations:** containment is only for observed latest revocation on listing/history. `get_title`, `session_exists`, `is_owned_by`, writes and other internal methods do not enforce this new check; some can still disclose metadata or mutate content. Direct SQLite registry writers do not share the conversation lock and can revoke after a query before its result is used. List and history route calls are separate operations; in-flight model/stream output is not continuously revalidated. Registry deletion, database replacement, rollback to a pre-revocation snapshot and corruption cannot be fully distinguished from unknown legacy state; a read failure is denied but an older valid snapshot may hide revocation. The test exercises cooperating threads, not cross-process races or power-loss safety. No task or artifact authorization changed. **PARTIAL / Phase 1.5.4 NO-GO.**

Files changed: `backend/agent/conversation_registry.py`, `backend/agent/memory.py`, `tests/backend/test_phase153g_registry_authorization_guard.py`, and this report. No commit, push, merge or deployment occurred.

## Phase 1.5.3G.4 access-path matrix (inspection)

| Operation | Entry point before this change | Existing revocation check |
| --- | --- | --- |
| List/count | `list_sessions`, `session_count` | Yes |
| History | `get_history` | Yes |
| Title/read, title/write, clear | `get_title`, `set_title`, `clear_session` via `_authorize` | No |
| Existence | `session_exists` via `_authorize` | No |
| Ownership | `is_owned_by` | No |
| Append or implicit creation | `add_*_message` via `_lifecycle_create_if_missing` / `_authorize` | No |
| Delete | `delete_session` via `_lifecycle_delete` / `_authorize` | No |
| Explicit creation/reuse | `create_session` via `_lifecycle_create_if_missing` | No |

Chat list/history/title/delete routes and agent calls use these methods; artifact checks use `is_owned_by`. Existing method-level JSON locks do not cover independent registry writers or entire multi-call routes. The intended narrow change denies observed revocation and registry read failures at common user-operation boundaries, while allowing only explicit creation after JSON deletion has completed. Unknown and legacy entries retain their current owner policy. This is containment, not verified authorization.

### Implementation and results

`_authorize` now checks observed revocation before JSON ownership and local legacy binding, covering `session_exists`, `get_history`, title reads/writes, clear, deletion, existing-session creation/reuse, and all message appends. `is_owned_by` returns false for observed revocation and propagates a generic denial for registry query failure; listing/count retains its existing revocation filter. Calls occur inside existing synchronized memory operations. Unknown and legacy states continue under the existing policy and do not become verified. Query failures become generic `PermissionError` without paths. No task or artifact permission policy changed.

The sole lifecycle exception is an **explicit** `create_session` for an ID whose JSON record is absent: it may create a fresh registry generation even if an older generation is revoked. Implicit message append and existence checks cannot recreate such an ID. If deletion revokes but JSON replacement fails, deletion raises and the old JSON remains; normal operations now deny it, including a retry through the public deletion method. There is no new administrative cleanup/recovery interface. A completed delete followed by explicit create obtains a new generation, leaving old registry records revoked. Direct registry calls, non-cooperating SQLite/filesystem writers, stale snapshots and in-flight streams remain outside this containment; route multi-call operations are not globally atomic and registry rollback can hide a revocation. An old generation cannot be trusted as current authorization based only on these checks.

Synthetic isolated targeted run on Windows/Python 3.13 with pytest plugin autoload and bytecode disabled and a Python socket wrapper permitting literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_complete_revocation.py tests/backend/test_phase153g_registry_authorization_guard.py tests/backend/test_phase153g_classification.py tests/backend/test_phase153g_registry_recovery.py tests/backend/test_phase153g_registry.py tests/backend/test_phase153g_lifecycle_coordination.py tests/backend/test_phase153g_conversation_lifecycle.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py -q -p no:cacheprovider --tb=short
# 41 passed, 1 xfailed; 9.65 seconds
```

The xfail is the unchanged artifact collision; L3-to-L1 task disclosure guard passed. The first run had **39 passed, 2 failed, 1 xfailed** because the test update accidentally inverted an active-record assertion and left the failed-deletion expectation unchanged; the assertions were corrected and the run above passed. The failed-deletion ownership assertion was deliberately strengthened from true to false to reflect the requested denial. After that run a cooperating-thread revocation/write test was added and the focused `python -m pytest tests/backend/test_phase153g_complete_revocation.py -q -p no:cacheprovider --tb=short` returned **4 passed, 1 pytest configuration warning; 1.63 seconds**. The broad run preceded that test-only addition. Python socket wrapping is not OS-level isolation; no rejected full suite was retried.

Changed files: `backend/agent/memory.py`, `tests/backend/test_phase153g_complete_revocation.py`, `tests/backend/test_phase153g_registry_recovery.py`, and this report. **Containment PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No commit, push, merge or deployment occurred.

## Phase 1.5.3G.5 application boundary audit

Baseline branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; prior uncommitted work preserved.

| Boundary | Code and current handling |
| --- | --- |
| Session list/count | `chat.list_sessions` calls synchronized memory list/count separately; revoked state is checked independently in each call. |
| Session history/title/task summaries | `chat.read_session` makes multiple calls and builds output from snapshots. It now rechecks owner/generation/revocation before return; direct registry writers can still race after that check. |
| Session title/delete | Chat routes call locked memory methods. JSON and SQLite remain separate and direct registry writers do not share the JSON lock. |
| User/assistant/system append, creation, reuse | Engine and routes use memory methods that reject observed revocation. Already-loaded messages and local variables are not automatically invalidated. |
| Normal, tool, tracked, planner, image and arithmetic SSE | Main SSE generators now use a wrapper that snapshots memory identity and checks before advancing after the first event and before releasing each event. It closes the wrapped generator after observed denial; existing memory persistence rejects observed revocation. |
| Approval intercept, non-streaming chat, external writers | Approval intercept has separate local generators; general resume is already fail-closed, but these generators are not wrapped. Non-streaming response assembly and direct registry mutation remain separate paths. |

Confirmed synthetic plain SSE bypass: a model generator could yield a second chunk after registry revocation and the wrapper emitted it. The regression test failed before the fix. The new outer guard rechecks the synchronized memory identity and stops with a generic SSE error when revocation, missing conversation or a changed owner/generation is observed. It does not hold locks while awaiting the underlying generator. `read_session` now checks an identity snapshot again before returning; a synthetic test revokes after metadata retrieval and gets 404. `stream_identity` is a short-lived comparison aid, not verified provenance or an authorization grant. Pending and unknown identities still follow the earlier ownership policy.

Boundaries remain: direct SQLite writers do not honor the conversation lock; a revocation can occur after the final check but before bytes are sent. Already-sent bytes cannot be recalled. Before the first yielded event of a newly created session there is no snapshot; a generator can execute work between checks, and already-started inference or a tool is not guaranteed cancelled. Underlying generator closure is requested on denial but arbitrary background tasks and side effects are not proven stopped. Model outputs or tool results may have reached logs, tools or caches before the guard observes a change. Separate approval intercept generators are not covered, and non-streaming results need a dedicated final revalidation/identity-binding audit. In-flight clearance downgrades are not independently reloaded by this guard. `memory._store` remains directly accessible to internal Python and legacy JSON/registry rollback is not detectable from these checks. No complete assurance is claimed for streaming, multi-call routes, direct writers or cross-process races.

Synthetic Windows/Python 3.13 tests, temporary storage, pytest plugin autoload/bytecode disabled, Python socket wrapper permitting literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_stream_revocation.py tests/backend/test_phase153g_complete_revocation.py tests/backend/test_phase153g_registry_authorization_guard.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py tests/backend/test_phase15_app_integration.py::test_real_app_lifespan_sessions_and_multipart -q -p no:cacheprovider --tb=short
# 20 passed, 1 xfailed; 10.03 seconds
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_stream_revocation.py tests/backend/test_phase153g_complete_revocation.py tests/backend/test_phase153g_registry_authorization_guard.py -q -p no:cacheprovider --tb=short
# 12 passed; 3.34 seconds
```

The initial regression test before the guard failed (1 failed); the first broad run includes the unchanged artifact collision xfail and L3-to-L1 guard. After that broad run, an initial identity check for existing sessions was added; the second focused run reflects the final code. The app lifespan test substitutes Ollama and vector boundaries; no live model or OS-level isolation was verified. No full-suite retry occurred. Changed files: `backend/api/chat.py`, `backend/agent/memory.py`, `tests/backend/test_phase153g_stream_revocation.py`, and this report. **PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No commit, push, merge or deployment occurred.

## Phase 1.5.3G.6 response boundary containment

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; pre-existing uncommitted changes preserved.

| Response path | Current behavior |
| --- | --- |
| Chat approval-intercept denial/resume SSE | Existing session identity captured at request entry and supplied to the shared event guard; checked before generator advancement and output. If no session exists, return only the generic existing unverified-task error, without resuming or consuming approval. Actual general task resume remains fail-closed. |
| Non-stream `POST /api/chat` | Captures identity for an existing session before inference and rechecks owner/generation and observed revocation after model result/source conversion and before constructing the response. For newly created sessions no pre-inference generation exists; the final check still requires a currently authorized session. |
| Conversation history | Prior `.5` identity check before response return remains. |
| Task details/approvals | Prior `.3C` routes return redacted metadata or deny sensitive approval data; no new task permissions. The test suite includes the downgraded-owner guard, but this pass does not establish live clearance refresh. |

Two synthetic regressions failed before changes: non-stream response returned content after revocation, and approval-intercept SSE emitted buffered synthetic content after revocation before iteration. Chat now wraps the two intercept generators with the existing SSE guard and passes the request-entry identity as its initial comparison, and rechecks non-stream identity before return. Additional synthetic tests cover revocation during intercept streaming, generation replacement, registry query failure and cross-owner denial. Tests mock the intercepted resume stream and do not imply real approval is allowed. The unbound-session path deliberately returns the existing generic task-content-unavailable error and does not call resume.

Validation on Windows/Python 3.13 with isolated synthetic paths, pytest plugin autoload and bytecode disabled, and a Python-level socket wrapper permitting literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_response_boundaries.py tests/backend/test_phase153g_stream_revocation.py tests/backend/test_phase153g_complete_revocation.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py tests/backend/test_phase15_app_integration.py::test_real_app_lifespan_sessions_and_multipart -q -p no:cacheprovider --tb=short
# 20 passed, 1 xfailed; 9.26 seconds
```

The initial two reproduction tests both failed before the fix. The first broader run after the initial fix returned 19 passed, 1 failed, 1 xfailed: an existing test with a task but no conversation expected the previous fail-closed task error. The unbound-session path was then made to return that error without consuming approval; the final run above passed. The xfail is the unchanged artifact filename-collision guard; the L3-to-L1 guard and existing approval-denial test passed. Model and vector boundaries in the app-lifespan test were substituted. Python socket guards do not prove OS isolation. The rejected full suite was not retried.

**Remaining limitations:** checks are not atomic with sending response bytes or with independent SQLite writes. Already-sent bytes cannot be recalled. In-flight current clearance is not reloaded or proven fresh; sensitive task approval/resume remains denied rather than claiming clearance-change protection. New-session requests lack a pre-inference identity; direct registry rollback or replacement, background work, tools between checks, and non-cooperating writers remain outside the proven boundary. History task status summaries and task status metadata are not proven safe against every concurrent clearance change. No comprehensive API/browser/real-model audit or OS-level egress verification was performed. **PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.**

Files changed in this continuation: `backend/api/chat.py`, `tests/backend/test_phase153g_response_boundaries.py`, and this report. Nothing was committed, pushed, merged or deployed.

## Phase 1.5.3G.7 new-session binding and consolidated assessment

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; all pre-existing uncommitted work preserved. For a new chat or multimodal session, `prepare_new_chat` now executes under the memory lifecycle lock, requires an existing configured persistent registry and matching server request principal, creates the JSON and registry identity through the existing pending/write/activate lifecycle, verifies the active row matches owner and generation, and returns the request snapshot before inference or tool dispatch. It preserves the engine system prompt. Chat and multimodal streams receive that initial snapshot through the event guard; non-stream chat compares it before return. The existing unbound approval-intercept path returns its fail-closed error before preparation. Existing conversations keep their current legacy behavior; a legacy JSON generation is not treated as verified provenance.

Registration or activation errors stop the request before inference. A failed activation can leave JSON plus a pending row; pending rows are now treated as unavailable by memory authorization/list checks, including on retry. This is quarantine, not automatic repair. Database query errors are denied. Creating a session occurs before image processing, so a later invalid image can leave an empty registered conversation; this is a compatibility/cleanup consideration. New requests without persistent memory, a registry, or a bound server principal now fail closed. Existing legacy sessions are not automatically promoted. There is no new task approval or artifact authorization.

The original synthetic new-session test failed before implementation because inference started before session creation. Windows/Python 3.13 final targeted run, isolated temporary storage, pytest plugin autoload and bytecode disabled, Python socket wrapper permitting literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153g_new_session_binding.py tests/backend/test_phase153g_response_boundaries.py tests/backend/test_phase153g_stream_revocation.py tests/backend/test_phase153g_complete_revocation.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py tests/backend/test_phase15_app_integration.py::test_real_app_lifespan_sessions_and_multipart -q -p no:cacheprovider --tb=short
# 27 passed, 1 xfailed; 9.63 seconds
```

Synthetic mocked tests cover new plain SSE, non-stream chat, tool SSE, planner SSE and image SSE; registry pending and activation failures prevent mocked inference, including a retry after activation failure. Earlier response-boundary tests cover revocation, generation replacement, cross-owner rejection, and approval interception; the L3-to-L1 guard passes and the unchanged artifact collision remains xfailed. This is not a live model, real vector-store, browser or complete end-to-end demonstration. Python socket wrapping does not establish OS-level isolation; the rejected full suite was not retried.

### Consolidated assessment

- **Implemented and targeted-tested:** owner-aware JSON conversation checks; observed revoked/pending containment; new-session pre-work binding to an active registry row; request/stream identity rechecks; generic registry failure denial; legacy quarantine from verified provenance; unverified task detail redaction and approval/resume denial; artifact access denial.
- **Implemented but not fully verified:** registry/JSON coordination and crash recovery across processes, identity checks through real long-running streams, and application compatibility outside the targeted synthetic paths. Conditional checks and event release are not one atomic operation.
- **Known unresolved defects:** artifact metadata keyed by filename can collide (strict xfail); independent direct SQLite registry writers bypass the JSON lifecycle lock; a registry database rollback can hide prior revocation. Existing tests do not prove all metadata, tool, or logging disclosure paths are closed.
- **Architectural limitations:** SQLite plus JSON cannot independently prove freshness under replacement/rollback; current user clearance and account state are not reloaded continuously during in-flight inference; in-flight tools/background tasks and already-sent bytes cannot be recalled. Legacy/unknown records are not trusted for verified task provenance. Multiple processes and non-cooperating writers need a stronger shared protocol.
- **Compatibility and release blockers:** task approval/resume and artifact viewing remain unavailable under fail-closed controls; pending registration can require manual intervention; startup requires persistent memory and registry. Live local model, browser, full-suite, air-gap and verified sandbox checks are incomplete. Full suite remains blocked by the prior automatic security-review rejection of an unidentified outbound request; no override or retry was performed.

Files changed in this continuation: `backend/api/chat.py`, `backend/agent/memory.py`, `backend/agent/conversation_registry.py`, `tests/backend/test_phase153g_new_session_binding.py`, and this report. **Phase 1.5.3G PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No commit, push, merge or deployment occurred.
