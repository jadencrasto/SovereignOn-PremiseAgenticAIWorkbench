# Fix register

## Security implementation continuation, 2026-11-07

- Conversation ownership: request-bound server identity is persisted for newly created sessions. Authenticated users cannot list or access another owner's session. Legacy list-format records remain unassigned in authenticated mode; the local synthetic principal can claim an individual legacy conversation in local mode. Atomic JSON updates are serialized across cooperating processes via a one-byte Windows lock (`msvcrt.locking`) or POSIX `flock`, plus the existing in-process lock. The lock only coordinates versions/processes that honor the same sidecar lock; network filesystems and mixed old/new versions have not been verified. A failed write may leave in-memory state changed until the next reload; the on-disk file remains intact.
- Retrieval: server-derived clearance is carried in request context and caps agent and document-search tool retrieval; plain chat retains its explicit effective clearance. Direct API and task-resume paths require broader integration validation, particularly where administrator approval of another user's task intersects conversation ownership. Local tests use mock model/retrieval providers.
- Tools: dispatcher checks an explicit flag and request context, role, exact approval binding, and consumes approved actions once using an atomic SQLite update before execution. Failed or interrupted actions need a new approval; cancellation/consume races and full workflow recovery require additional testing.
- Multipart uploads: ASGI middleware bounds `/api/documents` and `/api/chat/multimodal` multipart bodies before the framework parser. It permits configured file size plus 1 MiB multipart overhead and spools beyond 1 MiB to a temporary file. Direct arbitrary ASGI deployments or reverse proxies still require independent ingress, concurrency and timeout limits. The new `python -m backend` launcher uses validated configured host and port; direct `uvicorn` invocations can still override binding and must be controlled by deployment configuration.

These are targeted implementations with synthetic regression coverage, not verified production or air-gap guarantees. Earlier entries describe prior checkpoints.

## Telemetry investigation, 2026-11-07

Egress finding OPEN: exact rejected destination and module are unavailable. `backend/rag/store.py` now explicitly requests disabled Chroma product and OTLP telemetry before its import and disables default collection embedding functions; 3 mocked configuration tests passed. Installed Chroma native behavior and real-client startup remain unverified. Full suite is blocked pending demonstrated OS isolation; see TELEMETRY_AND_EGRESS_AUDIT.md.

## Continuation, 2026-11-07

Checkpoint is branch `phase1-5-recovery-security`, commit `5dc673f55a02e1c689af62c1bc4c7fa6625b9383`; subsequent changes are uncommitted. Earlier table records the original checkpoint.

- ISS-011 PARTIAL: JSON persistence uses same-directory temporary files, fsync and atomic replacement; an RLock serializes operations in one process and invalid JSON fails closed. Legacy file format is preserved. Restart, thread concurrency and injected replace failure tested. No cross-process serialization, ownership, migration, or rollback of already-mutated in-memory state after a failed save is implemented. Existing legacy conversations are not assigned to anyone.
- ISS-007 PARTIAL: authenticated server-derived effective clearance is now passed to plain and streaming text chat; regression tests check role and clearance combinations. Planned, multimodal and tool-based paths still use roles in places and remain open.
- ISS-005 PARTIAL: registry now rejects missing role and requires a verified task/step/argument-bound approval for tools marked as requiring approval; approved task resume supplies approval ID. Synthetic tests include a real SQLite approval manager. Registry-level per-request tools-disabled capability, replay protection, task/session ownership and complete workflow integration still need validation. Existing direct tool consumers must pass a verified role.
- Auth-disabled local mode no longer honors role simulation headers unless the existing explicit development simulation setting is enabled. This is not a multi-user security boundary and does not replace authentication or session ownership.

| ID | Change | Verification | Remaining |
|---|---|---|---|
| ISS-002 | `backend/config.py`, `.env.example`: loopback default; validator requires auth for non-loopback, rejects production auth override. | Synchronous validator tests passed. | Actual server command can independently bind a host; production deployment, reverse proxy and ingress configuration remain unverified. |
| ISS-003 | Removed Google font references in HTML/CSS; system fallbacks retained. | Source regression passed; Vite build passed. | Browser request capture and full dependency egress audit not performed. |
| ISS-004 | Report FAIL/INCOMPLETE/BLOCKED, count expected suites, avoid unconditional isolation claims, nonzero runner exit for non-PASS. | PASS/FAIL/INCOMPLETE/BLOCKED and missing suite synchronous tests passed. | Live benchmark acceptance and per-case mandatory/optional policy remain to be defined. |
| ISS-005 | Propagate tools flag into multimodal engine and guard registry dispatch in that path. | Regression tests added but async execution blocked. | Engine dispatch guard is not a registry-level capability enforcement; enabled-path approvals and text path need independent verification. |
| ISS-006 | Document handler reads bounded chunks and rejects > configured size with 413; closes upload. | Async regression added but blocked. | Framework multipart parsing may spool request before handler; request-body/ingress limits, exact boundary, malformed and interruption tests outstanding. |
| ISS-012 | Replaced misleading verified/live claims for static graph display. | Source inspection, lint/build. | Audit all synthetic surfaces and visually verify browser. |
| ISS-001/011 | No change | Not verified. | Owner-aware migration, atomic persistence, concurrency and ownership enforcement. |
| ISS-007 | No change | Not verified. | Propagate authenticated clearance across all paths. |

Do not assume partial fixes close the associated issues until the remaining verification and integration tests pass.

## Final integration verification, 2026-11-07

Fixed a tools-disabled arithmetic shortcut in `backend/api/chat.py:195`; real-route synthetic regression included in `test_phase15_app_integration.py`. Open: browser localStorage history is shared across users of the same browser profile (`frontend/src/components/chat/ChatHistorySidebar.tsx:34`), and sidebar deletion does not invoke the server delete helper (`frontend/src/api/chat.ts:102`). See FINAL_INTEGRATION_REPORT.md.

## Browser history synchronization, 2026-11-07

Implemented server-owned list/read and confirmed server deletion, with account-keyed chat remount, stream abort, stale-request invalidation and cross-tab auth-change notification. Legacy `sovereign_chat_sessions_v2` remains untouched and is never read or assigned. The earlier browser-history and sidebar-delete findings are addressed in code but browser verification is outstanding. Server history currently contains message role/content only: after reload titles are inferred, timestamps, rich tool/source/approval state and display renames are not restored. Browser-only legacy conversations remain quarantined, including in local mode; safe recovery needs a separate explicit attribution design.

## Phase 1.5.1 conversation recovery

Partial implementation: stable message IDs/timestamps, server-owned title, unknown-field preservation, local-only explicit legacy export. Rich events, browser verification and approval revalidation remain open. See CONVERSATION_RECOVERY_REPORT.md.

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
