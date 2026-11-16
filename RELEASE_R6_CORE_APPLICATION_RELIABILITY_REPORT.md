# R6.1 — Core application reliability

**R6.1: PARTIAL. R1–R5 remain PARTIAL; production: NO-GO.** Backend changes have focused synthetic evidence. The frontend corrections have not been executed or type-checked, and no live application, browser or model was used.

Starting branch: `main`; starting HEAD: `2b96fee1f24777247f4efb7a18293eef4275e9cb`. Existing uncommitted R5 report changes were preserved. Existing architecture uses authenticated FastAPI chat routes, server-issued owned conversations, process-local verified numeric receipts, permitted tool dispatch, explicit model readiness, Ollama streaming and React SSE/history components. Protected RAG, artifact operations and approval resume remain unavailable.

| Priority / confirmed defect | Location and user impact | Correction / verification |
| --- | --- | --- |
| P0 — Truncated provider answer could look complete | `backend/models/ollama_provider.py`: `done=true` with a non-stop completion reason was accepted and could be stored and followed by SSE `done`. | Reject non-stop terminal reasons as incomplete. New provider and production-route tests confirm error, no `done` and no persisted assistant reply. Missing `done_reason` retains existing compatibility; real-provider behavior remains unverified. |
| P1 — Provider iterator not closed promptly | The Ollama NDJSON iterator was left to async-generator finalization on error/cancellation; the first regression run reported a cleanup warning. | Explicitly close the line iterator with the response context. A focused cancellation test checks generator closure; the later runs no longer showed that warning. |
| P1 — Stopped plain response looked finished | `frontend/src/components/chat/ChatView.tsx` only marked interrupted planning/executing workflows, leaving ordinary partial text without an interruption status. | Mark any stopped streaming message interrupted; `MessageItem.tsx` uses response-oriented language. Static inspection only; frontend execution not verified. |
| P0 — Retry could repeat an uncertain tool operation | `ChatView.tsx` resubmitted the last user message for any error, including an error after tool dispatch. | Remove the chat-level retry callback; users can submit a new request deliberately. Static inspection only; frontend execution not verified. |

Modified files: `backend/models/ollama_provider.py`, `tests/backend/test_a2_provider_offline.py`, `frontend/src/components/chat/ChatView.tsx`, `frontend/src/components/chat/MessageItem.tsx`, and this report. No R5.5 native protocol files were changed.

The existing production-route synthetic workflow exercises ordinary chat, server-created conversation, calculator **42 → 126 → 131**, conversion, rename, history and cross-owner denial. The focused suite also exercises readiness, provider interruption, cancellation, authorization freshness and blocked document/artifact operations. These tests use fake providers and do not prove real model selection, real frontend/backend streaming, session switching or browser behavior. Existing receipt provenance and server ownership rules were not weakened. The SSE client buffers split lines; full browser parsing under arbitrary network fragmentation remains unverified. Completed history after restart remains subject to existing provenance restrictions. No unsafe automatic retry was added.

From repository root, with fake providers, in-process ASGI and temporary files; no real socket or service:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_a2_provider_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r12_readiness_offline.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py
python -m pytest -q tests/backend/test_a2_provider_offline.py
```

The first command's initial run passed **72 tests with 2 warnings**, including an unraisable async-generator finalization warning. After explicitly closing the iterator, its repeat passed **72 tests with 1 warning in 17.33 s**. After adding a further cancellation-closure regression, the second command passed **7 tests with 1 warning in 1.59 s**. The remaining warning is pytest's unknown `asyncio_mode` option with plugin autoload disabled. Zero tests were skipped; no timeout occurred or external hard timeout was configured. `git diff --check` for the four modified code/test files passed.

No frontend tests, TypeScript, production build, mounted React or browser checks were executed. The prior automatic approval review rejected frontend build continuation because of possible registry access; the available frontend harness invokes the TypeScript transpiler. No alternate execution path or dependency installation was used. **Frontend behavior, TypeScript, production build, mounted React, browser, real startup and live Ollama remain NOT VERIFIED.**

The next milestone is separately authorized isolated frontend execution and a bounded real chat-only runtime test, including actual Ollama streaming, history, cancellation and process cleanup. The R5.5 protocol remains frozen pending independent review; no live service, VM, launcher, protected capability, commit or push was started or enabled here.
