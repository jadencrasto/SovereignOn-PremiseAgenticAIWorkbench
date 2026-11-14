# Conversation recovery: Phase 1.5.1

Status: **PARTIAL**, 2026-11-07. Branch `phase1-5-recovery-security`, baseline `ec4839c4b05d27132985908908886df916b38dc5`. Existing uncommitted Phase 1.5 changes were retained. No real user conversation store was opened or modified by tests.

## Data flow before this change

The engine records user and completed assistant text in `ConversationMemory`, a JSON store with atomic replacement and cooperative locks. Streaming deltas, tool and source events enter React state; task and approval state are persisted separately in the existing task store. The history endpoint returns owned messages and the sidebar hydrates them. Earlier browser-local history at `sovereign_chat_sessions_v2` is unattributed and remains quarantined. The current persisted conversation data cannot safely reconstruct authorized citations, artifacts, image references, in-progress streams or actionable approvals.

## Implemented subset

- Extend the existing store, not a second conversation system. Newly created conversation objects use `schema_version: 2`; new messages carry UUIDs and UTC creation timestamps. Existing unversioned records remain accepted; missing IDs are deterministically derived from session ID and message position, and unknown timestamps remain absent. Reads alone do not write or claim ownership.
- Existing unknown conversation and message fields are retained when the JSON file is next written, including object-shaped unowned records. Existing owners and message ordering are retained. This is a compatibility strategy, not validation of arbitrary future schemas; malformed records and unsupported version semantics need further work.
- History reads expose stable message IDs, timestamps and user-defined title, omit system messages, and remain scoped by server identity. Rename is an owner-checked, length-bounded server operation. The frontend hydrates these fields. Streaming, model inputs and approval authorization do not consume the new fields.
- Local-only, explicit legacy export: in unauthenticated local mode, and only for the server-recognized local principal without presented credentials, the history drawer offers an explicit warning and download of the raw browser record. The server policy is checked again after confirmation. It neither imports nor deletes the record. The export is unverified, may contain someone else's data and must be saved privately. In authenticated mode the export policy denies this path; no historical ownership is asserted. Browser-level verification remains outstanding.

## Migration, backup and rollback

No real data migration was performed during this work. Existing data is parsed in memory; legacy deterministic IDs become persisted when an ordinary write atomically rewrites the store, including writes to other sessions. Existing locks and temporary-file replacement apply; an injected replace failure preserved the original synthetic file in a test. Automatic backups are not implemented. Before deploying this change, stop all writers, make a separate access-controlled backup of the conversation file and associated task database, and verify the copy. For rollback, stop all writers, preserve a copy of the newer data, then restore the verified pre-upgrade backup and compatible code; work added since the backup would otherwise be lost. Restoring to an older binary may discard new or unknown fields on later writes, so do not assume backwards write compatibility. Windows cooperative locks do not protect non-cooperating processes or sync tools.

## Still missing or unverified

No canonical persistence for tool events, source references, artifacts, attachments, interrupted-stream status, workflow links, or current approval state was added. This work does not restore, authorize, replay or automatically execute an approval or tool. Displayed pending approvals after reload require future server-side revalidation. Size bounds, retention, duplicate-reconnect handling, complete schema validation and an idempotent import are not implemented. Legacy export is unavailable to authenticated accounts; its usability and cross-tab security were not tested in a real browser. Conversation timestamps and titles for older records may remain unavailable. No real-model, browser E2E, artifact or classified-source tests were performed.

Acceptance criteria 1, 3 and 8 are **PARTIAL** (text/IDs and old records covered in synthetic tests); 2 is **PARTIAL** (backend owner tests pass, real-browser account isolation not verified); 4–6 are **BLOCKED/NOT IMPLEMENTED** for rich data and approval restoration; 7 is **PARTIAL** (explicit local-only export implemented, real browser unverified); 9 is **PARTIAL** (existing targeted cooperative-writer tests pass; non-cooperating processes not covered); 10 is **UNVERIFIED** for live streaming/task workflows; 11 is **PARTIAL** (targeted tests pass); 12 is met by this report. This is not release ready and Phase 2 should not begin.

## Verification

With `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, isolated archive of HEAD and the current backend/test files overlaid, the following targeted command passed **20 tests, 0 failed, 0 skipped, 12.30s**:

```text
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase151_conversation_records.py tests/backend/test_phase15_app_integration.py tests/backend/test_phase15_memory_continuation.py tests/backend/test_memory.py -q -p no:cacheprovider --tb=short
```

It covers legacy read/restart, owner isolation, atomic replacement failure, existing cooperative writes and actual app routes with synthetic data and fake model/vector services. An earlier run of the same set reported 19 passed and 1 failed because the new test expected a policy JSON response for an invalid token; the authentication layer correctly returned 401. The expectation was corrected without weakening authentication. These are not live model or OS isolation tests.

Frontend: `node --experimental-strip-types --test tests/historyRequests.test.mjs` **4 passed, 0 failed, 0 skipped** (pre-existing helper tests; not React/browser tests); `npm run lint` exit 0 with warnings; `tsc.cmd --noEmit --incremental false -p tsconfig.app.json` and corresponding `tsconfig.node.json` exit 0; `vite.cmd build --outDir <new temporary directory> --logLevel warn` exit 0 with a large-chunk warning. Full Python suite remains BLOCKED after the automatic review rejected a prior unidentified telemetry request; it was not retried. No OS-level egress isolation was demonstrated.

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
