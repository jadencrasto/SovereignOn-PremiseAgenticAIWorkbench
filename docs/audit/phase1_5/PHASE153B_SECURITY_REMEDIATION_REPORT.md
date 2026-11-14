# Phase 1.5.3B security remediation report

Date: 2026-11-07. Branch `phase1-5-recovery-security`, baseline `beeca44`; working tree was clean initially. Decision: **NO-GO for Phase 1.5.4 and Phase 2; security remediation is incomplete.** The earlier automatic-review restriction remains in force; no full suite was retried and no OS-level egress isolation was established.

## Implemented: narrow safe continuity

`AgentEngine._build_messages` now admits prior assistant messages only when their server-stored provenance is a valid `deterministic_calculation` with no source dependencies, as verified by the existing `may_read_assistant` policy. Malformed deterministic records, source-derived and unknown legacy answers remain excluded. The corresponding pre-existing continuity acceptance guard now passes, with additional negative checks. This applies to the plain-chat model-context builder; planner task reasoning still excludes previous assistant messages. Future outputs that use the independent calculation do not inherit a classified source from that calculation, but the general transitive source/tool/multimodal lineage problem remains unresolved. Empty retrieval results remain unverified, not independent.

## Open security failures and root causes

1. **Task disclosure: CONFIRMED with synthetic actual route.** `GET /api/tasks/{task_id}` returns stored results to the owner without source provenance/current clearance revalidation. A downgraded L1 owner can receive a synthetic L3 task result. Task listing, step details, approval reasons, resume and chat approval interception require a common policy; they were inspected but are not fully fixed or comprehensively tested. Their existing data has no trustworthy source lineage. A clearance-only comparison would not address reclassification, deletion, mixed dependencies or legacy unknowns. The strict expected-failure guard remains.
2. **Artifact collision: CONFIRMED at synthetic store layer.** `TaskStore.save_artifact` updates owner on filename conflict. `ToolRegistry.execute` checks ownership before tool execution, but creation happens before registration and the check/write sequence is not atomic; registry errors are logged rather than ensuring rollback. A concurrent or colliding lifecycle needs stable identifiers, exclusive file creation and atomic owner/task/session registration before the guard can be removed. An actual external exploit or complete concurrent end-to-end reproduction was not established. The strict expected-failure guard remains.
3. **Shared provenance: INCOMPLETE.** Existing `access_provenance_v1` handles narrow plain retrieved chunks and deterministic calculations, but does not provide versioned transitive lineage for task records, tool outputs, multimodal inputs, artifacts, prior source-derived assistants or approval/resume execution. Empty source lists remain withheld. No new generic authorization system or guessed classification was added.
4. **Attachment and artifact lifecycle: INCOMPLETE.** Existing owner-bound image descriptors are not exposed as a retrieval API. Artifact read policy has an explicit administrator exception; creation race, legacy migration, classification and authorized download of derived content need a complete lifecycle design. No real browser or real model verification ran.

## Design and compatibility direction

Use a bounded, server-owned provenance contract that distinguishes independently verified computation, exact versioned source/chunk dependencies, transitive parent output dependencies and unknown legacy state. Revalidate every dependency under current identity and clearance at history, task list/detail/approval/resume, model context, artifact preview/download and future attachment endpoints. Fail closed when records are unknown or dependencies unavailable, and carry lineage atomically through tool and task state changes. Existing data must remain intact; do not backfill source independence from empty retrieval. Decide and audit any administrator cross-owner exception separately from clearance.

Use stable artifact IDs unrelated to display names, atomic exclusive file reservation and metadata registration before any side effect, immutable owner/task/conversation relationships and explicit legacy quarantine/migration. The current filename precheck and post-write registration are insufficient, so this pass did not introduce a partial registration-only patch or mark the artifact guard fixed. Resume must revalidate both approval binding and full source/task lineage immediately before execution. Design and tests must cover race, failure rollback, deletion and restart.

## Tests executed

From a fresh temporary Git archive of HEAD with `backend/agent/engine.py` and `tests/backend/test_phase153_stabilization.py` overlaid, isolated synthetic fixtures, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`:

```text
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153_stabilization.py tests/backend/test_phase153_security.py tests/backend/test_phase152_recovery.py tests/backend/test_phase151_tool_history.py tests/backend/test_phase15_app_integration.py tests/backend/test_phase15_registry_continuation.py tests/backend/test_phase15_clearance_continuation.py tests/backend/test_phase15_memory_continuation.py -q -p no:cacheprovider --tb=short
# 23 passed, 0 failed, 0 skipped, 2 xfailed; 14.53s
python -m pytest -p pytest_asyncio.plugin tests/backend/test_approval.py tests/backend/test_tasks.py tests/backend/test_task_recovery.py -q -p no:cacheprovider --tb=short
# 28 passed, 0 failed, 0 skipped; 1.80s
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153_stabilization.py -q -p no:cacheprovider --runxfail --tb=short
# 1 passed, 2 failed, 0 skipped; 1.34s; pytest exit 1
```

The `--runxfail` failures are the intentionally open task-disclosure and artifact-owner guards. They are not passing or fixed tests. Test sets overlap. App integration uses synthetic identities and substituted model/vector services; Python socket guards do not demonstrate OS-level isolation. No live model, real Chroma, browser or full-suite run occurred.

Frontend `node --experimental-strip-types --test tests/historyRequests.test.mjs`: **4 passed, 0 failed, 0 skipped** (helper tests, not browser). `npm run lint`: exit 0 with warnings; app and node `tsc.cmd --noEmit --incremental false -p ...`: exit 0; Vite build to fresh temporary directory: exit 0 with chunk-size warning.

## Changed files

`backend/agent/engine.py` admits only verified independent calculator turns to plain-chat model context. `tests/backend/test_phase153_stabilization.py` converts the genuinely passing continuity guard and adds unsafe/unknown negative cases; task and artifact guards remain strict xfails. This report and appended audit addenda preserve historical evidence. No runtime data was intentionally changed.
