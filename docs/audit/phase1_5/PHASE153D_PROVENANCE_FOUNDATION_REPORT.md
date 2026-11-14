# Phase 1.5.3D provenance foundation report

Date: 2026-11-07. Branch `phase1-5-recovery-security`, HEAD `beeca44`; the working tree already contained uncommitted Phase 1.5.3B/1.5.3C code, tests and reports, which were preserved. Result: **PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No real task data was migrated or intentionally modified, and no commit, push or deployment occurred.

## Scope and architecture

The 1.5.3C fail-closed task/artifact controls remain in place. This pass implements a quarantine-only starting contract in the existing task SQLite database; it does **not** create verified tasks or grant access. `task_security_provenance` contains the stable existing task ID, schema version 1, server-context-bound owner when available, asserted conversation ID, creation time, verification state `unverified`, dependency state `unknown`, and NULL dependency/classification fields. SQL constraints reject attempts to set a verified state, known dependencies or classification under this schema. No API accepts client-supplied provenance. The conversation ID is not independently verified by this table; all records remain unverified. Role, clearance, source/chunk versions, tool and prior-output lineage are not captured or propagated. Empty or unknown dependencies cannot imply low classification. The new table is a quarantine envelope, not the complete shared authorization contract requested.

For tasks created while a server `effective_owner` context is bound, `TaskManager.create_task` rejects a different supplied owner and fills in an omitted owner from the bound context. Without a bound context, owner attribution in the envelope is NULL and the task remains unverified. New task row and envelope insert occur in one SQLite transaction. Duplicate creation is rejected. For tasks with envelopes, later ordinary updates cannot reassign the task's owner or conversation, checked under `BEGIN IMMEDIATE`. Normal task deletion also deletes the envelope. Existing tasks have no envelope and remain legacy/unverified; no ownership or classification is backfilled. Historical records are not automatically trusted.

The table deliberately has no SQL foreign key to `tasks` because the existing update implementation uses `INSERT OR REPLACE`; non-cooperating external SQL can still create orphans. No access decision may rely solely on the envelope without checking the current task and trusted dependencies. The SQLite file and server process are trusted boundaries; this schema does not protect against a privileged database editor. Schema CHECKs require a reviewed migration before future verified dependency states can be represented. Creating the table is additive on application startup; before applying this code to a real installation, stop writers and back up the SQLite database including WAL-related state. Rollback requires restoring compatible code and a verified backup; later task changes could be lost. No production backup or deployment was performed in this pass.

## Acceptance and operational limitations

- L3-to-L1 task-detail disclosure guard and deterministic plain-chat continuity guard remain passing in the targeted suite. Legacy/unverified task content stays redacted.
- No task is classified verified. Authorized L1 task details, task approval and resume remain blocked for all users; approval binding/replay protections remain intact but the route cannot be exercised. No approval is consumed by the deny path. Safe side-effect-free resume is not implemented.
- Artifact opaque IDs, collision-free file creation, staging, registration and reconciliation were **not implemented**. The original artifact ownership-collision guard remains xfailed. All artifact list/preview/download and `artifact_verifier` remain blocked by the earlier fail-closed policy. No artifact data was migrated.
- No new task-derived assistant message is marked source-independent. Current history gating remains in place. Real source classification revalidation, transitive dependencies, same-filename artifacts across owners, authorized artifact access and current-clearance task resume are not established.
- A positive authorized L1 workflow was not demonstrated. No real browser, local model, Chroma or OS-level egress isolation verification occurred. The Python socket guard in existing route tests is not OS isolation. Task approval and artifact access remain severe functional regressions, not completed features.

## Tests

Targeted Python tests used a fresh temporary Git archive of HEAD with current uncommitted backend and selected tests overlaid, isolated synthetic storage, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`:

```text
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153d_provenance.py tests/backend/test_phase153c_task_security.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase15_registry_continuation.py tests/backend/test_approval.py tests/backend/test_tasks.py tests/backend/test_task_recovery.py -q -p no:cacheprovider --tb=short
# 48 passed, 0 failed, 0 skipped, 1 xfailed; 3.68s
```

The xfail is the existing filename-based artifact-owner collision guard, not a pass. New synthetic tests cover server-bound owner, spoofed owner denial, unknown background/legacy records, atomic creation/rollback, immutable owner/conversation, restart, SQL quarantine constraints, denial of content access for new unverified tasks and concurrent creation. Existing tests cover fail-closed task access and internal approval/replay. No real models or external services were used.

An existing compatibility run before the final task-store immutability and SQL CHECK additions used the same isolated method:

```text
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase15_app_integration.py tests/backend/test_phase153_security.py -q -p no:cacheprovider --tb=line
# 4 passed, 2 failed, 0 skipped; 12.13s; pytest exit 1
```

The two unchanged tests still expect an approved task to resume and an owner to list an artifact; Phase 1.5.3C intentionally blocks both. This run was not repeated after the final task-store changes; no claim is made about the exact compatibility count after them. Frontend `node --experimental-strip-types --test tests/historyRequests.test.mjs`: 4 passed, 0 failed, 0 skipped; helper tests only. Frontend lint/typecheck/build were not rerun in this pass because no frontend files changed; earlier results remain historical.

## Changed files in this pass

`backend/agent/task.py`, `backend/agent/task_store.py`, `backend/security/task_access.py` (the last was an existing untracked 1.5.3C file), `tests/backend/test_phase153d_provenance.py`, this report and appended audit addenda. Earlier uncommitted files including task/agent/artifact fail-closed code and reports were not discarded.

## Next work

Define and implement trusted capture and atomic propagation of actual source/chunk versions, prior-output and tool dependencies, current classification checks, identity and session ownership. Then restore authorized task detail and approval/resume with approval revalidation and replay protection, and implement collision-safe opaque artifact identity and staged writes. Preserve legacy quarantine and add genuine positive and negative authorization/reclassification tests. The previously rejected full suite and formal network isolation remain BLOCKED; do not retry the rejected request or bypass automatic review.

## Phase 1.5.3E addendum — 2026-11-07

PARTIAL / NO-GO: no trustworthy verified task execution or provenance transition was enabled. New synthetic lifecycle tests confirm approval/event persistence and that a mutated unverified task cannot resume or consume approval. Targeted run: 50 passed, 1 artifact-collision xfailed; compatibility run: 4 passed, 2 failed because approvals and artifact listing remain blocked. See PHASE153E_VERIFIED_TASK_LIFECYCLE_REPORT.md.
