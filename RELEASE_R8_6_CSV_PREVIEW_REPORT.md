# R8.6 — Bounded CSV preview and request-scoped analysis

**Offline backend scope PASS. R8.6 overall PARTIAL pending Antigravity frontend verification. Live Ollama remains unverified. Production NO-GO.**

Started on `main` at `43d72b8036b27b7926a74d08af260f865259669b` with existing uncommitted work. The Antigravity appendix in [R8.5](RELEASE_R8_5_LOCAL_RUNTIME_ACCEPTANCE_REPORT.md#antigravity-independent-verification--r85) records clean TypeScript checks, 91 frontend, 139 selected backend and 252 broad backend passes. R8.4 also records 91 frontend and 252 broad backend passes. R8.5 live acceptance remained blocked by separate authorization and process-containment requirements.

## Behavior and limits

The existing chat composer accepts one `.txt` or `.csv` attachment, or an image on the existing image path. CSV gets a numbered table with validated headers, plain-text cells, inferred visible-column types and a locally computed total data-row count. Larger tables show the first 12 rows and a shortened-preview notice. Users can preview a new inclusive range. CSV questions require a valid explicit range and a preview for that range in the composer. Selecting, replacing or removing attachments uses the existing abort handling. Conversation changes and logout discard in-flight state.

| Limit | CSV contract |
|---|---|
| Multipart and UTF-8 | At most 10,240 bytes per form, 2,048 bytes per file, and 1,200 decoded characters. One file, approved form fields, strict UTF-8 with optional BOM and shared binary/control-character rejection. The `.txt` byte limit stays 2 KiB. |
| Shape | Comma delimiter, first record supplies 1–8 unique non-empty single-line headers, followed by 1–32 consistently wide data rows. Headers are trimmed for display and compared case-insensitively. Each header and data cell is limited to 128 characters. Quoted commas, escaped quotes, quoted newlines, LF, CRLF and empty cells are supported. |
| File type | Safe `.csv` name. Accepts `text/csv`, `text/plain`, `application/octet-stream` or the common browser `.csv` label `application/vnd.ms-excel`, with UTF-8 charset parameters only. No workbook reader runs on this path. |
| Row selection and prompt | Data rows are numbered from 1, excluding the header. A preview or question can select up to 12 consecutive rows. A question is at most 1,024 UTF-8 bytes. The serialized question and selected-table JSON together are capped at 4,096 UTF-8 bytes. The fixed system message is separate and the existing 384-output-token and 4,096-response-character ceilings apply. |
| Cell interpretation | Cells retain their original strings. Column labels indicate Number, Boolean, Text or Empty for the displayed rows. Formula prefixes, HTML-like strings and URLs remain inert. No formula calculation, script execution, coercion, link fetching or SQL is provided. |

## Integration and security

| Files | Change |
|---|---|
| `backend/multimodal/spreadsheet.py`, `backend/multimodal/csv_file.py`, `backend/multimodal/text_file.py` | Reuse the standard-library CSV reader and bounded UTF-8 validator. Add CSV-specific structure, row and context limits and allow a safe, typed CSV attachment descriptor. |
| `backend/api/chat.py`, `backend/security/request_limits.py`, `backend/multimodal/context.py` | Add `/api/chat/csv-preview` and `/api/chat/csv-file` under the shared bounded multipart path. Reuse the guarded text-file inference path, history projection and reattachment handling. |
| `frontend/src/api/chat.ts`, `frontend/src/components/chat/ChatInput.tsx`, `ChatView.tsx`, `MessageItem.tsx` | Add table preview, row controls, CSV multipart routing, history labeling and reattachment guidance. Preserve same-conversation submission locks and text-readiness checks. |
| `tests/backend/test_r86_csv_preview_offline.py` | Add parser and API cases, exact provider payload assertions, authentication and owner isolation, reattachment, cancellation, context and readiness limits, and suppressed completion after invalid tool markup. |
| `frontend/tests/a61-chat-attachment.test.cjs`, `a22-chatview-handlers.test.cjs`, `a2-stream-client.test.cjs` | Add inert rendering and empty-cell assertions, readiness and range gating, validation, removal and stale-preview tests, duplicate submission and logout tests, and CSV form-field checks. |

Preview requires authenticated `VIEW_DATA` permission and verifies ownership when given a conversation ID. Analysis additionally requires an owned conversation, the authenticated local chat-only development profile, fresh text-model readiness and matching local Ollama identity. The server reparses and validates the complete upload on each request. Only selected rows, the validated headers and the parsed selected-row count accompany the question. Parsed preview counts and column names are available without model inference. Model answers are plain text and may contain analytical errors. They carry no verified calculation or citation claim.

Analysis uses a direct model stream with no tools, SQL, retrieval, indexing or artifact publication. Invalid tool output, incomplete inference, identity mismatch and cancellation cannot emit successful completion. The app persists a fixed user-turn marker and only the attachment ID, filename, MIME type and size after successful inference. CSV bytes, rows, question and answer are transient. A follow-up that refers to unavailable table data prompts reattachment. R5.5 process containment, R6.4 provenance protection and the protected storage, RAG and artifact gates stay in force.

## Offline verification

Both commands returned exit 0 from the repository root. Each produced one existing `asyncio_mode` configuration warning with pytest plugin autoload disabled.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_r86_csv_preview_offline.py tests/backend/test_r83_text_file_offline.py --tb=short
python -m pytest -q tests/backend/test_r86_csv_preview_offline.py tests/backend/test_r83_text_file_offline.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_a2_provider_offline.py tests/backend/test_r12_readiness_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r67_approval_stream_offline.py tests/backend/test_r41_shared_storage_gate.py --tb=short
```

| Check | Result |
|---|---|
| Focused CSV and UTF-8 line-range tests | 59 passed, 0 failed or skipped, 8.93s. |
| Integrated backend subset | 164 passed, 0 failed or skipped, 19.81s. Includes text and image chat, calculator and unit conversion continuity, history, readiness and provider validation, approvals, and protected document, storage and artifact restrictions. |
| Static | `git diff --check` passed. |
| TypeScript, frontend and live runtime | Frontend coverage added and source reviewed. No frontend test, browser, live service or inference process was started in Codex. |

## Antigravity handoff

Run the two backend commands above in the reviewed offline environment. For baseline-wide comparison, rerun the [R8.5 broad backend command](RELEASE_R8_5_LOCAL_RUNTIME_ACCEPTANCE_REPORT.md#antigravity-independent-verification--r85) with `tests/backend/test_r86_csv_preview_offline.py` included and capture exit status. The 164-test run is a selected integration subset.

From `frontend`, use the installed dependencies and reviewed local Node:

```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a61-chat-attachment.test.cjs .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a41-history-render.test.cjs
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```

For separately authorized live acceptance, use the [R8.5 launch and stop conditions](RELEASE_R8_5_LOCAL_RUNTIME_ACCEPTANCE_REPORT.md#antigravity-handoff) and recheck ownership and availability of loopback 8001, 5174 and the approved Ollama listener on 11434. Keep the disabled launcher out of the run. Use disposable state and a test operator, with independent process containment and shutdown evidence.

Save this synthetic fixture as UTF-8 `stock.csv`:

```csv
Item,Units,Notes,Active
Café,12,"blue, small",true
Washers,3,=1+1,false
Valves,7,,true
```

1. Preview with text inference unchecked. Verify four headers, three numbered data rows, numeric `Units`, Boolean `Active`, literal `=1+1` and the empty Notes cell.
2. Select and preview rows 2–3. Verify the first row disappears from this preview and both selected row numbers remain correct. Check that invalid bounds and an unpreviewed range cannot submit. Verify invalid UTF-8, duplicate headers and inconsistent widths surface a clear error.
3. After fresh text readiness, ask which items are in the selected rows. The expected items are Washers and Valves. Exact provider input scope is verified by the offline payload test. Live verification of that scope requires approved observation of the synthetic provider request.
4. Cancel an in-flight question, replace/remove an attachment, switch conversations, and sign out during a preview. Check that late data stays discarded. After a completed turn, reload the conversation and confirm descriptor-only history. Ask about the earlier table and verify reattachment guidance. Check that protected ingestion, retrieval and publication remain gated.

**Recommended next milestone** — Record Antigravity frontend acceptance for the CSV workflow and add the CSV cases to the separately authorized R8.5 live acceptance matrix.

---

## Antigravity independent verification — R8.6

Executed 2026-11-16 on the same checkout without commit, push, pull, merge or branch switch.

### 1. Branch and HEAD

- Branch: `main`
- HEAD: `43d72b8036b27b7926a74d08af260f865259669b`
- Working tree: 44 modified, 17 untracked files (reports, new modules, new test files).

### 2. Files modified by R8.6

Confirmed via `git status` and `git diff`:

| File | Change |
|---|---|
| `backend/multimodal/csv_file.py` | New. Bounded CSV parser using stdlib `csv.reader(strict=True)`. `parse_csv_file` validates MIME, UTF-8, headers (1–8 unique, ≤128 chars, single-line), rows (1–32, consistent width, ≤128 chars/cell). `select_csv_rows` enforces 1-based inclusive range ≤12 rows. `csv_column_types` labels Number/Boolean/Text/Empty without coercion. |
| `backend/multimodal/spreadsheet.py` | Existing `csv_rows` reused by `csv_file.py`. No structural change in R8.6. |
| `backend/multimodal/context.py` | Added `_CSV_REFERENCE`, `_EARLIER_CSV_REFERENCE`, `_IMMEDIATE_CSV_FOLLOWUP` patterns. `requires_text_reattachment` detects CSV follow-ups. `TEXT_REATTACH_MESSAGE` updated to include CSV. `_is_csv_turn` checks `text_attachment_v1.mime_type == 'text/csv'`. |
| `backend/api/chat.py` | Added `POST /api/chat/csv-preview` and `POST /api/chat/csv-file` routes. Shared `_text_file_form` and `_read_text_file` accept `csv_file=True`. `_chat_attachment_file` selects rows, serializes `untrusted_table` JSON, enforces 4096-byte context cap, appends CSV-specific system instructions, persists descriptor-only history with `mime_type: text/csv`. |
| `backend/security/request_limits.py` | Shared multipart validation covers CSV form fields. |
| `frontend/src/api/chat.ts` | Added `previewCsvFile` and CSV routing in `streamChatTextFile`. CSV uses `/api/chat/csv-preview` and `/api/chat/csv-file` with `row_start`/`row_end` form fields. |
| `frontend/src/components/chat/ChatInput.tsx` | CSV file selection triggers table preview. Row range controls (start/end inputs). Validates range before allowing submission. Requires fresh text readiness. |
| `frontend/src/components/chat/ChatView.tsx` | CSV submission lock, logout cleanup, late-answer drop for CSV turns. |
| `frontend/src/components/chat/MessageItem.tsx` | History renders CSV attachment descriptor label. |
| `frontend/tests/a61-chat-attachment.test.cjs` | +2 tests: CSV inert cell rendering with row/readiness gating; CSV error/removal/unmount cleanup. |
| `frontend/tests/a2-stream-client.test.cjs` | +1 test: CSV endpoints and inclusive row pair submission. |
| `frontend/tests/a22-chatview-handlers.test.cjs` | +1 test: CSV duplicate submission lock and late-answer drop on logout. |
| `tests/backend/test_r86_csv_preview_offline.py` | New. 26 tests covering parser validation, ranges, provider payload assertions, auth/ownership, reattachment, cancellation, context limits, readiness, provider identity, tool-markup rejection. |

### 3. TypeScript results

From `frontend/`:
```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
```
Result: **PASS (0 errors each)**.

### 4. Targeted frontend test results

```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a61-chat-attachment.test.cjs .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a41-history-render.test.cjs
```
Result: **58 passed, 0 failed, 0 skipped** in 577.6 ms, `EXIT_CODE:0`.

Verified CSV-specific behaviors:
- `<script>inert</script>` rendered as text in `<td>`, no `<script>` or `dangerouslySetInnerHTML` in tree.
- `=1+1` rendered as literal text, no formula evaluation.
- Empty cells preserved as empty strings in table.
- Row range validation blocks submission without valid preview.
- Fresh text readiness required before CSV question send.
- Duplicate submissions suppressed by existing lock.
- Logout drops late CSV answer.
- CSV preview and analysis route to `/api/chat/csv-preview` and `/api/chat/csv-file`.
- `row_start`/`row_end` form fields sent; `line_start`/`line_end` absent from CSV requests.
- Error, removal, and unmount cleanup discard stale CSV state.

### 5. Full frontend regression total

```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```
Result: **95 passed, 0 failed, 0 skipped** in 950.1 ms, `EXIT_CODE:0`.
- Previous baseline (R8.5): 91 passed.
- New baseline (R8.6): **95 passed** (+4 new CSV frontend tests).
- All prior chat, image, text-preview, line-range, history, settings, and proxy tests continue passing.

### 6. Focused backend count

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_r86_csv_preview_offline.py tests/backend/test_r83_text_file_offline.py --tb=short
```
Result: **59 passed, 0 failed, 1 warning in 6.25s**, `EXIT_CODE:0`.
- Matches Codex R8.6 report exactly (59 focused).
- Warning: 1 `asyncio_mode` config under disabled autoload.

### 7. Broad backend count

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i7_model_reliability.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_i6_workflow_offline.py tests/backend/test_r83_text_file_offline.py tests/backend/test_r86_csv_preview_offline.py --tb=short
```
Result: **278 passed, 0 failed, 8 warnings in 26.46s**, `EXIT_CODE:0`.
- Previous broad baseline (R8.5): 252 passed.
- New broad baseline (R8.6): **278 passed** (+26 new CSV tests).
- All prior provider, readiness, conversation, numeric receipt, planning, tool, authorization, document gate, artifact boundary, storage, model reliability, multimodal, workflow, and text-file tests remain passing.

### 8. CSV parsing and row-selection findings

Code inspection of `backend/multimodal/csv_file.py`:

1. **Parser**: Uses `csv.reader(strict=True)` via `csv_rows()` which wraps `io.StringIO(text, newline='')` — correctly handles CRLF, LF, CR, and quoted newlines.
2. **UTF-8 validation**: Delegates to `decode_text_file` which enforces ≤2048 bytes, ≤1200 chars, valid UTF-8-SIG, no binary headers, no control chars.
3. **Header validation**: 1–8 headers, unique (case-insensitive), non-empty after trim, ≤128 chars, single-line (rejects `\r\n\t` in raw header).
4. **Row validation**: Consistent width (must match header count), ≤32 rows, ≤128 chars per cell.
5. **Row selection**: 1-based inclusive, regex `[1-9][0-9]?` (1–99), `start ≤ end ≤ len(rows)`, `end - start < 12` (max 12 rows).
6. **Column types**: `Number` (regex `-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?`), `Boolean` (`true`/`false` case-insensitive), `Empty` (all blank), `Text` (fallback). No coercion or evaluation.
7. **MIME acceptance**: `text/csv`, `text/plain`, `application/octet-stream`, `application/vnd.ms-excel` — all with UTF-8 charset only.
8. **Context limit**: Provider payload JSON (`question` + `untrusted_table`) capped at 4096 UTF-8 bytes.

Test coverage verified through `test_r86_csv_preview_offline.py`:
- 21 parametrized rejection cases (empty, missing headers, duplicate headers, excess columns/rows/cells, invalid UTF-8, binary, traversal names, wrong MIME, wrong charset, unterminated quotes).
- Range boundary tests (missing end, zero start, reversed, exceeds row count, exceeds 12-row limit).
- Multipart size limit enforcement.
- Duplicate form field rejection.
- Exact provider payload assertion (only selected rows + headers in `untrusted_table`).

### 9. Persistence and security results

| Control | Finding |
|---|---|
| No formula execution | `=2+3`, `+`, `@name` stored as literal strings. `csv_column_types` classifies without evaluation. Provider instructions say "Do not evaluate formula-like cells." |
| No HTML/JS execution | `<script>inert</script>` rendered as text in `<td>`. Frontend test asserts no `<script>` element or `dangerouslySetInnerHTML` in rendered tree. |
| No tool dispatch | `request_tools_enabled.set(False)` before provider call. Test asserts `not getattr(request, 'tools', None)`. `<tool_call>` markup in model output triggers `INVALID_TOOL_REQUEST` error with no history persistence. |
| No system instruction injection | CSV cells are in `untrusted_table` JSON value, not in the system message. System message is a fixed string. |
| No SQL/Python execution | No database query or code execution path for CSV data. |
| Only selected rows to provider | Test asserts exact `untrusted_table` contains only selected row data, not full CSV. |
| Transient content | `memory.json` verified to not contain `private-row`, `next`, question text, or provider answer. Only attachment descriptor persisted (`attachment_id`, `filename`, `mime_type`, `size_bytes`). |
| Cross-conversation isolation | Anonymous requests return 401. Other-user requests return 404. History query for non-owned session returns empty. |
| Cleanup | Cancellation test confirms provider generator closed, no history persistence. Unmount aborts in-flight preview. Logout drops late answer. |
| Error containment | `TextFileError` produces structured `X-Error-Code` headers. No raw stack traces in responses. |
| Protected gates | RAG retrieval, tool dispatch, artifact publication remain gated. `engine._retrieve_context` and `engine._dispatch_tool` rigged to `pytest.fail` — never invoked. |

### 10. Confirmed defects and fixes

**None.** All R8.6 changes passed without requiring repair. All prior fixtures (including the R8.4 `model_outputs.json` fix) remain compatible.

### 11. Live inference status

**BLOCKED / NOT EXECUTED.**

Operator live execution authorization for background workbench services and browser profiles was not granted. R5.5 independent native process supervisor remains pending security review. Under fail-closed governance, live services were not started.

### 12. Port and model preflight

| Port | Status | Owner |
|---|---|---|
| 8000 | FREE | — |
| 8001 | FREE | — |
| 5173 | FREE | — |
| 5174 | FREE | — |
| 11434 | LISTENING | PID 35408 (ollama.exe) |

Ollama `/api/tags` models:
- `qwen2.5:7b` — 7.6B Q4_K_M, capabilities: completion, tools.
- `llava:7b` — 7B Q4_0, capabilities: completion, vision.
- `nomic-embed-text:latest` — 137M F16, capabilities: embedding.

### 13. Security restrictions preserved

- Disabled launcher and `scripts/dev.ps1` untouched.
- R5.5 process supervisor pending.
- R6.4 durable provenance-key protection blocked.
- `socket.socket` monkeypatched to `AssertionError("socket creation forbidden")` in offline tests.
- No packages installed. No `.env` in repository root.
- No telemetry references in repository code (forensically confirmed in R8.4).
- `git diff --check`: EXIT_CODE:0 (LF→CRLF warnings only).

### 14. R8.6 status

- **Offline Verification**: **PASS** (95 frontend, 278 backend, 0 failures, TypeScript clean).
- **R8.6 Focused Tests**: **PASS** (59 passed, matching Codex report).
- **CSV Parser & Security**: **PASS** (code inspection + 26 backend + 4 frontend CSV-specific tests).
- **Persistence Safety**: **PASS** (descriptor-only, no raw cells or question in durable store).
- **Live Application Acceptance**: **BLOCKED**.
- **Production Status**: Strictly **NO-GO**.
- **`git diff --check`**: PASS (EXIT_CODE:0).

### 15. R8.7 Codex handoff

- Offline regression baselines: **95 frontend**, **278 broad backend**.
- Live acceptance remains BLOCKED pending operator authorization and R5.5 process supervisor resolution.
- R6.4 durable provenance-key protection remains blocked.
- Synthetic CSV live test fixture (`stock.csv` from report) ready for separately authorized acceptance run.
- Next milestone candidates: multi-file attachment management, structured JSON preview, additional cell-type inference, or CSV export from analysis results.
