# R8.7 — Cross-feature workbench integration

**Scoped offline backend PASS. Overall R8.7 PARTIAL pending Antigravity frontend verification. Live acceptance BLOCKED. Production NO-GO.** Work started on `main` at `43d72b8036b27b7926a74d08af260f865259669b` with existing uncommitted work.

Antigravity’s [R8.6 verification appendix](RELEASE_R8_6_CSV_PREVIEW_REPORT.md#antigravity-independent-verification--r86) records clean TypeScript checks, 95 frontend passes, 59 focused backend passes and 278 broad backend passes with exit 0. No R8.6 repair was reported. Live inference and browser acceptance remained blocked. Earlier R8.2, R8.4 and R8.5 evidence covers image lifecycle, TXT line ranges and cookie sign-in/readiness in process.

## Confirmed defects and changes

| Defect | Correction |
|---|---|
| Stopping a draft during conversation creation discarded the old response but kept the Send lock until the server returned. The create request could also keep running through logout. | `frontend/src/components/chat/ChatView.tsx` owns an abort controller for each pending create and passes its signal through `frontend/src/api/chat.ts`. Stop, navigation and unmount abort the pending request. An old completion cannot adopt its session ID or release the lock for a newer request. A create that completed server-side may leave an empty owned conversation. |
| Plain chat could dispatch on the original model if the selection changed while a draft acquired its server ID. Attachment paths already rechecked the selection. | The same current text-model and readiness check now runs for plain drafts after the asynchronous create. Image requests still also require the pinned vision selection and readiness. No model request fires on a stale selection. |
| The reattachment check treated any new upload as if it could satisfy a reference to a different prior attachment type. A prior picture could also cause an ambiguous current TXT question to be rejected. | `backend/multimodal/context.py` and `backend/api/chat.py` pass the current attachment type into the existing checks. Naming an earlier unavailable format returns the existing reattachment error before inference. A generic reference in a question with a new upload can refer to that upload. Explicit earlier-file comparisons still require reattachment. |

## Integration contracts

| Path | Verified contract |
|---|---|
| Ordinary chat and approved tools | Authenticated text readiness, normal tool and approval checks, verified numeric continuity and ordinary SSE terminal behavior. |
| Image | `/api/chat/multimodal` uses fresh vision and text readiness. Only the current image reaches vision. A mismatched explicit reference returns 409 before either provider stage. |
| TXT and CSV | The existing preview and question endpoints retain `line_start`/`line_end` and `row_start`/`row_end` respectively. Questions require owned history, the authenticated local chat-only profile and fresh text readiness. They send current scoped content without tool or protected retrieval paths. |
| History and lifecycle | Server history is owner-scoped. Attachment bytes remain request-scoped, and descriptors offer reattachment guidance. The existing sidebar retains its server-confirmed deletion and separate list/navigation generation checks. New tests cover replacing a pending TXT preview with CSV then image, stopping and replacing a pending CSV draft, logout during creation and model changes. |

`tests/backend/test_r87_attachment_transitions_offline.py` exercises mixed and same-format attachments through the production ASGI routes. Refused cross-format references make no provider call and leave history unchanged. Accepted replacements use only the submitted content. The integrated tests cover readiness expiry, provider identity and assistant-role validation, cancellation, failure without a false `done`, tool isolation, attachment persistence limits and protected document/storage/artifact gates. R5.5, R6.4 and the disabled launcher remain in force.

## Offline verification

Run from the repository root. Both commands exited 0 with one existing pytest `asyncio_mode` configuration warning under disabled plugin autoload.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_r87_attachment_transitions_offline.py --tb=short
python -m pytest -q tests/backend/test_r87_attachment_transitions_offline.py tests/backend/test_r86_csv_preview_offline.py tests/backend/test_r83_text_file_offline.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_a2_provider_offline.py tests/backend/test_r12_readiness_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r67_approval_stream_offline.py tests/backend/test_r41_shared_storage_gate.py --tb=short
```

- Focused **7 passed** in 3.45s. Integrated subset **171 passed** in 21.99s. Both had zero failures or skips.
- `git diff --check` passed with line-ending notices on existing changes.
- Codex did not run TypeScript, frontend suites, live browser, live Ollama or native process tests. No live application defect is established by the offline results.

## Antigravity handoff

Rerun the two commands above. For the broad suite, use the exact backend command in section 7 of the R8.6 appendix with `tests/backend/test_r87_attachment_transitions_offline.py` added before `--tb=short`. Record exit status for each run.

From `frontend` with reviewed locally installed tools:

```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a61-chat-attachment.test.cjs .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a1-capability-render.test.cjs .\tests\a41-history-sidebar.test.cjs .\tests\a41-history-render.test.cjs
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```

For separately authorized live acceptance, use the [R8.5 launch and stop conditions](RELEASE_R8_5_LOCAL_RUNTIME_ACCEPTANCE_REPORT.md#antigravity-handoff) and [R8.6 synthetic CSV fixture](RELEASE_R8_6_CSV_PREVIEW_REPORT.md#antigravity-handoff). Recheck port availability and exact ownership on loopback 8001 and 5174 and verify the approved Ollama listener on 11434. The prior port observations were point-in-time only. Use disposable credentials and state, an approved runner with independent containment and deadline, and bounded evidence collection. On shutdown verify owned descendants and listeners are gone and pre-existing processes survive.

Within that run, sign in, check text and vision readiness, and verify plain streaming, approved arithmetic, image analysis, TXT line ranges and CSV row questions in the same conversation. Replace TXT with CSV, then with image, and verify only the current attachment submits. Change models during creation, stop a draft and immediately send a different message, then switch conversations and sign out during a preview. A question explicitly naming an unavailable earlier format should return 409 with no provider event. Reload owned history and verify attachment metadata and correct reattachment guidance. Exercise rename and deletion without stale updates. These cases remain unverified live pending authorization and containment.

**Recommended next action** — Run the Antigravity end-to-end acceptance matrix under separately approved local execution and verified process containment, starting with the offline checks above.

---

## Antigravity independent verification — R8.7

Executed 2026-11-16 on the same checkout without commit, push, pull, merge or branch switch.

### 1. Branch and HEAD

- Branch: `main`
- HEAD: `43d72b8036b27b7926a74d08af260f865259669b`
- Working tree: 44 modified, 18 untracked files (reports, new modules, new test files).

### 2. Files changed by R8.7

Confirmed via `git status` and `git diff`:

| File | Change |
|---|---|
| `frontend/src/components/chat/ChatView.tsx` | `creating` ref changed from boolean to `AbortController`. New `draftRevision` state. `createSession` call now receives `creation.signal`. Post-create validates current model selection and readiness against snapshot — aborts on mismatch. Unmount/stop/navigation abort pending create. Late completion cannot adopt session ID. `modelSelection` ref tracks `selectedModel`, `selectedVisionModel`, `capabilities`. `ChatInput` key uses `draftRevision` for forced reset. Demo preload uses lifetime abort controller. |
| `frontend/src/api/chat.ts` | `createSession` accepts optional `AbortSignal`. `streamChatTextFile` passes text-file line or CSV row range correctly. |
| `backend/multimodal/context.py` | `requires_text_reattachment` now accepts `has_attachment` parameter and passes current attachment type to distinguish cross-format references. `_CSV_REFERENCE`, `_EARLIER_CSV_REFERENCE` patterns distinguish explicit earlier-format references. |
| `backend/api/chat.py` | Reattachment checks pass `has_attachment=True` when a current attachment is present, enabling cross-type discrimination. |
| `frontend/tests/a22-chatview-handlers.test.cjs` | +2 tests: stopped draft releases Send; late create cannot dispatch old attachment. |
| `frontend/tests/a61-chat-attachment.test.cjs` | +1 test: replacing TXT with CSV then image never sends old file. |
| `frontend/tests/a41-history-sidebar.test.cjs` | +1 test: explicit history selection survives background list refresh. |
| `tests/backend/test_r87_attachment_transitions_offline.py` | New. 7 parametrized tests covering cross-format reattachment (CSV→image, CSV→TXT, TXT→CSV, image→CSV, image→TXT, CSV→image generic, CSV→CSV same-format). Verifies 409 with correct error code, no provider call, and unchanged history for refused cross-format references. |

### 3. TypeScript results

From `frontend/`:
```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
```
Result: **PASS (0 errors each)**.

### 4. Targeted frontend test results

```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a61-chat-attachment.test.cjs .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a1-capability-render.test.cjs .\tests\a41-history-sidebar.test.cjs .\tests\a41-history-render.test.cjs
```
Result: **80 passed, 0 failed, 0 skipped** in 889.5 ms, `EXIT_CODE:0`.

Verified R8.7-specific behaviors:
1. Stopped draft releases Send immediately — `creating.current?.abort()` sets `creating.current = null` and `setIsStreaming(false)`.
2. Late draft-creation response cannot activate obsolete conversation — `creation.signal.aborted` check and `selectionVersion` guard reject stale completions.
3. Late response cannot submit attachment from cancelled draft — `creation.signal.aborted` returns before stream dispatch.
4. Switching text models invalidates pending draft — `modelSelection.current` comparison against `selectedModel` after async create detects mismatch.
5. `draftRevision` state forces `ChatInput` key reset, clearing stale attachment/preview state.
6. Replacing TXT with CSV then image never sends either old file (test 75).
7. Background history refresh does not cancel intentional navigation (test 65).
8. CSV submission lock, duplicate suppression, logout cleanup all retained.
9. Image and text-file reattachment continue working across all prior tests.

### 5. Full frontend regression total

```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```
Result: **99 passed, 0 failed, 0 skipped** in 1015.0 ms, `EXIT_CODE:0`.
- Previous baseline (R8.6): 95 passed.
- New baseline (R8.7): **99 passed** (+4 new cross-feature tests).
- All prior chat, image, text-preview, line-range, CSV, history, settings and proxy tests continue passing.

### 6. Focused backend tests

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_r87_attachment_transitions_offline.py --tb=short
```
Result: **7 passed, 0 failed, 1 warning in 2.33s**, `EXIT_CODE:0`.
- Matches Codex R8.7 report exactly (7 focused).
- Warning: 1 `asyncio_mode` config under disabled autoload.

### 7. Broad integrated backend tests

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i7_model_reliability.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_i6_workflow_offline.py tests/backend/test_r83_text_file_offline.py tests/backend/test_r86_csv_preview_offline.py tests/backend/test_r87_attachment_transitions_offline.py --tb=short
```
Result: **285 passed, 0 failed, 8 warnings in 29.05s**, `EXIT_CODE:0`.
- Previous broad baseline (R8.6): 278 passed.
- New broad baseline (R8.7): **285 passed** (+7 new attachment-transition tests).
- All prior provider, readiness, conversation, numeric receipt, planning, tool, authorization, document gate, artifact boundary, storage, model reliability, multimodal, workflow, text-file, and CSV tests remain passing.

### 8. Draft-creation race verification

Code inspection of `ChatView.tsx`:

1. `creating` ref upgraded from `boolean` to `AbortController | null`. Each send creates a fresh `AbortController`.
2. `createSession(creation.signal)` propagates abort signal through `chat.ts` to the fetch call.
3. Post-create guard: `if (creation.signal.aborted || selectionVersion.current !== version) return;` — prevents stale session adoption.
4. Stop/navigation: `creating.current?.abort(); creating.current = null; setIsStreaming(false);` — immediately releases Send.
5. Unmount cleanup: same abort pattern in `useEffect` return.
6. Error handler: `if (!creation.signal.aborted && selectionVersion.current === version)` — only shows error toast for the active request.

### 9. Model-switch race verification

1. `modelSelection` ref tracks `{ selectedModel, selectedVisionModel, capabilities }`, updated via `useEffect`.
2. After async `createSession`, the handler compares `current.selectedModel !== selectedModel` — returns without dispatch on mismatch.
3. For image requests, additionally checks `current.selectedVisionModel !== selectedVisionModel`.
4. For text-file requests, verifies `current.capabilities?.text_file_understanding === true`.
5. `chatDisabled` re-evaluated against current capabilities for both text and vision models.

### 10. Attachment reattachment findings

Code inspection of `backend/multimodal/context.py` and `test_r87_attachment_transitions_offline.py`:

| Scenario | Expected | Verified |
|---|---|---|
| CSV→image: "What is in the CSV?" | 409 `TEXT_FILE_REATTACH_REQUIRED` | ✅ No provider call, history unchanged |
| CSV→TXT: "Summarize the CSV." | 409 `TEXT_FILE_REATTACH_REQUIRED` | ✅ No provider call, history unchanged |
| TXT→CSV: "Read the text file." | 409 `TEXT_FILE_REATTACH_REQUIRED` | ✅ No provider call, history unchanged |
| Image→CSV: "Describe the screenshot." | 409 `IMAGE_REATTACH_REQUIRED` | ✅ No provider call, history unchanged |
| Image→TXT: "What color is it?" | 200 (generic reference, current TXT accepted) | ✅ Provider called, no prior image data in request |
| CSV→image: "What is in the table?" | 200 (generic reference, current image accepted) | ✅ Provider called, no prior CSV data in request |
| CSV→CSV: "What is in the CSV?" | 200 (same format, current CSV accepted) | ✅ Provider called, prior "Describe" content absent |

Cross-format isolation: accepted replacements contain only the submitted content. No prior attachment bytes, questions, or model context leak across workflows.

### 11. Confirmed defects fixed

**None discovered during R8.7 verification.** All changes passed without repair. All prior fixtures remain compatible.

### 12. Live execution results or blockers

**Status: BLOCKED / NOT EXECUTED** for all live cases.

**Blocker**: Operator live execution authorization for background workbench services and browser profiles was not granted. R5.5 independent native process supervisor remains pending security review. Under fail-closed governance, live services were not started.

### 13. Port and model preflight

| Port | Status | Owner |
|---|---|---|
| 8000 | FREE | — |
| 8001 | FREE | — |
| 5173 | FREE | — |
| 5174 | FREE | — |
| 11434 | LISTENING | PID 35408 (ollama.exe) |

Ollama models: `qwen2.5:7b`, `llava:7b`, `nomic-embed-text:latest`.

### 14. Security controls preserved

- Disabled launcher and `scripts/dev.ps1` untouched.
- R5.5 process supervisor pending.
- R6.4 durable provenance-key protection blocked.
- `socket.socket` monkeypatched to `AssertionError("socket creation forbidden")` in offline tests.
- No packages installed. No `.env` in repository root.
- No telemetry references in repository code.
- `git diff --check`: EXIT_CODE:0 (LF→CRLF warnings only).
- `request_tools_enabled.set(False)` enforced for all text-file and CSV analysis paths.
- Protected RAG, artifact and document gates remain active.

### 15. R8.7 status

- **Offline Verification**: **PASS** (99 frontend, 285 backend, 0 failures, TypeScript clean).
- **R8.7 Focused Tests**: **PASS** (7 passed, matching Codex report).
- **Draft-Creation Race**: **PASS** (code inspection + 2 new frontend tests).
- **Model-Switch Race**: **PASS** (code inspection + post-create model validation logic).
- **Cross-Format Reattachment**: **PASS** (7 parametrized backend tests + 1 frontend attachment-replacement test).
- **Live Application Acceptance**: **BLOCKED**.
- **Production Status**: Strictly **NO-GO**.
- **`git diff --check`**: PASS (EXIT_CODE:0).

### 16. Handoff to Codex R8.8

- Offline regression baselines: **99 frontend**, **285 broad backend**.
- Live acceptance remains BLOCKED pending operator authorization and R5.5 process supervisor resolution.
- R6.4 durable provenance-key protection remains blocked.
- Next milestone candidates: live end-to-end acceptance execution under approved containment, multi-attachment metadata refinement, structured response formatting, or CSV export functionality.
