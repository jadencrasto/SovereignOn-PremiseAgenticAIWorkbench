# Phase 1.5.3 security boundary report

Date: 2026-11-07. Branch `phase1-5-recovery-security`, HEAD `e744b24`. Initial tree already contained uncommitted Phase 1.5.2 work; it was preserved. This phase is **PARTIAL** and is not production-ready. No real data migration, commit, push or deployment occurred.

## Artifact access

Confirmed in the original `backend/api/artifacts.py:_is_artifact_accessible`: `MANAGE_TASKS` caused an immediate allow before artifact ownership was checked. The implementation now requires owner equality for non-admin users, including operators/managers. Admin cross-owner and unowned-legacy access is an explicit exception; if an artifact has a task ID, even an admin is denied when its owner, task owner and persisted conversation owner cannot be reconciled. The local synthetic admin retains the explicit admin policy. List, preview and download share the policy; list no longer returns an internal path and excludes symlinks. Active application sandbox settings are used rather than the module-global path. Unregistered legacy artifacts are hidden from non-admins; old task-linked records with missing/unbound conversation ownership are denied instead of being silently reassigned.

A synthetic FastAPI route test covers owner/manager, other owner, viewer, admin, legacy file, inconsistent task owner and malformed filename, with temporary SQLite and files. This is not a browser or real deployment test. Artifact classification is not modeled by this policy; admin cross-owner access is deliberate and must be reviewed against the deployment's organizational policy. Filename-keyed artifact collisions, filesystem races and the full tool-side artifact creation/registration lifecycle require further audit.

## Conversation clearance

Confirmed: history previously returned saved assistant text to its owner without checking source provenance; hiding citation cards alone could not prevent disclosure after downgrade. The updated history endpoint withholds an assistant response and its tool metadata if provenance is missing, malformed, unavailable, or any referenced source chunk is not visible under the current server-derived clearance. Plain no-tool chat now stores bounded document/chunk identifiers for its retrieved sources; history reads re-resolve every referenced chunk via the document service at the current clearance. No-source plain chat and deterministic arithmetic receive explicit server-created provenance. Legacy, planner, multimodal and tool-generated assistant responses without supported provenance remain stored but are withheld even from high-clearance owners. User-authored messages remain visible to their owner. No records were silently deleted.

To avoid laundering old source-derived assistant answers into new plain responses, both main conversation prompt builders currently exclude all prior assistant messages. This materially reduces multi-turn continuity and is an interim fail-closed behavior. History responses are re-fetched on sidebar selection rather than using the previous in-memory conversation cache, but an already displayed response cannot be remotely erased from a client after a downgrade; there is no real-time revocation channel. The on-disk store is part of the trusted server boundary; metadata is not cryptographically signed against filesystem tampering. User messages, project/system prompts, derived files and task/step records may themselves contain confidential text. In particular, task-detail and resume paths persist/return task results and have not been fully revalidated on clearance changes. This work does **not** establish complete classified-data confinement. Source reclassification, deletion, mixed sources and downgrade were tested with a synthetic fake document service; the downgrade was also exercised through an authenticated route with synthetic identities and a fake resolver. No real Chroma or browser test ran.

## Attachments

Prepared an owner-bound storage model: server-produced image descriptors (UUID, safe filename, MIME and size only) can be stored in the user message under `attachments_v1`, inheriting conversation ownership and atomic persistence. The route passes the processed descriptor to the multimodal engine; raw bytes and filesystem paths are not added to conversation JSON. Existing multipart and image validation remain. No attachment metadata or content retrieval endpoint or new URL is exposed. An isolated memory test verifies descriptor validation, no raw bytes and cross-user denial; this is not a content retrieval or browser test. Orphaned images after interruption, legacy uploads, cleanup and a safe owner/clearance-bound retrieval API remain open.

## Exact tests

Python tests ran inside a fresh Git archive of HEAD with current backend and selected uncommitted tests overlaid, temporary stores, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`. The app test replaces model and vector services and guards Python socket connections; this does not establish OS egress isolation:

```text
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153_security.py tests/backend/test_phase152_recovery.py tests/backend/test_phase151_tool_history.py tests/backend/test_phase15_app_integration.py tests/backend/test_phase15_registry_continuation.py tests/backend/test_phase15_clearance_continuation.py -q -p no:cacheprovider --tb=short
# 19 passed, 0 failed, 0 skipped; 14.30s
python -m pytest -p pytest_asyncio.plugin tests/backend/test_approval.py tests/backend/test_tasks.py tests/backend/test_task_recovery.py tests/backend/test_phase15_memory_continuation.py -q -p no:cacheprovider --tb=short
# 31 passed, 0 failed, 0 skipped; 2.05s
```

The sets overlap. An earlier development run returned 17 passed and 1 failed because a test searched for substring `bytes`, which is part of the legitimate key `size_bytes`; the test was corrected to assert the forbidden raw `bytes` key and secret value are absent. Frontend: `node --experimental-strip-types --test tests/historyRequests.test.mjs` 4 passed, 0 failed, 0 skipped; `npm run lint` exit 0 with warnings; app and node `tsc.cmd --noEmit --incremental false -p ...` exit 0; Vite build to a fresh temp directory exit 0 with chunk-size warning. Frontend helper tests are not React/browser E2E.

## Prerequisites for Phase 1.5.4

1. Complete provenance and transitive lineage for tool, planner, multimodal, prior assistant and task/approval/artifact data, including clearance changes, deletion and reclassification. Verify all APIs and resume paths before considering the confidentiality boundary closed.
2. Design tested attachment retrieval and lifecycle checks; protect artifact ownership on creation, task linking and download and explicitly decide administrative cross-owner policy.
3. Reconcile the deliberate legacy-history withholding and reduced multi-turn continuity with safe migration and user experience; do not automatically mark unknown content unclassified.
4. Perform browser, real-model and Chroma checks in an approved independently isolated environment. Full-suite retry and OS isolation are still BLOCKED under the prior automatic review decision and must not be bypassed.

A final focused rerun after adding the admin/inconsistent-task regression used the same isolated overlay and `python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153_security.py -q -p no:cacheprovider --tb=short`: **4 passed, 0 failed, 0 skipped in 1.22s**. This is a subset of the larger run and must not be added as unique coverage.
