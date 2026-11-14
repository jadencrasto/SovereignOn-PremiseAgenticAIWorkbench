# Phase 1.5.3E verified task lifecycle report

Date: 2026-11-07. Branch `phase1-5-recovery-security`, HEAD `beeca44`. The working tree contained uncommitted Phase 1.5.3B–D work at the start; it was preserved. **PARTIAL / NO-GO for Phase 1.5.4 and Phase 2.** No verified execution path was enabled, no production data was changed, and nothing was committed, pushed or deployed.

## Findings and decision

The existing `task_security_provenance` schema explicitly accepts only `unverified`, `unknown` and NULL dependency/classification values. Task creation records an asserted conversation ID but the envelope does not independently verify its owner binding. The normal agent path admits planner-generated steps, retrieval, tools and prior data without a complete authoritative dependency record. The existing `task_content_revalidation_available()` is false, so task details are redacted and approval/resume fails before approval consumption. These constraints do not establish the requested verified source-independent execution. Empty retrieval is not proof of independence.

A valid approval must remain bound to task, step, tool and argument hash; the registry verifies and consumes approval before executing. However, that binding alone does not prove source independence, current clearance, immutable plan/content or at-most-once recovery after a crash. Re-enabling the general agent or promoting a quarantine row would therefore violate the requested security conditions. The schema CHECK constraints and absence of a trusted transition path make enabling `verified` require a reviewed migration and new atomic lifecycle, not a configuration toggle. No positive verified task-detail or approval/resume case was claimed. Legacy and new unverified tasks remain restricted; artifact collision xfail remains unchanged.

## Work and targeted evidence

Added `tests/backend/test_phase153e_lifecycle.py`. One synthetic test confirms that updating task state after creating an approval and event preserves those records and leaves provenance unverified. A second creates an unverified task, changes its plan after an approval is issued, and verifies that direct resume returns an error, the approval remains pending and the tool is not called. These tests do **not** demonstrate invalidation of an already verified task: there is no verified state. The existing downgraded-owner and deterministic-conversation guards were retained, and the artifact collision remains an expected failure. No application source was changed in this pass.

## Proposed transition design — not implemented

1. Provide a server-controlled entry point with a narrowly specified deterministic computation and strict input schema. Verify current authenticated principal and actual conversation ownership at creation. Do not use the generic LLM planner, prior assistant history, retrieval, file/image data or arbitrary tools to create or execute this task. A source-independent claim must arise from this enforced path, not an empty list supplied by a caller.
2. In a reviewed additive SQLite migration, represent a distinct `verified_independent` state with server-controlled versioned policy identifier, owner and conversation IDs, creation clearance, and canonical hashes covering immutable task request, exact executable plan, operation parameters and policy version. Existing rows remain unknown; prohibit in-place promotion based only on user-provided data. Bind plan changes or unexpected dependencies to automatic invalidation or rejection.
3. On every task read and immediately before approval and execution, re-read the task and provenance, compare owner, current authenticated clearance, conversation binding, hashes, plan and policy allowlist. Reject changes, unknowns and expired or previously consumed approvals before side effects. Revalidate under the same transactional boundary used to claim execution; preserve exact task/step/tool/arguments binding. The policy must not allow arbitrary tools just because a task is verified.
4. Use a durable single-use state transition and idempotency key for the one fixed operation, with a restart test covering the boundary between claim, computation and persistence. A database transaction alone cannot guarantee exactly-once arbitrary external side effects after a crash; do not extend this guarantee beyond a deterministic, idempotent operation without an atomic participant.
5. Only after actual positive and negative route tests pass should verified task-detail and approval/resume be selectively enabled. Keep source-derived, tool-derived, multimodal and legacy tasks unverified until their transitive provenance is captured and revalidated.

## Verification

Python targeted run in a disposable Git archive of HEAD with the existing uncommitted backend and tests overlaid, synthetic temporary SQLite stores, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`:

```text
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153e_lifecycle.py tests/backend/test_phase153d_provenance.py tests/backend/test_phase153c_task_security.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase15_registry_continuation.py tests/backend/test_approval.py tests/backend/test_tasks.py tests/backend/test_task_recovery.py -q -p no:cacheprovider --tb=short
# 50 passed, 0 failed, 0 skipped, 1 xfailed; 3.88s
```

The xfail is the unchanged artifact-owner collision guard. In a separate isolated compatibility run:

```text
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase15_app_integration.py tests/backend/test_phase153_security.py -q -p no:cacheprovider --tb=line
# 4 passed, 2 failed, 0 skipped; 11.90s; pytest exit 1
```

The unmodified compatibility tests expect task approval to execute and owners to list artifacts, both still blocked by Phase 1.5.3C. The actual-app tests substitute model and Chroma services; no real model or browser was used. Python-level socket guards are not proof of OS network isolation. Full-suite execution remains BLOCKED after the previous automatic review rejection; it was not retried.

## Acceptance status

| Requested check | Status |
| --- | --- |
| Verified task creation, detail, successful approval, safe resume and mutation invalidation | NOT IMPLEMENTED / NOT RUN; no trusted verified state or fixed execution path exists. |
| Cross-owner, forged/unknown provenance and legacy restriction | Existing targeted tests pass for quarantine behavior; they do not establish a verified lifecycle. |
| L3-to-L1 disclosure and deterministic continuity | Existing guards pass in the targeted suite. |
| Approval on an unverified mutated plan | Denied in the new synthetic test; approval remains pending and tool is not called. |
| Artifact collision guard | XFAILED and remains open. |

## Files changed in this pass

`tests/backend/test_phase153e_lifecycle.py`, this report and appended audit addenda. Prior uncommitted implementation and reports were preserved. Secure source-independent task creation, verification-state transitions, restored task detail, approval/resume and any artifact work remain outstanding.
