# R6.2 — Chat state and recovery

**R6.2: PARTIAL. Production: NO-GO.** Starting branch `main`, HEAD `2b96fee1f24777247f4efb7a18293eef4275e9cb`. Earlier uncommitted work, including R6.1, was preserved. R5.5 remains frozen.

The authenticated route checks server-side readiness and conversation ownership; the engine stores user messages, streams provider or tool events, and stores eligible completed assistant responses. ConversationMemory uses a locked disk-backed conversation store, not SQLite for message history; the task store uses SQLite. The SSE wrappers relay deltas and terminal events. The React client guards callbacks by the current stream controller, aborts on conversation changes, and loads history from the server. Partial responses are not durably saved as interrupted assistant messages; a reload may show only the user turn. Existing provenance restrictions can withhold assistant content on later reload, including after process restart. No schema migration or client-authoritative persistence was introduced.

| Severity / confirmed defect | Correction and evidence |
| --- | --- |
| P0 — Planning failure or cancellation could be followed by `done`, even by later engine output. | `backend/api/chat.py` closes the engine iterator after the terminal task event. Production-route tests inject partial output followed by failure or cancellation and check there is no later delta or `done`. |
| P0 — Tracked-tool failure, or cancellation during finalization, could be followed by `done`. | The tracked wrapper now returns after failure and emits cancellation for finalization races. A fake tool stream tests cancellation after a tool result; no conclusion is made that a tool's effects were undone. |
| P1 — The client waited for `done` after a failed or cancelled task. | Both SSE clients treat those task events as terminal; `ChatView` stops its loading state while preserving failed/cancelled status. New frontend contract tests were added but not executed. |
| P1 — A delayed delete of conversation A could clear a newly selected B. | `ChatHistorySidebar` checks the current selection when deletion completes. A regression test was added but not executed. |
| P2 — Stop toast asserted interruption although browser abort does not confirm server cancellation. | The toast now says response reception stopped and the operation may still run. |

Changed files: `backend/api/chat.py`, `frontend/src/api/client.ts`, `frontend/src/components/chat/ChatView.tsx`, `frontend/src/components/chat/ChatHistorySidebar.tsx`, `tests/backend/test_r51_core_slice.py`, `tests/backend/test_a33_production_tools_offline.py`, `frontend/tests/a2-stream-client.test.cjs`, `frontend/tests/a22-chatview-handlers.test.cjs`, `frontend/tests/a41-history-sidebar.test.cjs`, and this report. Existing R6.1 modifications to other files were retained.

Backend verification, repository root, synthetic providers, temporary storage and in-process ASGI only:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_a2_provider_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r12_readiness_offline.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py
git diff --check
```

Final run: **76 passed, 1 warning, 0 skipped**, 15.76 seconds. The warning is pytest's unknown `asyncio_mode` configuration option with plugin autoload disabled. An earlier run passed 75 tests; the next run had 75 passed and one new test failed because it omitted the required owned-session setup. The setup was corrected before the final run. `git diff --check` passed. No external hard timeout was configured.

The existing synthetic tests cover owned history access, verified calculations and conversion, readiness, provider cleanup, authorization and protected-operation gates. They do not establish all disconnect, deletion, restart and side-effect timing guarantees. Failed and interrupted partial content is not persisted as a completed assistant response; it is not guaranteed to remain visible after reload. No request-idempotency mechanism or backend retry-safety classifier was added. Automatic UI retry remains removed; users must decide whether to submit again, and a lost connection does not establish whether an operation ran. Client abort is not proof of server cancellation or rollback.

Frontend tests, TypeScript, build, mounted React and browser: **NOT EXECUTED / NOT VERIFIED**. The available frontend tests use the restricted TypeScript transpiler; the earlier build denial remains in force. Live application startup, Ollama, actual network disconnect behavior and native process control remain unverified. No services, listeners, VM, native fixtures, frontend build, dependency downloads or protected operations were started. Authentication, readiness, authorization, RAG/artifact blocks and launcher restrictions remain in place.

**Next milestone:** obtain separate authorization for a reviewed offline frontend test environment and verify the new terminal-event and conversation-switching tests there.
