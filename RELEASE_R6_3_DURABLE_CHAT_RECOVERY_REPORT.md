# R6.3 — Durable chat recovery

**R6.3: PARTIAL. Production: NO-GO.** Starting branch `main`; HEAD `2b96fee1f24777247f4efb7a18293eef4275e9cb`. Existing uncommitted R5/R6 work was preserved.

Conversation messages use the existing locked, atomically replaced JSON-backed `ConversationMemory`; task state uses SQLite. This is not a SQLite message store, so no SQLite migration was made. The normal chat engine formerly saved only completed responses: provider errors and generator closure lost the partial assistant turn.

For ordinary plain-chat streams, `backend/agent/engine.py` now records exactly the text already emitted when provider iteration fails or the stream is closed. The existing store writes one new assistant turn with server-owned `response_state_v1` (`failed` or `interrupted`) and existing provenance. A finalization-time write avoids a disk replacement per token; the 11-character tool-syntax detection buffer is intentionally not saved. Failed persistence is logged without concealing the original error. The store checks the original owner/conversation-generation identity under its existing lock, so deletion or replacement prevents a stale write. Incomplete messages are excluded from later model input. History exposes the state, and the sidebar maps it to the existing message status. Completed-response behavior is unchanged.

This is **not a complete durable streaming lifecycle**: there is no pending/streaming checkpoint, request ID, duplicate-finalization protocol, or recovery of tokens after abrupt process loss. Provider creation or failures before entering the stream-iteration boundary may not create a record. It does not cover agent/tool/multimodal streaming. Existing JSON locking and atomic replacement do not establish cross-store transactions or guarantee persistence if finalization fails. Legacy records are unchanged. On a process restart, the record and state survive, but plain-chat provenance uses a process-local key, so content is withheld on API reload rather than weakening the existing authorization boundary. Recovery of readable text across restart remains unresolved.

Tool execution state is not transactionally bound to external side effects or to these records. A disconnected client cannot infer whether a tool ran, and a tool failure is not proof of no side effect. Existing task and approval records are used where present, verified receipts remain process-local and bound to existing identities, and automatic frontend retry remains removed. No new claim of retry safety, idempotency or exactly-once execution is made. Approval consumption, role checks, conversation ownership, and document/artifact restrictions were not changed.

Files changed for R6.3: `backend/agent/memory.py`, `backend/agent/engine.py`, `backend/api/chat.py`, `frontend/src/api/chat.ts`, `frontend/src/components/chat/ChatHistorySidebar.tsx`, `tests/backend/test_a2_provider_offline.py`, `tests/backend/test_a21_conversation_offline.py`, `frontend/tests/a41-history-sidebar.test.cjs`, and this report. The API and frontend files also contain preserved R6.2 changes.

Offline test command from repository root, using fake providers, temporary storage and in-process ASGI:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_a2_provider_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py
```

**57 passed, 1 warning, 0 skipped**, 14.15 seconds. The warning is pytest's unknown `asyncio_mode` option with plugin autoload disabled. The first, narrower run produced **39 passed and 2 failed** because older tests required partial messages not to be saved; those assertions were updated to require interrupted state and exclude the partial from subsequent model context. Tests include interruption, iterator cleanup, failure after partial output, same-process retrieval and persisted state after restart, ownership and blocked operations. They do not prove independent external-side-effect recovery, exactly-once behavior, duplicate requests, or complete process-crash recovery.

The frontend regression test was added but **NOT EXECUTED**: the available harness invokes the restricted TypeScript path. TypeScript, frontend build, mounted React, browser, live Ollama and actual application startup remain unverified. No service, native fixture, VM, frontend build, dependency download or protected capability was started or enabled. The launcher and R5.5 protocol remain untouched.

**Next milestone:** establish an independently reviewed durable provenance and operation-identity boundary before implementing restart-readable incomplete responses and tool-outcome recovery.
