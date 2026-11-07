# Workflow smoke tests

## Security implementation continuation, 2026-11-07

Two-user conversation isolation was exercised through an in-process ASGI client with fake authentication/model services and isolated JSON storage. Restart, threaded and two-process persistence, clearance filtering over synthetic metadata, one-use approval dispatch and request-body limits were exercised in targeted tests. These are not real network/browser, full application startup, live RAG or real-model end-to-end tests. No full-suite retry was attempted.

## Continuation, 2026-11-07

| Check | Result | Scope |
|---|---|---|
| Qwen local inference | PASS: `READY`, 14.51s | Direct Ollama API, synthetic prompt, not application chat |
| Nomic embeddings | PASS: 768 dimensions, 5.7s | Direct Ollama API, synthetic text |
| LLaVA vision | PASS: `Red`, 12.93s | Direct Ollama API, generated red rectangle |
| DOCX and XLSX | PASS | Direct tool invocation with synthetic text/table in a temporary directory; reopened with python-docx/openpyxl; no model, agent or approval |
| Approval and denial | PARTIAL | Temporary SQLite approval-manager and registry regression; not end-to-end UI workflow |
| Restart and recovery | PARTIAL | Temporary legacy JSON and threaded write tests; ownership and multi-process scenarios unverified |
| RAG/citation, full agent workflow, browser offline, verified Docker code isolation | BLOCKED/NOT RUN | Full-suite telemetry auto-review rejection or no isolated verified setup |

These direct checks used only synthetic content. They do not replace live application workflow validation.

No live model workflow was verified: the environment denied loopback access to Ollama and a minimal local asyncio loop did not complete. No production data was used. Test durations and outputs below are unavailable.

| Synthetic workflow | Result | Reason |
|---|---|---|
| Basic chat | BLOCKED | Ollama and async runtime |
| Multi-step agent execution | BLOCKED | Ollama and async runtime |
| PDF/DOCX ingestion and grounded Q&A | BLOCKED | Embedding model and async runtime |
| DOCX report generation | NOT RUN | No isolated functioning agent workflow |
| XLSX workbook generation | NOT RUN | No isolated functioning agent workflow |
| Sandboxed code and verification | NOT RUN | No approved verified isolated Docker image established |
| Image/scanned document | BLOCKED | Vision model and async runtime |
| Tool-disabled negative | BLOCKED | Async test added, not completed |
| Approval-required execution | BLOCKED | Async runtime; full approval path not exercised |
| Conversation restart and recovery | NOT RUN | Ownership migration not implemented |

No mock-only test is counted as a live workflow.

## Final integration verification, 2026-11-07

Actual app routes with isolated persistence and fake models/vector services covered local sessions, legacy claim/restart, two-user session/task isolation, approval/replay and upload rejection. No live Qwen, Nomic, LLaVA, real RAG, artifact generation, full agent or verified sandbox workflow was run in this verification. See FINAL_INTEGRATION_REPORT.md; prior live-model records remain historical.

## Phase 1.5.1 conversation recovery

Synthetic actual-route coverage exercises ownership, title, legacy export policy and persistence with mocked model/vector services. No real browser, real model or rich-workflow reload was run. See CONVERSATION_RECOVERY_REPORT.md.

## Final rich-history continuation, 2026-11-07

Baseline `03370df` on `phase1-5-recovery-security`. Status remains **PARTIAL**. In `AgentEngine.chat_stream_with_tools`, successful and failed tool calls are now recorded atomically with a nonempty final assistant message, in bounded `tool_events_v1` metadata: up to 32 entries with generated invocation ID, restricted tool name and boolean result. Arguments, tool output, summaries, tokens, source text and filesystem paths are not persisted in that metadata. An actual engine test uses a fake model and fake tool registry. Authenticated history emits only validated allowlisted fields; the sidebar maps them to existing tool activity UI, which now labels failures correctly. No hydration invokes a tool or makes an approval actionable. Existing owners, unknown fields, local legacy export and the same JSON file/locking scheme remain in use.

This does not record interrupted calls (a final assistant message may never be saved), tools when the final response is empty, or events emitted by `chat_stream_with_tools_multimodal`, the planning/agent execution and resume paths, and arithmetic shortcut. It does not implement persisted source citations, artifact/attachment references, task status, approval history, interruption/failure state, retry/reconnect deduplication, full browser reload or authorization revalidation of source/artifact access. Invocation IDs are generated for this stored record and are not existing execution registry identifiers. Do not infer these missing features from this partial implementation. No live inference, artifact, browser, real Chroma or OS egress test was run. No persisted migration executed against user data; existing backup and rollback advice continues to apply.

Validation from a temporary Git-archive copy with modified backend/test files overlaid, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`: `python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase151_tool_history.py tests/backend/test_phase151_conversation_records.py tests/backend/test_phase15_app_integration.py tests/backend/test_phase15_memory_continuation.py tests/backend/test_memory.py tests/backend/test_phase15_registry_continuation.py -q -p no:cacheprovider --tb=short` — **26 passed, 0 failed, 0 skipped in 14.89s**. Covers fake model/tool engine loop, allowlist, restart/owner isolation, actual route serialization with synthetic persistence, and existing approval controls. `node --experimental-strip-types --test tests/historyRequests.test.mjs` — **4 passed, 0 failed, 0 skipped**; these are request-helper tests, not browser tests. Frontend `npm run lint` exit 0 with warnings, app and node `tsc.cmd --noEmit --incremental false -p ...` exit 0, and `vite.cmd build --outDir <new temporary directory> --logLevel warn` exit 0 with large-chunk warning. Full Python suite and OS egress proof remain BLOCKED after the previous automatic review rejection.
