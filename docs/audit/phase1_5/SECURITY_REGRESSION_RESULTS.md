# Security regression results

## Security implementation continuation, 2026-11-07

An isolated archive copy with modified modules/tests overlaid, pytest plugin autoload disabled and `pytest_asyncio.plugin` explicitly loaded: **57 passed, 0 failed, 0 skipped** in 9.04 seconds. A separate targeted RBAC/tool compatibility run yielded **99 passed** in 19.17 seconds, before the final simulated-clearance-header and task-owner changes. Included tests for a two-user in-process ASGI chat flow, legacy data, restart, same-process and two-process concurrent JSON writers, retrieval clearance ceilings with synthetic classification metadata, simultaneous request contexts, one-use SQLite approvals, mismatched arguments, disabled dispatch, multipart size boundaries and interrupted input, and configured launcher binding. HTTP/model and retrieval dependencies in these tests are fake or synthetic; no real model or production document was involved.

Full suite, external browser network validation, actual production host binding, application end-to-end workflows, and telemetry isolation remain BLOCKED / NOT RUN.

## Telemetry investigation, 2026-11-07

3 isolated fake-client configuration tests passed; they do not measure network traffic or prove real Chroma startup safe. A model-download-capable default embedding function was disabled for both collection creation paths. Exact telemetry source remains unknown. No full-suite retry was made; no OS-level egress isolation was demonstrated. See TELEMETRY_AND_EGRESS_AUDIT.md.

## Continuation, 2026-11-07

Isolated current-copy runs: 48 passed (memory, approval, plan validation, previous phase1.5 targeted tests and new clearance, persistence and registry tests); 103 passed (legacy tool/RBAC tests and initial continuation tests, after correcting tests for the deliberately stricter registry policy); 33 passed (task, recovery and ownership tests). The 103 and 48 test sets overlap; do not add them into a unique total. Three role/clearance combinations include admin with lower clearance. Approval dispatch tests include a temporary SQLite approval manager, mismatched arguments, viewer denial and valid approval. These checks do not establish conversation ownership, replay prevention or end-to-end authorization.

An initial older RBAC/tool run produced 8 failures and 91 passes: its tests assumed role-free dispatch and writes without approval. Tests were updated to provide roles for allowed read operations and expect denial without approval, then the 103-test run passed. No application policy was weakened to satisfy them.

Isolated synchronous tests in `tests/backend/test_phase15_targeted.py`: 8 passed, 3 deselected, 2 warnings. These cover config binding/auth, production auth override, external font references and evaluation reporting. Async tests for upload size and multimodal dispatch were deselected because the environment cannot complete a minimal asyncio event loop; no result is claimed for those cases. Tests use mocks for some boundaries and do not establish end-to-end security.

Outstanding: session ownership, cross-user guessed IDs, legacy-data migration, concurrent writes, exact-limit and interrupted upload, multipart ingress limits, all-path clearance, registry-level tool disable, approval-to-action binding and live browser network capture. All remain unverified or unimplemented.

## Final integration verification, 2026-11-07

Current targeted run: 81 passed, 0 failed, 0 skipped; real-route synthetic integration: 2 passed, 0 failed, 0 skipped. Actual authentication, SQLite and conversation files were used with fake Chroma and inference; see FINAL_INTEGRATION_REPORT.md for exact commands and boundaries. Browser localStorage is not account-scoped, a remaining confidentiality issue. Full-suite and OS isolation are still BLOCKED.

## Browser history synchronization, 2026-11-07

`node --experimental-strip-types --test tests/historyRequests.test.mjs` (frontend): 4 passed, 0 failed, 0 skipped. Unit tests exercise server-authorized account lists, stale list/read invalidation, local-principal behavior, delete success and error propagation using fakes; they do not mount React or verify real browser storage, cookies, cross-tab messaging or logout. Isolated archive with current `backend/api/chat.py` and `tests/backend/test_phase15_app_integration.py` overlaid: `python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase15_app_integration.py -q -p no:cacheprovider --tb=short`: 2 passed, 0 failed, 0 skipped, 12.36s. Actual FastAPI routes/auth/temporary persistence, simulated models and Chroma; tests include authorized read, guessed/missing ID denial, denied deletion and successful deletion. Full suite and OS-level egress proof remain BLOCKED.

## Phase 1.5.1 conversation recovery

Isolated targeted Python run: 20 passed, 0 failed, 0 skipped; frontend helper tests: 4 passed, 0 failed, 0 skipped. Initial targeted run: 19 passed, 1 failed due to an incorrect expectation for an invalid-token response (actual 401); the test was corrected. Models and Chroma are fake in integration tests. Browser and OS-level network isolation remain unverified; details in CONVERSATION_RECOVERY_REPORT.md.

## Final rich-history continuation, 2026-11-07

Baseline `03370df` on `phase1-5-recovery-security`. Status remains **PARTIAL**. In `AgentEngine.chat_stream_with_tools`, successful and failed tool calls are now recorded atomically with a nonempty final assistant message, in bounded `tool_events_v1` metadata: up to 32 entries with generated invocation ID, restricted tool name and boolean result. Arguments, tool output, summaries, tokens, source text and filesystem paths are not persisted in that metadata. An actual engine test uses a fake model and fake tool registry. Authenticated history emits only validated allowlisted fields; the sidebar maps them to existing tool activity UI, which now labels failures correctly. No hydration invokes a tool or makes an approval actionable. Existing owners, unknown fields, local legacy export and the same JSON file/locking scheme remain in use.

This does not record interrupted calls (a final assistant message may never be saved), tools when the final response is empty, or events emitted by `chat_stream_with_tools_multimodal`, the planning/agent execution and resume paths, and arithmetic shortcut. It does not implement persisted source citations, artifact/attachment references, task status, approval history, interruption/failure state, retry/reconnect deduplication, full browser reload or authorization revalidation of source/artifact access. Invocation IDs are generated for this stored record and are not existing execution registry identifiers. Do not infer these missing features from this partial implementation. No live inference, artifact, browser, real Chroma or OS egress test was run. No persisted migration executed against user data; existing backup and rollback advice continues to apply.

Validation from a temporary Git-archive copy with modified backend/test files overlaid, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`: `python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase151_tool_history.py tests/backend/test_phase151_conversation_records.py tests/backend/test_phase15_app_integration.py tests/backend/test_phase15_memory_continuation.py tests/backend/test_memory.py tests/backend/test_phase15_registry_continuation.py -q -p no:cacheprovider --tb=short` — **26 passed, 0 failed, 0 skipped in 14.89s**. Covers fake model/tool engine loop, allowlist, restart/owner isolation, actual route serialization with synthetic persistence, and existing approval controls. `node --experimental-strip-types --test tests/historyRequests.test.mjs` — **4 passed, 0 failed, 0 skipped**; these are request-helper tests, not browser tests. Frontend `npm run lint` exit 0 with warnings, app and node `tsc.cmd --noEmit --incremental false -p ...` exit 0, and `vite.cmd build --outDir <new temporary directory> --logLevel warn` exit 0 with large-chunk warning. Full Python suite and OS egress proof remain BLOCKED after the previous automatic review rejection.

## Phase 1.5.2 addendum — 2026-11-07

Partial: multimodal, planned and resumed registry tool statuses persist only with a nonempty final response; conversation reads expose owner-scoped, read-only task/approval statuses. Synthetic targeted run: 11 passed; frontend helper tests: 4 passed; lint, both typechecks and temporary build passed with warnings. Images, clearance-revalidated citations, artifact links and interrupted-stream journaling remain unimplemented. Actual browser, live models, full suite and OS egress isolation remain unverified or blocked. Static review found the artifact API grants MANAGE_TASKS roles access before checking ownership. See PHASE152_IMPLEMENTATION_REPORT.md for evidence and limitations.

## Phase 1.5.3 security addendum — 2026-11-07

Partial: owner-bound artifact policy, conservative provenance-gated assistant history and owner-bound image descriptors were added. Unprovenanced assistant history is withheld and prior assistant turns are excluded from model context; attachment retrieval is not exposed. Synthetic targeted Python runs: 19 passed and 31 passed (overlapping); frontend helpers 4 passed; lint/typechecks/build passed with warnings. Task/step clearance revalidation, complete provenance, artifact lifecycle and authorized attachment retrieval remain open; full suite and OS isolation remain blocked. See PHASE153_SECURITY_REPORT.md for exact commands, initial test failure, compatibility impact and limitations.

## Phase 1.5.3 stabilization addendum — 2026-11-07

NO-GO for Phase 1.5.4. Empty-source plain-chat provenance now fails closed. Synthetic tests confirmed that task detail can return a prior higher-clearance result to its downgraded owner, artifact registration can reassign a filename owner, and safe prior assistant messages are excluded from model context. Targeted Python result: 13 passed, 3 xfailed; a `--runxfail` confirmation produced the intended 3 failures. A cross-subsystem provenance and lifecycle design is needed. See PHASE153_STABILIZATION_REPORT.md.

## Phase 1.5.3B addendum — 2026-11-07

NO-GO: narrow verified deterministic calculation continuity was restored in plain-chat context; task clearance disclosure and filename-based artifact ownership collision remain open strict expected-failure guards. Targeted tests: 23 passed, 2 xfailed; the guard confirmation run produced 1 passed and 2 failed. Shared transitive provenance, authorization and atomic artifact lifecycle remain incomplete. See PHASE153B_SECURITY_REMEDIATION_REPORT.md.

## Phase 1.5.3C addendum — 2026-11-07

The downgraded-owner task-result guard passes using universal fail-closed redaction because trustworthy task/source lineage is absent. Approval/resume, approval-history details, all artifact viewing/download and artifact verification are currently blocked for everyone; task status metadata remains available subject to ownership/admin policy. Compatibility run: 4 passed, 2 failed due to these deliberate restrictions; targeted security run: 39 passed, 1 artifact-collision xfailed. This is partial containment, not full provenance or release readiness. See PHASE153C_TASK_SECURITY_REPORT.md.

## Phase 1.5.3D addendum — 2026-11-07

PARTIAL / NO-GO: new tasks obtain an atomic server-generated unverified provenance envelope; bound owner mismatch is rejected and protected task owner/session cannot be reassigned. The envelope grants no access and records unknown dependencies/classification. Task detail/approval/resume and artifact access remain fail-closed; artifact collision remains open. Current targeted run: 48 passed, 1 xfailed. See PHASE153D_PROVENANCE_FOUNDATION_REPORT.md for exact tests, two historical compatibility failures and schema limitations.

## Phase 1.5.3E addendum — 2026-11-07

PARTIAL / NO-GO: no trustworthy verified task execution or provenance transition was enabled. New synthetic lifecycle tests confirm approval/event persistence and that a mutated unverified task cannot resume or consume approval. Targeted run: 50 passed, 1 artifact-collision xfailed; compatibility run: 4 passed, 2 failed because approvals and artifact listing remain blocked. See PHASE153E_VERIFIED_TASK_LIFECYCLE_REPORT.md.
