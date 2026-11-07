# Final integration and release candidate verification

Date: 2026-11-07. Branch `phase1-5-recovery-security`; baseline commit `ec4839c4b05d27132985908908886df916b38dc5`. Working tree was clean before this verification. Windows, Python 3.13.7, Node 22.20.0. No push, merge, deployment, model download or full-suite retry.

## Decision

**NOT RELEASE READY; Phase 1.5 PARTIAL.** Browser profile history is not user-isolated, the full Python suite and OS-level egress isolation remain blocked, and live application model/RAG and isolated sandbox workflows remain unverified in this verification. Do not start Phase 2 until the security and acceptance gates are resolved.

## Changes and integration findings

- `backend/api/chat.py:195`: arithmetic shortcut was executed and advertised as a tool when `tools_enabled=false`; it is now skipped for disabled requests. `tests/backend/test_phase15_app_integration.py` tests the route with fake inference and checks no tool event is emitted.
- **OPEN, high priority, statically confirmed:** `frontend/src/components/chat/ChatHistorySidebar.tsx:34` uses an origin-wide browser localStorage history, without per-user scoping. Different accounts using the same browser profile can be exposed to prior locally saved transcripts even when backend access is denied. Browser reproduction and remediation are outstanding.
- **OPEN, deletion gap, statically supported:** `frontend/src/components/chat/ChatHistorySidebar.tsx:116` deletes browser storage; the server deletion helper exists at `frontend/src/api/chat.ts:102`, but the sidebar does not invoke it. Server-side deletion from that UI path remains unverified; users may expect deletion to remove persisted server data.
- Frontend API passes `tools_enabled` in JSON and multipart requests; this static inspection does not establish complete frontend compatibility or browser behavior.

## Tests and commands

Python tests ran in a newly created temporary `git archive HEAD` copy with the modified chat module and (for integration) the new test overlaid. Temporary databases, memory, uploads, logs and Chroma paths were configured by the tests. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`. Exact commands below are executed from the isolated copy:

```text
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase15_app_integration.py -q -p no:cacheprovider --tb=short
# 2 passed, 0 failed, 0 skipped; 10.93 s
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase15_security_controls.py tests/backend/test_phase15_registry_continuation.py tests/backend/test_phase15_memory_continuation.py tests/backend/test_phase15_clearance_continuation.py tests/backend/test_phase15_targeted.py tests/backend/test_memory.py tests/backend/test_approval.py tests/backend/test_tasks.py tests/backend/test_task_recovery.py tests/backend/test_telemetry_configuration.py -q -p no:cacheprovider --tb=short
# 81 passed, 0 failed, 0 skipped; 12.84 s
```

The two new integration tests start the actual FastAPI lifespan and exercise real routes, authentication stores, JSON conversation persistence, SQLite task/approval persistence and an actual calculator implementation. They cover health, local session continuation/deletion, local legacy claim and restart, two authenticated users' denied guessed sessions and tasks, disabled text dispatch, permitted approval with consumption and replay denial, malformed multipart, and an oversized multipart request. Task plan and approval state are seeded, rather than produced by a real agent. Chroma, embedding health and Ollama are substituted; an in-process Python socket guard is used. This does not prove OS egress isolation, real Chroma, live inference, native library traffic, or the whole deployment. Clearance and concurrent persistence coverage comes from the targeted synthetic component tests, not a complete live RAG flow. Exact-size and interrupted ingress coverage is in targeted tests, not an external proxy test. Full task resume and recovery as a live route workflow is not established.

Frontend commands (from `frontend`): `npm run lint` exit 0 with warnings; `.\node_modules\.bin\tsc.cmd --noEmit --incremental false -p tsconfig.app.json` exit 0; `.\node_modules\.bin\tsc.cmd --noEmit --incremental false -p tsconfig.node.json` exit 0; `.\node_modules\.bin\vite.cmd build --outDir <new-temporary-directory> --logLevel warn` exit 0 with large-chunk warning. The temporary build avoids overwriting existing `dist`; this is not the `npm run build` command. No browser tests were executed.

## Blocked and not run

Full Python suite: **BLOCKED** after automatic review rejected an earlier attempt involving an unidentified outbound telemetry request; destination and call path remain unavailable. No retry was made. OS-level egress denial and browser network capture: **BLOCKED**. Real Chroma/RAG ingestion, Qwen, Nomic, LLaVA, DOCX/XLSX generation, real agent planning and verified sandbox execution: **NOT RUN** in this verification, because an adequately isolated environment has not been demonstrated. Previous local model successes in older reports are historical and are not current end-to-end evidence.

## Future verification in an approved isolated environment

Use a dedicated disposable Windows VM or equivalent with independently verified OS/network policy blocking external egress while allowing only required loopback services; verify isolation using authorized network monitoring, not library settings or mocks. Keep the guest disconnected from production data and credentials. Export a disposable copy of this commit plus the reviewed changes; use dedicated temporary conversation, auth/task databases, document, Chroma, upload, log and artifact directories, with synthetic documents. Confirm model files are already present and no download is triggered. Obtain authorization consistent with the earlier automatic-review decision before any previously rejected suite retry. Then run the documented targeted commands, approved full suite, browser capture, real Qwen/Nomic/LLaVA and cited RAG, artifact, agent, approval and sandbox workflows. Record commands, case counts, traffic evidence and outputs; failures must remain visible. See `TELEMETRY_AND_EGRESS_AUDIT.md` for the unresolved telemetry investigation.

## Browser history follow-up, 2026-11-07

The earlier frontend findings are addressed in code, pending browser verification. The sidebar loads only sessions listed and read through authenticated server routes. The old browser-wide history key is not read or deleted and is not attributed to authenticated or local users. Logout/account changes unmount chat, abort its stream, invalidate old responses and notify other tabs where BroadcastChannel is supported. Delete waits for server success; errors retain the visible row. Account switching, legacy key isolation, async invalidation and deletion were covered by 4 Node unit tests with fakes; authenticated read/delete was exercised in 2 isolated actual-app route tests. There are no installed browser/component test dependencies and no real browser test was executed.

Compatibility limitation: server conversation persistence provides role/content only, so reconstructed history cannot restore local-only timestamps, display renames, source/tool events, images or interactive pending approval state across a page reload. In-session rich state is retained in memory during same-user navigation. Legacy local-only conversations remain preserved in browser storage but cannot currently be viewed or migrated through the UI. This is an open functional regression and Phase 1.5 remains PARTIAL. Multi-tab behavior on browsers without BroadcastChannel remains unverified. See SECURITY_REGRESSION_RESULTS.md for exact tests.

## Phase 1.5.1 conversation recovery

Stable message IDs and timestamps, owner-scoped titles, unknown-field preservation and a local-only explicit legacy export are implemented in a limited schema extension. Rich source/tool/artifact and approval restoration remain incomplete; browser verification is not done. Phase 1.5 remains PARTIAL. See CONVERSATION_RECOVERY_REPORT.md for migration, rollback, tests and blockers.
