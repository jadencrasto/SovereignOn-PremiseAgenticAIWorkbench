# R8.3 — Bounded UTF-8 Text Preview and Request-Scoped Understanding

**R8.3 PARTIAL. Focused and integrated offline backend verification passed. The changed frontend awaits Antigravity verification. Live Ollama inference is unverified. Production remains NO-GO.**

Started from `main` at `43d72b8036b27b7926a74d08af260f865259669b`. Existing files and uncommitted changes were preserved.

## Verified baseline

Antigravity's appended R8.2 results record both TypeScript checks passing, 59 targeted and 82 full frontend passes, 65 focused and 215 integrated backend passes. Its live multimodal check remained blocked. R8.1 recorded 75 frontend and 213 integrated backend passes. Those counts apply to the earlier milestones.

## Existing architecture and gap

The composer supported image previews and multipart chat. The Documents area uses the protected ingestion and retrieval service, and its disclosed content is gated by `document_content_allowed()`. Its existing decoder accepts legacy encodings for protected document ingestion. Neither path exposed strict, request-scoped UTF-8 preview.

R8.3 adds two endpoints to the existing chat router and reuses bounded multipart middleware, session ownership and authentication, readiness checks, the loopback Ollama adapter, SSE callbacks, and the existing history descriptor contract.

| Operation | Verified backend behavior |
|---|---|
| `POST /api/chat/text-preview` | Authenticated `VIEW_DATA` request with one `file` part. An existing conversation is checked when `session_id` is supplied. A signed-in new draft can preview before creating a conversation. Returns filename, byte count, text and truncation status with `Cache-Control: no-store`. No inference or history write. |
| `POST /api/chat/text-file` | An explicit file attachment, question and existing owned conversation. Inference is available only in the repository's authenticated local chat-only development profile. The frontend uses the server's profile capability and existing text-model readiness. The endpoint rechecks readiness and verifies the configured Ollama adapter's loopback URL and resolved model before dispatch. |
| Later chat and history | A successful request records a fixed user-turn description and an owner-scoped attachment descriptor containing ID, filename, MIME type and size. The question, file text and answer are transient. Earlier attachment turns are excluded from prompt context. Recognized earlier-file references, including immediate “What does it say?” follow-ups, ask for reattachment. Explicit comparisons with a previous file return a conflict. Owner-authorized deletion removes the conversation and its descriptor. |

## Validation and security boundary

- One multipart file with a 10 KiB whole-body ceiling, 2 KiB file ceiling and 1,200 Unicode character ceiling. The preview shows the first 800 characters and indicates truncation. Questions are capped at 1 KiB of UTF-8. Duplicate fields, extra parts and incomplete multipart endings return a stable form error. Multipart input stays below the parser's disk-spool threshold.
- A `.txt` extension, bounded display filename, and `text/plain` or generic binary MIME are required. An explicit charset must be UTF-8. Actual bytes undergo strict UTF-8 decoding with optional initial BOM. Blank files, embedded NUL, other unsafe controls and recognized binary signatures are rejected. Failures use fixed messages without local paths or file contents.
- Preview and answer content are rendered as plain text. File bytes are resubmitted only when the user sends a question. The prompt has a fixed system instruction and a user message with separate question and untrusted file-text fields. The model receives no prior conversation, tools, source IDs or retrieved content. Responses are buffered until the adapter reports completion and capped at 4,096 characters. Incomplete, invalid, cancelled or unauthorized answers produce no success event. The existing provider checks the response model identity.
- The route revalidates the authenticated session and conversation before presenting the answer. Model failures, upload disconnect and inference cancellation are covered with in-process fixtures. The frontend ignores late preview and stream results after removal, replacement, conversation switch or logout. Text and image files use distinct controls, with one attachment per submission.
- The workflow has no ingestion, indexing, filesystem tool, RAG retrieval or artifact publication path. No file contents or derived assistant response are added to durable history, so no durable provenance is granted. Existing R5.5, R6.4, protected document, approval and artifact gates remain in force.

## Files changed

- Backend: `backend/api/chat.py`, `backend/agent/memory.py`, `backend/health/routes.py`, `backend/multimodal/context.py`, `backend/multimodal/text_file.py`, `backend/security/request_limits.py`, `tests/backend/test_r83_text_file_offline.py`.
- Frontend: `frontend/src/api/chat.ts`, `frontend/src/api/capabilities.ts`, `frontend/src/components/chat/ChatInput.tsx`, `ChatView.tsx`, `MessageItem.tsx`, `frontend/src/types/index.ts`, and `frontend/tests/a61-chat-attachment.test.cjs`, `a22-chatview-handlers.test.cjs`, `a2-stream-client.test.cjs`.

## Executed offline verification

From the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_r83_text_file_offline.py --tb=short
python -m pytest -q tests/backend/test_r83_text_file_offline.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_a21_conversation_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_i6_workflow_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_r67_approval_stream_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_r41_shared_storage_gate.py --tb=short
git diff --check
```

- Focused: **24 passed, 0 failed** in 4.26s.
- Integrated: **120 passed, 0 failed** in 16.30s. Includes ordinary text chat and conversion, image chat, approval isolation, protected document ingestion denial and artifact gates. Each final run had one pytest configuration warning for `asyncio_mode` under disabled plugin autoload.
- The initial focused run exposed generator cleanup warnings for malformed forms. The text-file parser now explicitly closes its request iterator and partial buffers on failure. Both final runs completed without cleanup warnings.
- `git diff --check` passed. Standard Git line-ending notices concern previously modified files. Frontend tests and TypeScript checks were added to the handoff and not executed in Codex.

## Antigravity handoff

Use the two backend commands above in the approved offline workspace. For the broader baseline, rerun section 7 of the R8.2 Antigravity report with `tests/backend/test_r83_text_file_offline.py` added. From `frontend`, use the installed Node and project-local dependencies:

```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a61-chat-attachment.test.cjs .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a1-capability-render.test.cjs .\tests\a41-history-sidebar.test.cjs
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```

The existing live configuration uses backend `127.0.0.1:8001`, frontend `127.0.0.1:5174` and Ollama `127.0.0.1:11434`. Those addresses are configuration targets. Codex's CIM listener query was unavailable and its read-only `netstat` output showed no listener rows in its environment, so recheck the host before launch. The last Antigravity check found 8001 and 5174 free and Ollama listening on 11434.

If separately authorized, follow the disposable-state, egress and process containment conditions in the R8.1 handoff. Check port ownership with `Get-NetTCPConnection -State Listen`. Use the reviewed single-process backend configuration, `APP_ENV=development`, `LOCAL_CHAT_ONLY=true`, `AUTH_ENABLED=true`, `AUTH_DEV_OVERRIDE=false`, `BACKEND_HOST=127.0.0.1`, `BACKEND_PORT=8001`, `CORS_ORIGINS=http://127.0.0.1:5174` and `OLLAMA_BASE_URL=http://127.0.0.1:11434`. From the root, the reviewed backend interpreter may run `& .\backend\venv\Scripts\python.exe -m backend` only if that is the independently approved path. From `frontend`, set `$env:BACKEND_PORT = '8001'` and use `npm run dev -- --host 127.0.0.1 --port 5174 --strictPort` under the approved runner. Confirm both listeners and `/api/health/ready` before using disposable credentials. Keep unrelated listeners running and stop only the owned workbench processes through the runner, verifying closure.

Synthetic fixture:

```powershell
$fixture = Join-Path ([System.IO.Path]::GetTempPath()) ("sovereign-r83-" + [guid]::NewGuid() + ".txt")
@'
Maintenance note: the switch is amber.
Unicode check: café, नमस्ते.
<script>literal preview text</script>
'@ | Set-Content -LiteralPath $fixture -Encoding utf8
```

1. Sign in with a disposable account. Select the TXT control and open the fixture before checking the model. Expect the name, byte count and literal script tags. Upload a second synthetic file with 900 ASCII characters and check for the truncation label.
2. Try an empty file, a renamed PDF and invalid UTF-8. Confirm fixed errors and no preview or inference. Remove an invalid selection. Switch between image and text selections and check that the prior preview clears.
3. In the approved local profile, check the selected text model and ask “What color is the switch?” Expect amber and one successful SSE terminal sequence. Reload and check for the text-file descriptor only. Follow up with “What does it say?” and expect reattachment guidance. Check other-conversation isolation and owner-only deletion.
4. Stop an in-flight answer, change chats during preview, and log out during a separate request. Check for stale UI suppression. Record only synthetic results and process exit evidence. Remove the generated fixture when the approved run ends.

## Next feature

Add **line-range selection for UTF-8 preview questions** so the user can explicitly send a smaller excerpt from the current transient file while keeping the same bounds and model gates.

---

## Antigravity local verification evidence

### 1. Current branch and HEAD
- **Branch**: `main`
- **Starting HEAD**: `43d72b8036b27b7926a74d08af260f865259669b`
- **Repository**: `<repo_root>`

### 2. Files changed
- **Backend production code**:
  - `backend/multimodal/text_file.py` (new): Defines bounded constants (`MAX_TEXT_FILE_BYTES = 2048`, `MAX_TEXT_FILE_CHARS = 1200`, `TEXT_PREVIEW_CHARS = 800`, `MAX_TEXT_FORM_BYTES = 10240`, `MAX_TEXT_QUESTION_BYTES = 1024`), `text_filename`, `decode_text_file`, `text_understanding_enabled`, and `safe_text_attachment`.
  - `backend/api/chat.py`: Added `POST /api/chat/text-preview` and `POST /api/chat/text-file` endpoints, streaming runner `_stream_text_file`, form reader `_text_file_form`, and ownership validation.
  - `backend/agent/memory.py`: Updated `safe_tool_history` and message attachment handling to project validated text attachment descriptors (`mime_type: text/plain`).
  - `backend/health/routes.py`: Exposes `text_file_understanding` capability status scoped to authenticated local chat-only development profile.
  - `backend/multimodal/context.py`: Added `TEXT_REATTACH_MESSAGE`, `is_text_turn`, `requires_text_reattachment`, and unified history filtering.
  - `backend/security/request_limits.py`: Integrated text-file form size bounds into middleware checks.
- **Frontend production code**:
  - `frontend/src/api/chat.ts`: Added `previewTextFile` and `streamChatTextFile` client methods.
  - `frontend/src/api/capabilities.ts`: Added capability check for `text_file_understanding`.
  - `frontend/src/components/chat/ChatInput.tsx`: Added TXT file picker, preview card with character count, truncation indicator, question length counter, and reattachment notice.
  - `frontend/src/components/chat/ChatView.tsx`: Integrated text file question submission, session abort, and duplicate send suppression.
  - `frontend/src/components/chat/MessageItem.tsx`: Renders text-file attachment descriptor badge.
  - `frontend/src/types/index.ts`: Added `TextFilePreview` and attachment types.
- **Test suites**:
  - `tests/backend/test_r83_text_file_offline.py` (new): 24 focused backend tests covering text preview, validation, streaming, limits, isolation, and error boundaries.
  - `frontend/tests/a61-chat-attachment.test.cjs`: Added regressions for text preview rendering, failed preview handling, and unmount cancellation.
  - `frontend/tests/a22-chatview-handlers.test.cjs`: Added regressions for text-file question dispatch, readiness checks, and conversation switch aborts.
  - `frontend/tests/a2-stream-client.test.cjs`: Added regression for multipart text-file request format.

### 3. TypeScript verification results
Executed using Node.js `v22.20.0` and project-local TypeScript compiler without downloading dependencies:
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

### 4. Targeted frontend regression tests
Executed targeted frontend tests across 5 test files:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a61-chat-attachment.test.cjs .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a1-capability-render.test.cjs .\tests\a41-history-sidebar.test.cjs
```
Result: **66 passed, 0 failed, 0 skipped** in 704.2 ms.
Verified:
- Signed-in users can select allowed `.txt` files and inspect preview before model readiness.
- Empty files, files exceeding 2 KiB, unsupported MIME types, and malformed files are rejected with explanatory errors.
- Preview text is rendered safely as inert text (no HTML or script execution).
- Text file attachment removal clears preview and resets composer state.
- In-flight text-file questions are aborted on conversation switch or logout; late callbacks are ignored.
- Text-file question dispatch requires fresh server-confirmed text model readiness.
- Image chat functionality and controls remain unaffected.
- Protected document actions remain unavailable.

### 5. Full frontend test count
Executed complete offline frontend regression suite:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```
Result: **89 passed, 0 failed, 0 skipped** across all 14 test files in 930.9 ms.
- Previous baseline (R8.2): 82 passed.
- New baseline (R8.3): **89 passed** (+7 new regression tests).

### 6. Focused backend test count
Executed the focused R8.3 backend suite:
```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_r83_text_file_offline.py --tb=short
```
Result: **24 passed, 0 failed, 1 warning** in 3.14s.
- Warning: 1 PytestConfigWarning (`asyncio_mode` under disabled autoload).
- Verified: UTF-8 validation, 2 KiB / 1,200 char limits, empty/binary rejection, authentication & ownership, request-scoped content, no durable file bytes, no durable question/answer in history, reattachment requirement, model readiness verification, provider cancellation, tool rejection, and RAG/document/artifact isolation.

### 7. Broad integrated backend count
1. **Codex Selected Integrated Suite (9 test files)**:
   ```powershell
   $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
   python -m pytest -q tests/backend/test_r83_text_file_offline.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_a21_conversation_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_i6_workflow_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_r67_approval_stream_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_r41_shared_storage_gate.py --tb=short
   ```
   Result: **120 passed, 0 failed, 1 warning** in 10.72s. (Confirms Codex's 120-test result was an intentional scoped subset).
2. **Broad Integrated Backend Regression Suite (22 test files)**:
   ```powershell
   $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
   python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i7_model_reliability.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_i6_workflow_offline.py tests/backend/test_r83_text_file_offline.py --tb=short
   ```
   Result: **239 passed, 0 failed, 8 warnings** in 22.66s.
   - Previous broad baseline (R8.2): 215 passed.
   - New broad baseline (R8.3): **239 passed** (+24 new backend tests from `test_r83_text_file_offline.py`).
   - Warnings: 1 config warning (`asyncio_mode`) and 7 unregistered `asyncio` mark warnings under disabled autoload.

### 8. Explanation of the 2 KiB limit
- **Rationale for 2 KiB (2,048 bytes) Ceiling**:
  - The tentative handoff note suggested 64 KiB, but implementation analysis confirmed that a 2 KiB limit is the strictly correct architectural boundary for transient text preview.
  - A 2 KiB file size combined with form overhead yields a maximum multipart request body of 10 KiB (`MAX_TEXT_FORM_BYTES = 10,240 bytes`). This guarantees that file uploads stay strictly beneath server framework disk-spooling thresholds (e.g. Starlette / SpooledTemporaryFile), ensuring 100% in-memory processing without leaving residual temporary files on disk.
  - The 1,200 character ceiling fits conservatively inside single-turn prompts for local quantized models (e.g. `qwen2.5:7b`), eliminating latency spikes, context truncation, or OOM risks on local CPU/GPU hardware.
  - The UI preview displays up to 800 characters with an explicit truncation indicator for files between 800 and 1,200 characters.
  - Strict byte validation rejects binary signatures, NUL bytes, and unsafe Unicode control characters before any decoding or provider dispatch.
  - Frontend and backend limits are fully synchronized (2,048 bytes, 1,200 characters, 1 KiB question ceiling).

### 9. Confirmed fixes
- Multipart form parser explicitly closes request iterators and releases partial buffers on failure, preventing generator leaks.
- Independent endpoints for preview (`/api/chat/text-preview`) and question understanding (`/api/chat/text-file`) prevent accidental model invocation during inspection.
- The composer enforces explicit reattachment prompts whenever conversation history contains earlier text file turns.
- No new regressions or defects were detected during Antigravity's verification pass.

### 10. Request-scoped storage behavior
- **No File Content in History**: Neither raw bytes, decoded text, nor file excerpts are written to durable storage (`data/memory.json`).
- **No Question or Generated Answer in History**: Unlike standard chat turns, `_stream_text_file` persists only a fixed user-turn summary (`"Question about an attached text file."`) and an owner-scoped descriptor (`attachment_id`, `filename`, `mime_type: text/plain`, `size_bytes`). The specific question and generated answer are omitted from durable conversation storage.
- **Reattachment Enforcement**: Subsequent follow-ups referencing earlier text files trigger `requires_text_reattachment()`, returning a 409 conflict (`TEXT_FILE_REATTACH_REQUIRED`) before model or tool invocation.

### 11. Protected operation results
- Document ingestion and RAG search in `backend/api/documents.py` remain strictly gated and offline.
- Text-file processing performs no vector indexing, no OCR, and no filesystem storage writes.
- Tool dispatch is strictly disabled during text-file questions (`request_tools_enabled.set(False)`), and attempts by models to emit tool-call markup trigger `INVALID_TOOL_REQUEST`.
- Artifact publishing and code execution remain disabled.

### 12. Live execution results or blockers
- **Status**: **BLOCKED / NOT VERIFIED** for live browser/backend execution.
- **Port Preflight Findings**:
  - `127.0.0.1:11434`: **Listening** (PID 35408, `ollama.exe`).
  - `127.0.0.1:8001`, `127.0.0.1:5174`, `8000`, `5173`: Confirmed **FREE**.
  - Local model `qwen2.5:7b` (tools, completion) confirmed available.
- **Blocker Rationale**:
  - Operator live execution authorization for interactive browser profiles and background workbench processes was not granted.
  - R5.5 independent native process supervisor remains pending security review.
  - Under fail-closed governance, live services were not started.

### 13. Security controls preserved
- R5.5 native process supervisor pending independent review.
- R6.4 durable provenance-key protection remains blocked.
- Protected document ingestion, RAG vector indexing, artifact publishing, and production execution gates remain in fail-closed development state.
- In-memory socket barrier (`socket.socket` raising `AssertionError`) prevents unauthorized network telemetry during offline tests.

### 14. R8.3 status
- **R8.3 Offline Verification**: **PASS**.
- **Live Acceptance**: **BLOCKED**.
- **Production Status**: Strictly **NO-GO**.

### 15. R8.4 Codex handoff
- **Target Capability**: Line-range selection for UTF-8 preview questions.
- **Requirements**:
  - Allow users to select specific line ranges from an attached `.txt` file for targeted questioning.
  - Maintain the existing 2 KiB / 1,200 character boundary, strict UTF-8 validation, and transient request-scoped history policy.
  - Preserve protected document RAG and artifact publication gates.
