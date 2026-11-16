# R8.2 — Multimodal Conversation Continuity and Safe Attachment Lifecycle

**R8.2 PARTIAL. Focused offline backend verification passed. Changed frontend behavior awaits Antigravity verification. Real image inference is UNVERIFIED. Production remains NO-GO.**

Started from `main` at `43d72b8036b27b7926a74d08af260f865259669b`. Existing working-tree and report changes were kept.

## R8.1 verification baseline

Antigravity reported zero TypeScript errors in both projects, 52 targeted frontend passes, 75 full frontend passes across 14 files, 73 focused backend passes, and 213 integrated backend passes across 21 modules. The authorized read-only inventory found `qwen2.5:7b` and `llava:7b` available locally. Its preflight observed 8001 and 5174 free and Ollama listening on 11434. Live browser, backend startup and Ollama inference were blocked and were never run. The R7.6 full baseline was 67 frontend and 186 backend passes.

## Confirmed defects and corrections

| Finding | R8.2 behavior | Evidence |
|---|---|---|
| The model builders included earlier image-bearing user turns as plain text, although the old attachments and withheld answers were unavailable. The stored message format also accepts legacy `images` fields, which the provider would serialize if forwarded. | Chat and task-reasoning context omit earlier image turns and any history message carrying legacy image bytes. The reasoning stage can include the prompt for the exact current image turn. A later text request is built from eligible text history. | Current image context and legacy exclusions pass in the focused backend suite. |
| Text follow-ups could ask the model to describe an unavailable image. A request with a new attachment could ask for a comparison with the older image. | Recognized references within an owned conversation get `IMAGE_REATTACH_REQUIRED` before inference or tool dispatch. Streaming text gets one error event. JSON text and a new-image comparison get 409. The composer offers reattachment guidance when the conversation has attachment metadata. | Endpoint and message-history tests passed. The composer regression is ready for Antigravity. |
| Image prompts such as “approve” were intercepted by the browser's text approval shortcut. | Image-bearing submissions use the read-only multipart path. The backend continues to force tools off and skip document retrieval. | Backend tool rejection and no-retrieval tests passed. The approval shortcut regression awaits frontend execution. |
| A draft could finish opening after the selected models or their readiness changed, and starting another chat from an already-empty draft left the composer mounted. | Before dispatch after draft creation, image submission rechecks the latest committed model selection and readiness. Explicit new-chat actions remount the empty draft composer, releasing its image preview. | Frontend regressions added and pending execution. |
| A delayed demo image download could invoke its old send callback. A failed download could send the same prompt as a text-only request. Sent previews were tracked only after a render effect. | Demo requests capture the conversation selection and are cancelled when the listener is retired. A failed or stale image download leaves its prompt unsent. Sent preview URLs are registered when created and revoked on switch, reset, conversation loss or unmount. | Frontend regressions cover late and failed demo downloads, Stop, switch and logout cleanup. |

## Frontend and backend contract

- Authentication and owner checks still precede image acceptance. The upload remains bounded and validated as PNG, JPEG or WebP. Image bytes are sent to the selected, checked vision model for that request. The text model receives an untrusted interpretation and the current prompt. Image requests preserve independent model identity and freshness checks, provider response validation, and terminal error handling.
- Detected image follow-up wording is deliberately conservative. Ordinary text remains available, and the composer explains that image questions need a fresh attachment. The route does not attempt general intent classification. Only one image can be analyzed per request.
- Stopping reception marks the visible response interrupted and drops late callbacks. The synthetic backend regressions still exercise provider and stream cleanup. Live cancellation timings and provider shutdown remain unverified.
- Attachment metadata stays on the originating user message. Reload shows the descriptor without a preview. Owner-authorized deletion removes access to the conversation and its metadata. Image-derived assistant responses remain withheld on reload under the current provenance policy. No new durable image-byte storage was added. Legacy records were left intact and their image payloads are excluded from inference.
- Protected ingestion, retrieval, local OCR indexing, file writes, artifact publication and approval enforcement were not changed. R5.5 remains pending independent review. R6.4 durable provenance remains blocked.

## Files modified

- Backend: `backend/multimodal/context.py`, `backend/api/chat.py`, `backend/agent/engine.py`, `tests/backend/test_a61_multimodal_offline.py`.
- Frontend: `frontend/src/components/chat/ChatView.tsx`, `frontend/src/components/chat/ChatInput.tsx`, `frontend/tests/a22-chatview-handlers.test.cjs`, `frontend/tests/a61-chat-attachment.test.cjs`.

## Regression results

**65 passed, 0 failed in 8.30s**, with one expected pytest configuration warning for `asyncio_mode` under disabled plugin autoload. Test coverage includes the two current-image submissions, no-provider reattachment errors, plain text and separate conversations, descriptor ownership and deletion, legacy provider context exclusions, provider failures, cancellation, bounded ingress, model changes and readiness expiry. `git diff --check` passed with standard line-ending notices on two files already changed before this milestone.

Focused offline command, executed from the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_a61_multimodal_offline.py tests/backend/test_a21_conversation_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_i6_workflow_offline.py --tb=short
```

The R8.1 integrated backend command is also available in section 8 of `RELEASE_R8_1_MULTIMODAL_VISION_REPORT.md` for a complete local regression. That 213-pass result applies to the R8.1 baseline.

## Antigravity handoff

Run the focused backend command above. From `frontend`, use the authorized Node installation and existing dependencies for both TypeScript checks, targeted regressions and the complete frontend suite:

```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a22-chatview-handlers.test.cjs .\tests\a61-chat-attachment.test.cjs .\tests\a1-capability-render.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a41-history-sidebar.test.cjs
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```

If explicitly authorized under the R8.1 startup and cleanup conditions, recheck ownership of 8001, 5174 and the existing loopback 11434 listener before running the live workflow. Verify process containment, egress controls and disposable state first. Use only the reviewed local chat setup and leave unrelated listeners running.

1. Use two non-sensitive PNGs such as a blue square and a green circle. Check both models, attach the first image and ask what shape is visible.
2. Ask “What color was the previous image?” without an attachment. Expect reattachment guidance and no success event. Ask an unrelated text question, then attach the second image and ask about it. Expect fresh analysis of the second image. For an explicit comparison with the earlier attachment, expect the request to be declined with reattachment guidance.
3. Exercise image removal and new-draft reset. Stop an image stream and switch conversations, then separately log out midstream. Check the interrupted label, preview cleanup, and suppression of late updates. Expire readiness or switch the vision selection while a draft is opening and check that the stale request is never sent.
4. Reopen the owned conversation and check metadata-only history and existing withholding. Try reading or deleting it with another disposable account, then delete with its owner. Record redacted event types and observed process cleanup.

Frontend tooling, live startup, browser behavior and Ollama inference were not executed in Codex.

## Next workbench capability

**Bounded request-scoped UTF-8 text preview** is the next safe increment. The existing documents API uses the ingestion service and blocks persisted content disclosure under `document_content_allowed()`. The parser in `backend/rag/ingest.py` already separates plain-text decoding from the PDF and DOCX paths. A preview can start with user-supplied UTF-8 text, displayed as plain text under a strict size limit. Scope it to transient input in the active session, with no indexing, durable retention, tools or access to existing repositories. Keep the protected API gates in place and qualify local model analysis separately.

**Next action.** Run the authorized Antigravity offline checks for the R8.2 changes before scheduling live qualification.

---

## Antigravity local verification evidence

### 1. Current branch and HEAD
- **Branch**: `main`
- **Starting HEAD**: `43d72b8036b27b7926a74d08af260f865259669b`
- **Repository**: `<repo_root>`

### 2. Files modified
- **Backend production code**:
  - `backend/multimodal/context.py` (new): Defines `IMAGE_REATTACH_MESSAGE`, regex matchers (`_IMAGE_REFERENCE`, `_EARLIER_IMAGE_REFERENCE`, `_IMMEDIATE_VISUAL_FOLLOWUP`), `is_image_turn`, `requires_reattachment()`, and `request_scoped_history()`.
  - `backend/api/chat.py`: Enforces reattachment pre-check in text chat and multimodal routes, applies `request_scoped_history()`, excludes image submissions from text approval shortcuts.
  - `backend/agent/engine.py`: Replaces earlier image user turns and legacy image payloads with filtered request-scoped history in reasoning context.
- **Frontend production code**:
  - `frontend/src/components/chat/ChatView.tsx`: Synchronous vision readiness recheck after draft creation, session-keyed composer remounting on new chat, preview URL registration and revocation on session switch/logout, and demo download cancellation.
  - `frontend/src/components/chat/ChatInput.tsx`: Surfaces reattachment notice (`hasEarlierImage`) when earlier images are present without a new attachment; releases preview URL on attachment removal.
- **Test suites**:
  - `tests/backend/test_a61_multimodal_offline.py`: Added offline coverage for reattachment prompt rejection, exclusion of legacy image turns from provider context, and comparison reattachment requirements.
  - `frontend/tests/a22-chatview-handlers.test.cjs`: Added regressions for preview revocation on switch/reset/logout, draft creation vision recheck, approval shortcut bypass for image chat, and failed/stale demo download handling.
  - `frontend/tests/a61-chat-attachment.test.cjs`: Added regressions for preview revocation on removal and reattachment guidance display in composer.

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
Executed the targeted R8.2 frontend suite across 5 test files:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a22-chatview-handlers.test.cjs .\tests\a61-chat-attachment.test.cjs .\tests\a1-capability-render.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a41-history-sidebar.test.cjs
```
Result: **59 passed, 0 failed, 0 skipped** in 727.6 ms.
Verified:
- Approval-like text with an attachment stays on the read-only image route and bypasses text approval shortcut.
- Switching vision models or expiring readiness while a draft opens safely discards the pending image request.
- Starting a new chat from an empty draft remounts the composer with a fresh key and revokes previews.
- Stopping an image stream and switching sessions drops late stream callbacks and revokes the preview URL.
- Failed or stale demo image downloads leave prompt unsent and cannot dispatch into an alternate session.
- Removing an attachment revokes its object URL while preserving plain-text sending.
- The composer renders explicit reattachment guidance when an earlier image is present in history.

### 5. Full frontend test count
Executed the complete offline frontend test suite:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```
Result: **82 passed, 0 failed, 0 skipped** across all 14 test files in 964.5 ms.
- Previous baseline (R8.1): 75 passed.
- New baseline (R8.2): **82 passed** (+7 new regression tests).

### 6. Focused backend regression tests
Executed the focused R8.2 backend suite:
```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_a61_multimodal_offline.py tests/backend/test_a21_conversation_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_i6_workflow_offline.py --tb=short
```
Result: **65 passed, 0 failed, 1 warning in 5.35s**.
- Warning: 1 PytestConfigWarning (`asyncio_mode` under disabled autoload).
- Verified: Reattachment required for explicit image follow-ups and comparisons, legacy image payload exclusions from model prompts, owner-scoped attachment metadata, and fail-closed error handling without tool or provider invocation.

### 7. Integrated backend regression tests
Executed the full integrated offline regression suite:
```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i7_model_reliability.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_i6_workflow_offline.py --tb=short
```
Result: **215 passed, 0 failed, 8 warnings in 20.91s**.
- Previous baseline (R8.1): 213 passed.
- New baseline (R8.2): **215 passed** (+2 new backend tests).
- Warnings: 1 config warning (`asyncio_mode`) and 7 unregistered `asyncio` mark warnings under disabled autoload.

### 8. Confirmed defects and fixes
- All 5 defects identified by Codex were independently verified as resolved:
  1. Historical image turns and legacy `images` arrays are excluded from subsequent reasoning contexts.
  2. Follow-up queries referencing unavailable previous images receive `IMAGE_REATTACH_REQUIRED` before model invocation or tool dispatch.
  3. Image attachments bypass the text approval shortcut and route through read-only multipart SSE.
  4. Draft creation rechecks committed model selection and freshness before dispatch.
  5. Object URL previews are promptly revoked on switch, reset, removal, and unmount; demo downloads abort safely on conversation change.
- No new regressions or defects were detected during Antigravity's verification pass.

### 9. Preview cleanup findings
- Object URLs created via `URL.createObjectURL` are tracked and revoked synchronously on:
  - Attachment removal in `ChatInput.tsx`.
  - Conversation selection change in `ChatView.tsx`.
  - Conversation deletion or reset.
  - Active session unmount / operator logout.
- No memory leaks or premature revocations observed during streaming or valid submission.

### 10. Reattachment and context-isolation findings
- `requires_reattachment()` intercepts requests with explicit image references (`"this image"`, `"previous picture"`, `"what color was it"`, etc.) when prior turns contained an image attachment, returning `IMAGE_REATTACH_MESSAGE` immediately.
- Neither Ollama model providers nor tool dispatchers are invoked on reattachment-required follow-ups.
- `request_scoped_history()` ensures that only eligible text history reaches the chat provider on follow-up text prompts.
- Dual-image comparison requests (e.g., asking to compare a new image with an earlier attachment) are blocked with a 409 conflict, preserving single-image request-scoped boundaries.

### 11. Live inference results or blockers
- **Status**: **BLOCKED / NOT VERIFIED** for live browser/backend execution.
- **Port Preflight findings**:
  - `127.0.0.1:11434`: Listening (PID 35408, `ollama.exe`).
  - `127.0.0.1:8001`, `127.0.0.1:5174`, `8000`, `5173`: All confirmed **FREE**.
  - Local model tags: `qwen2.5:7b` (tools) and `llava:7b` (vision) available.
- **Blocker Rationale**:
  - Independent native process supervision (R5.5) remains pending independent security review.
  - Operator live execution authorization for background workbench services and browser profiles was not granted.
  - Under fail-closed governance, live services were not started.

### 12. Security gates preserved
- R5.5 native process supervisor pending independent review.
- R6.4 durable provenance-key protection remains blocked.
- Protected document RAG ingestion, artifact publication, and production code execution remain in fail-closed development state.
- Unauthenticated image-derived assistant responses remain withheld on conversation reload.

### 13. R8.2 status
- **R8.2 Offline Verification**: **PASS**.
- **Live Multimodal Acceptance**: **BLOCKED**.
- **Production Status**: Strictly **NO-GO**.

### 14. R8.3 Codex handoff
- **Target Capability**: Bounded request-scoped UTF-8 text preview.
- **Requirements**:
  - Plain-text transient decoding under strict size limit (e.g. 64 KiB).
  - No background indexing, durable storage, or tool dispatch.
  - Maintain protected document and RAG ingestion gates in `backend/api/documents.py`.
