# Phase 1.5.3C task security report

Date: 2026-11-07. Branch `phase1-5-recovery-security`, HEAD `beeca44`. Prior Phase 1.5.3B changes were uncommitted and have been preserved. No task data was migrated or deleted, and no commit, push or deployment occurred.

## Verdict

**Task-disclosure regression guard: PASS** in a synthetic route test. **Overall task functionality and release readiness: PARTIAL / NO-GO.** This is a deliberately broad fail-closed containment, not complete provenance-aware authorization. The artifact collision remains unresolved. No full-suite retry or OS-isolation verification was performed.

## Root cause and policy

`GET /api/tasks/{task_id}` previously authorized identity/role but returned persisted `result`, `error`, user request, plan, step arguments/results/errors with no current classification or source-lineage revalidation. Stored creation-time clearance alone cannot establish permission after source reclassification, deletion or mixed-source derivation. Other task endpoints and resume could expose or reuse the same data.

Current task records have no verifiable transitive source provenance. The shared `backend/security/task_access.py` therefore reports content revalidation unavailable. It is not controlled by client inputs or environment. Until a real policy exists, all task payloads are treated as unverified for every clearance, including admins. This is stricter than a dynamic L3/L1 recheck and cannot preserve high-clearance payload access. Non-admin task identity matching now requires the server user ID, rather than username aliases; admin cross-owner status metadata remains an explicit existing administrative exception. Operators cannot cancel unowned legacy tasks; admins retain cancellation access.

## Changed boundaries and consequences

- `/api/tasks` returns owner-scoped (or admin cross-owner) task status/count/time metadata; user requests and client-controlled session IDs are replaced with a generic unavailable indicator.
- `/api/tasks/{id}` applies ownership first and returns status/time metadata with user request/session ID withheld and plan, result and error absent. Existing stored values are not rewritten.
- `/api/tasks/monitor` is admin-only and redacts requests, errors and session IDs from the returned aggregate. The internal manager still has access to underlying data.
- `/api/tasks/{id}/approvals` and `/approve` return 403 after authorization rather than expose approval details or use an unverified plan. This blocks both approve and reject through that endpoint; cancellation remains separate. `AgentEngine.resume_agent_task` also rejects before reading task or approval state, so the chat natural-language approval path cannot bypass the route. It emits an error rather than a misleading done event. **All task continuation, including legitimate administrator approvals, is temporarily unavailable.** Existing approval binding, consumption and replay code was not weakened or removed; it cannot be exercised through the blocked route.
- Existing artifact records also lack proven task/source lineage; artifact list/preview/download therefore withhold all artifacts, including for owners and admins, until revalidation is available. `artifact_verifier` is denied at the registry boundary because it can expose file content through previews. **All artifact viewing/download and verification are temporarily unavailable.** Artifact identity/storage was not redesigned; the filename collision acceptance guard remains expected-failing.
- Removed several obvious task-context/query-bearing log statements. Other tool/audit/error paths and in-flight streams have not received a complete confidentiality audit. This is not proof that every tool or logging path is safe; current user clearance is not rechecked continuously during an already open stream. Other direct file/tool routes may still need provenance enforcement.

## Tests

Run in separate temporary Git archives with changed backend/tests overlaid, synthetic fixtures, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`. The actual-app tests substitute model/vector services; Python socket guards are not OS isolation.

```text
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153c_task_security.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase15_registry_continuation.py tests/backend/test_approval.py tests/backend/test_tasks.py tests/backend/test_task_recovery.py -q -p no:cacheprovider --tb=short
# 39 passed, 0 failed, 0 skipped, 1 xfailed; 3.14s
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153_stabilization.py::test_downgraded_owner_cannot_read_classified_task_result -q -p no:cacheprovider --tb=short
# 1 passed, 0 failed, 0 skipped; 1.08s
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase15_app_integration.py tests/backend/test_phase153_security.py -q -p no:cacheprovider --tb=line
# 4 passed, 2 failed, 0 skipped; 12.34s; pytest exit 1
```

The compatibility failures are retained without changing their assertions: `test_actual_auth_sessions_isolate_conversations_and_tasks` expects the prior successful approval but receives 403; `test_artifact_routes_enforce_owner_task_and_admin` expects an authorized owner's artifact listing but receives an empty list. They demonstrate the intended but severe functionality loss. Sets overlap; the single downgraded-user guard is part of the larger run. The remaining xfail is the unchanged artifact owner-reassignment guard; it is not a pass.

Focused new tests exercise listing/detail redaction, secret-bearing plan/step/error/approval fields, current L1 versus recorded L3, admin metadata-only policy, cross-owner denial, monitor restriction, approval preservation, direct resume denial, chat approval stream error, artifact owner/admin denial and verifier dispatch denial. Existing isolated registry/approval tests still cover approval single-use and replay internally. These tests use synthetic fixtures and do not prove live model, browser, full tool coverage or complete reclassification protection.

## Remaining blockers

Implement a trustworthy versioned, source-aware and transitive provenance system for task inputs, tool outputs, plans, results and artifacts; revalidate current identity and clearance plus each dependency on every read/resume; restore permitted high-clearance task and artifact workflows only after that policy and side-effect binding are tested. Resolve filename-based artifact ownership collision and atomic creation separately, as requested. Audit all alternate tool access, stored user input, logs, approval resume and in-flight clearance revocation. Verify application workflows and browser behavior in an approved isolated environment. Full suite remains BLOCKED after prior automatic review rejection of an unidentified outbound telemetry request; no bypass or retry occurred.

## Phase 1.5.3D addendum — 2026-11-07

PARTIAL / NO-GO: new tasks obtain an atomic server-generated unverified provenance envelope; bound owner mismatch is rejected and protected task owner/session cannot be reassigned. The envelope grants no access and records unknown dependencies/classification. Task detail/approval/resume and artifact access remain fail-closed; artifact collision remains open. Current targeted run: 48 passed, 1 xfailed. See PHASE153D_PROVENANCE_FOUNDATION_REPORT.md for exact tests, two historical compatibility failures and schema limitations.

## Phase 1.5.3E addendum — 2026-11-07

PARTIAL / NO-GO: no trustworthy verified task execution or provenance transition was enabled. New synthetic lifecycle tests confirm approval/event persistence and that a mutated unverified task cannot resume or consume approval. Targeted run: 50 passed, 1 artifact-collision xfailed; compatibility run: 4 passed, 2 failed because approvals and artifact listing remain blocked. See PHASE153E_VERIFIED_TASK_LIFECYCLE_REPORT.md.
