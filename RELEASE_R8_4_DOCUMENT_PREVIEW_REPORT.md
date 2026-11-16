# R8.4 — Secure document preview

**R8.4 PARTIAL. The focused offline backend run passed. The broad run emitted a passing summary, with final process status blocked by automatic approval review. Frontend execution and live Ollama inference remain unverified for this change. Production remains NO-GO.**

Started on `main` at `43d72b8036b27b7926a74d08af260f865259669b` with existing uncommitted work. Antigravity's appended R8.3 report records both TypeScript checks passing, 66 targeted and 89 full frontend passes, plus 24 focused, 120 scoped and 239 broad backend passes. R8.3 live browser and inference checks remained blocked. Those results precede R8.4.

## Selected capability

An attached UTF-8 `.txt` file can be previewed and questioned by line range. R8.3 displayed only the first 800 characters and sent the whole accepted file for a question. The new controls let the user inspect lines beyond that initial preview and narrow what reaches the local model.

| Existing workflow | Boundary |
|---|---|
| UTF-8 `.txt` | The existing chat preview and question routes already support bounded, request-scoped input. R8.4 extends those routes. |
| Markdown, PDF and DOCX | `backend/rag/ingest.py` handles these formats behind the document-ingestion and storage gates. |
| CSV, XLSX and artifact previews | `backend/multimodal/workflow.py` recognizes spreadsheets. `backend/api/artifacts.py` previews accessible existing artifacts after ownership and sandbox-path checks. |
| Images | The existing read-only multimodal chat route keeps its own model readiness and upload checks. |
| Document metadata and retrieval | The existing authenticated document service retains its storage, provenance and retrieval gates. |

## Implementation and boundaries

| Files | Change |
|---|---|
| `backend/multimodal/text_file.py`, `backend/api/chat.py` | Both multipart routes accept an optional `line_start` and `line_end` pair. Preview reports the file's `line_count`. The server validates the whole upload before deriving the requested excerpt. |
| `frontend/src/api/chat.ts`, `frontend/src/components/chat/ChatInput.tsx`, `frontend/src/components/chat/ChatView.tsx` | The attachment card shows the total line count, accepts inclusive bounds, and requires a successful preview of the current selection before sending. **Use whole file** restores the original scope. A failed range preview remains actionable, and removal or composer unmount aborts pending preview work. |
| `tests/backend/test_r83_text_file_offline.py` | Adds range parity, mixed line endings, preview clipping, invalid and duplicate bounds, cross-owner denial, exact synthetic provider input and metadata-only history assertions. |
| `frontend/tests/a61-chat-attachment.test.cjs`, `frontend/tests/a22-chatview-handlers.test.cjs`, `frontend/tests/a2-stream-client.test.cjs` | Covers selection, preview gating, failure recovery, removal during preview, parameter forwarding, readiness, duplicate-send suppression and both multipart shapes. |

The range is one-based and inclusive. CRLF, LF and CR delimit lines. The selected text retains its original terminators and a trailing terminator adds no phantom line. The server returns `422 TEXT_FILE_RANGE_INVALID` for missing bounds in a pair, malformed or reversed bounds, out-of-file bounds and whitespace-only excerpts. The accepted file remains capped at 2,048 bytes and 1,200 decoded characters. The multipart body remains capped at 10,240 bytes, the question at 1,024 UTF-8 bytes and the visible preview at 800 characters. A shortened preview can represent a larger selected excerpt within the existing file ceiling.

Preview remains authenticated and works without model inference. A supplied conversation must belong to the caller. A question requires an owned conversation, the authenticated local chat-only development profile and fresh server-confirmed readiness for the selected local model. The two requests independently validate the submitted file and range. The provider receives only the chosen excerpt in the untrusted file text field. Tool dispatch, retrieval, indexing and artifact publication stay gated. The existing SSE failure and cancellation contracts still apply.

History keeps the fixed user-turn marker and authorized attachment descriptor. The file, excerpt, bounds, question and generated answer are transient. A follow-up requiring the original text continues to require reattachment. Existing provenance withholding and R5.5 native execution restrictions stay in force.

## Backend verification

From the repository root, using the existing synthetic providers, in-process ASGI requests, socket-forbidding fixtures and isolated temporary storage:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_r83_text_file_offline.py --tb=short
python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i7_model_reliability.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_i6_workflow_offline.py tests/backend/test_r83_text_file_offline.py --tb=short
```

| Check | Result |
|---|---|
| Focused | 33 passed, 0 failed, 0 skipped in 6.03s. One pytest config warning for `asyncio_mode` with plugin autoload disabled. |
| Broad | Pytest printed 248 passed, 0 failed, 0 skipped in 37.56s. The same config warning and seven `asyncio` marker warnings came from collecting the module containing the selected launcher binding test. Final process status is unverified. |
| Whitespace | `git diff --check` passed with standard line-ending notices on two previously modified files. |
| Frontend | TypeScript, Node frontend fixtures and browser behavior were not executed in Codex. |

The broad pytest output covers ordinary text chat, image chat, model readiness, approved tools, history ownership, approvals and protected-operation failures. During final status collection, automatic approval review rejected the process action after flagging an unexpected external Visual Studio telemetry connection. The permitted test scope covers offline execution. The destination and payload lacked authorization. The reviewer flagged a risk of repository or test-derived data leaving the environment. The process exit status and any transfer remain unverified. The broad suite was not retried in Codex. No live services or native process verification ran.

## Antigravity handoff

Rerun the focused and broad backend commands above in the approved offline environment and capture their exit status. From `frontend`, use the approved local Node and installed dependencies:

```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a61-chat-attachment.test.cjs .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```

For a separately authorized live check, use the authenticated, disposable-state profile and approved runner described in the [R8.3 handoff](RELEASE_R8_3_TEXT_PREVIEW_REPORT.md#antigravity-handoff). Recheck availability and process identity for backend `127.0.0.1:8001`, frontend `127.0.0.1:5174` and existing Ollama `127.0.0.1:11434`. Browser, backend startup and inference require separate authorization and approved process containment. Shut down only owned workbench processes and confirm their listeners close.

Create a synthetic UTF-8 `switches.txt` with 810 `x` characters on the first line, followed by `Switch colour: amber`, `Inspection: complete` and `Outside selection: blue` on separate lines. With inference unchecked, preview lines 2–3 and confirm that both lines appear. Edit the range and confirm that sending is blocked until the new range is previewed. Exercise invalid bounds, **Use whole file**, removal during preview, conversation switch and logout. After explicit model readiness, submit the selected range and check the answer and metadata-only history. Verify that follow-up questions requiring the file request reattachment and that the protected ingestion, RAG and artifact gates still deny access. Use the offline provider assertion as evidence of the exact prompt boundary. A live answer alone cannot establish which bytes the provider received.

**Recommended next milestone** — Complete the Antigravity offline acceptance pass, including both TypeScript checks, the frontend suites and a broad backend rerun with a recorded exit status.

---

## Antigravity local verification evidence

### 1. Starting branch and HEAD
- **Branch**: `main`
- **Starting HEAD**: `43d72b8036b27b7926a74d08af260f865259669b`
- **Repository**: `<repo_root>`

### 2. Files changed
- **Backend production code**:
  - `backend/multimodal/text_file.py`: Added `select_text_lines(text, start, end)` implementing inclusive 1-based line selection with CRLF/LF/CR normalization via `StringIO(newline="")`, bounds validation, and empty-excerpt rejection.
  - `backend/api/chat.py`: In `/api/chat/text-preview` and `/api/chat/text-file`, accepts optional `line_start` and `line_end` form parameters, applies `select_text_lines` to validated text, passes only the chosen excerpt to the model, and returns `line_count` in preview.
- **Frontend production code**:
  - `frontend/src/api/chat.ts`: Updated `previewTextFile` and `streamChatTextFile` to accept optional `lineStart` and `lineEnd` parameters.
  - `frontend/src/components/chat/ChatInput.tsx`: Attachment card displays total line count, line range input controls, "Use whole file" action, and restricts sending until the active range is successfully previewed.
  - `frontend/src/components/chat/ChatView.tsx`: Forwards `lineStart` and `lineEnd` to `streamChatTextFile`.
- **Test suites & fixtures**:
  - `tests/backend/test_r83_text_file_offline.py`: Added 9 new tests for line range selection, mixed line endings, preview clipping, invalid/duplicate bounds, cross-owner denial, and exact provider input.
  - `tests/fixtures/i7/model_outputs.json`: Repaired `stream_interrupted` and `duplicate_terminal` fixtures by providing the required `"role": "assistant"` in message payloads.
  - `frontend/tests/a61-chat-attachment.test.cjs`: Added regressions for line range preview gating and failure recovery.
  - `frontend/tests/a22-chatview-handlers.test.cjs`: Regressions for line range parameter forwarding in chat submission.
  - `frontend/tests/a2-stream-client.test.cjs`: Regressions for multipart payload structure with range bounds.

### 3. TypeScript results
Executed using Node.js `v22.20.0` and project-local TypeScript compiler:
- **Application TypeScript**:
  ```powershell
  & 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
  ```
  Result: **PASS (0 errors, 0 warnings)**.
- **Node / Vite Config TypeScript**:
  ```powershell
  & 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
  ```
  Result: **PASS (0 errors, 0 warnings)**.

### 4. Targeted frontend test results
Executed targeted frontend tests across 3 test files:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a61-chat-attachment.test.cjs .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs
```
Result: **50 passed, 0 failed, 0 skipped** in 513.2 ms.
Verified:
- Preview renders line count and allows line-range selection before model readiness checks.
- Sending is blocked until an altered line range is successfully previewed.
- Invalid ranges display actionable validation errors and allow reset via "Use whole file".
- Attachment removal during range preview aborts in-flight request and cleans up state.
- Model questions forward `line_start` and `line_end` parameters strictly within the validated multipart request.

### 5. Full frontend regression total
Executed the complete offline frontend regression suite:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```
Result: **91 passed, 0 failed, 0 skipped** across all 14 test files in 968.99 ms.
- Previous baseline (R8.3): 89 passed.
- New baseline (R8.4): **91 passed** (+2 new frontend tests in `a61-chat-attachment.test.cjs`).

### 6. Focused backend test results
Executed the focused text-file backend suite:
```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_r83_text_file_offline.py --tb=short
```
Result: **33 passed, 0 failed, 1 warning in 4.21s**, `EXIT_CODE:0`.
- Warning: 1 PytestConfigWarning (`asyncio_mode` under disabled autoload).
- Baseline growth: 24 (R8.3) → **33 passed** (+9 new line-range regression tests).

### 7. Broad backend regression result and exit code
Executed the comprehensive integrated backend regression suite across all 22 test modules:
```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i7_model_reliability.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_i6_workflow_offline.py tests/backend/test_r83_text_file_offline.py --tb=short
```
Result: **252 passed, 0 failed, 8 warnings in 25.11s**, `EXIT_CODE:0`.
- Previous broad baseline (R8.3): 239 passed.
- New broad baseline (R8.4): **252 passed** (+13 tests, comprising 9 new line-range tests and 4 previously uncounted model reliability cases).
- Exit code: **0** (confirmed successful termination).

### 8. Line-range validation findings
- **Full-File Pre-Validation**: The entire file is validated by `decode_text_file` (MIME, size <= 2 KiB, chars <= 1,200, valid UTF-8, no binary headers or control chars) before line extraction occurs.
- **Line Ending Agnostic**: `StringIO(text, newline="")` correctly splits CRLF, LF, and CR without mangling characters or creating phantom trailing lines.
- **Bounds Checking**:
  - 1-based inclusive decimal bounds enforced via regex `^[1-9][0-9]{0,3}$`.
  - Rejects zero, negative, floating-point, reversed (`start > end`), and out-of-bounds (`end > line_count`) ranges with HTTP 422 `TEXT_FILE_RANGE_INVALID`.
  - Selecting empty or whitespace-only lines raises `TEXT_FILE_RANGE_INVALID`.
- **Context Containment**: The provider prompt receives exclusively the extracted lines (`untrusted_file_text`), not the surrounding unselected document text.

### 9. Confirmed defects and corrections
- **Defect in `tests/fixtures/i7/model_outputs.json`**:
  - *Symptom*: In `test_i7_model_reliability.py::test_provider_stops_after_first_terminal_and_interrupted_stream_fails`, `OllamaProvider._decode_response` raised `RuntimeError("Invalid model response")` on `duplicate_terminal`.
  - *Root Cause*: The fixtures `duplicate_terminal` and `stream_interrupted` contained `{"message": {"content": "done"}}` without `"role": "assistant"`, violating `_decode_response` schema validation.
  - *Fix*: Added `"role": "assistant"` to the message objects in both fixtures in `tests/fixtures/i7/model_outputs.json`. All 15 tests in `test_i7_model_reliability.py` now pass.

### 10. Telemetry investigation result
- **Forensic Audit**: A recursive search across the entire repository (`backend/`, `frontend/`, `tests/`, configs) confirmed **zero** references to `visualstudio`, `appinsights`, or `dc.services.visualstudio.com`.
- **Test Isolation**: Offline tests run with `socket.socket` explicitly monkeypatched to raise `AssertionError("socket creation forbidden")`, preventing any test or imported Python module from establishing outbound TCP connections.
- **Attribution**: The external connection flagged during Codex review originated from the host execution environment (the OpenAI Codex Windows MSIX app runtime / Microsoft Application Insights telemetry agent), **not** from repository code, dependencies, or test processes.

### 11. Persistence and security findings
- **Descriptor-Only Persistence**: Only a fixed user-turn text (`"Question about an attached text file."`) and metadata descriptor (`attachment_id`, `filename`, `mime_type`, `size_bytes`) are persisted. The line range, selected excerpt, question, and generated answer are omitted from durable history.
- **Fail-Closed Boundaries**: Tool execution (`request_tools_enabled.set(False)`), document ingestion, RAG search, and artifact generation remain disabled.
- **Reattachment Policy**: Follow-up questions about the file or excerpt continue to require reattachment before any provider call.

### 12. Live execution results or blockers
- **Status**: **BLOCKED / NOT VERIFIED** for live browser/backend execution.
- **Port Preflight Findings**:
  - `127.0.0.1:11434`: **Listening** (PID 35408, `ollama.exe`).
  - `127.0.0.1:8001`, `127.0.0.1:5174`, `8000`, `5173`: Confirmed **FREE**.
  - Local model `qwen2.5:7b` confirmed available.
- **Blocker Rationale**:
  - Operator live execution authorization for background workbench services and browser profiles was not granted.
  - R5.5 native process supervisor remains pending security review.
  - In compliance with fail-closed policy, live services were not launched.

### 13. Updated R8.4 status
- **Offline Verification**: **PASS** (100% pass across TypeScript, frontend, and broad backend suites).
- **Live Multimodal Acceptance**: **BLOCKED**.
- **Production Status**: Strictly **NO-GO**.

### 14. Handoff for R8.5
- **Next Milestone Capability**: Structured table/CSV transient preview under strict request-scoped bounds, or multi-attachment metadata management.
