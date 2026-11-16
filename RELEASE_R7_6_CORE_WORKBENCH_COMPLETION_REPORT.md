# R7.6 — Core Workbench Functional Completion

**R7.6 PARTIAL. The integrated offline backend suite passed. Production remains NO-GO.**

Started on `main` at `43d72b8036b27b7926a74d08af260f865259669b` with the R7.2–R7.5 working-tree changes and reports present.

## Latest local evidence

Antigravity's R7.5 report records both no-emit TypeScript checks passing, **66 passing frontend tests across 14 files**, and **150 passing backend tests**. Its preflight found port 8001 free, unrelated Automata processes on IPv4 port 8000 and IPv6 port 5173, and a pre-existing Ollama listener on IPv4 port 11434. Listener ownership and availability were observations at that preflight. Live startup, browser integration and Ollama inference were blocked by missing operation approval and independent process cleanup controls. Preserve the unrelated services and recheck availability in the approved run.

The R7.3 protocol fixtures and R7.4 dev proxy regressions were retained. The backend receives its bind port from validated `BACKEND_PORT`. The Vite dev shell consumes the same variable and routes relative browser `/api` traffic to IPv4 loopback. Auth cookies and CSRF headers travel through that path, including chat over HTTP SSE. Chat has no application WebSocket endpoint. The frontend development port remains 5173. Invalid backend port values remain covered by the binding, configuration and previous proxy fixtures.

## Core workflow evidence

| Workflow | Current implementation and verification |
|---|---|
| Authentication and session handling | Implemented. R7.6 in-process routes cover protected requests and current account/session revalidation. Live login and cookie transport still require a browser run. |
| Capability discovery | Implemented. R7.6 covers inventory and capability routes with synthetic providers. R7.5 frontend fixtures cover capability rendering. |
| Model inventory and selection | Implemented. R7.6 covers canonical Ollama names and model identity. R7.5 frontend fixtures cover alternate-model inventory and readiness conflicts. |
| Explicit inference readiness | Verified offline. Authenticated checks are model-specific, with expiry and endpoint or identity changes invalidating readiness. Live inference is unverified. |
| Ordinary text chat | Verified in-process with synthetic provider responses and server-owned conversations. |
| Streaming | Verified in-process for completion, errors and cancellation. R7.6 reproduced and corrected false success after malformed tool markup. Mounted browser streaming is unverified. |
| Automatic planning | Verified with synthetic model output for conversational, arithmetic and conversion requests. Live Ollama planning is unverified. |
| Approved tool execution | Verified offline for allowlisted tools, validated arguments, authorization freshness and approval failure. |
| Cross-turn verified results | Verified in-process for `25 + 17` → 42, `multiply that by 3` → 126, `add 5` → 131 and `convert 5 km to metres` → 5000 m. Receipt scope and stale, missing and revoked cases remain covered. |
| Conversation persistence | Verified offline for same-process history, partial plain-chat responses and provenance checks. Readability across restart remains blocked by R6.4. |
| History navigation | Implemented. Prior frontend fixtures cover switching, background refresh, selection and stale callbacks. Mounted behavior is unverified. |
| Rename and delete | Verify-and-mutate routes pass in-process checks. Prior sidebar fixtures cover server confirmation and delayed deletion. |
| Failure and interruption | Backend cases pass offline. R7.6 adds an in-flight submission guard in the frontend. A client stopping reception may leave an operation running. |
| Logout and session cleanup | Implemented. Prior local frontend tests cover the logout client. Auth changes remount the workbench and stream cleanup aborts reception in source. Live logout during generation is unverified. |

## Corrections

| Defect | Change and coverage |
|---|---|
| Malformed model tool markup appeared as assistant text followed by `done`, with no corresponding completed assistant message in history. | `backend/agent/engine.py` now raises a typed failure for rejected markup in text and image reasoning. `backend/api/chat.py` emits the existing terminal `error` event with `INVALID_TOOL_REQUEST` and a fixed public message. The model's raw fragment stays out of the response. In-process regressions exercise both tracked and untracked text routes and the image route with a synthetic image. They verify no success terminal, no malformed fragment and no dispatch of the monitored tool. The text regressions also verify that history contains the user turn and no fabricated assistant completion. |
| A second send could enter the same rendered handler before React applied the streaming state, replace the active request and dispatch twice. | `frontend/src/components/chat/ChatView.tsx` checks the existing active abort controller synchronously. The handler fixture holds the first stream open, invokes the same callback twice and checks one dispatch, the first response and a working later submission. This fixture awaits Antigravity execution. |
| Two older Ollama protocol fixtures omitted the model identity required by the existing adapter. | `tests/fixtures/i7/model_outputs.json` provides the requested model identity and explicitly marks successful terminal records with `stop`. Provider checks and fixture assertions remain enforced. |

Backend test files changed are `tests/backend/test_a33_production_tools_offline.py`, `test_a61_multimodal_offline.py` and `test_i7_model_reliability.py`. The frontend fixture changed is `frontend/tests/a22-chatview-handlers.test.cjs`. The frontend reader already treats `error` as terminal and displays its content separately from model text. It needs no protocol change for the additional error code.

## Integrated backend verification

Run from the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i7_model_reliability.py tests/backend/test_a61_multimodal_offline.py
```

**186 passed, 0 failed, 0 skipped in 29.18 seconds.** Eight pytest warnings under disabled plugin autoload were one unknown `asyncio_mode` option and seven unregistered `asyncio` marks from collecting the module for its synchronous binding test. The suite used synthetic providers, in-process ASGI, temporary stores, mocked binding and the socket-forbidding test loop. The three targeted route tests reproduced the previous terminal-success bug before the fix. The initial expanded suite had 185 passes and one outdated Ollama fixture failure. The final integrated run includes that corrected fixture. `git diff --check` passed.

**R7.6 frontend runtime verification is pending.** Codex did not execute TypeScript, Node fixtures, Vite or a browser. The suite did not start the workbench, Ollama, Chroma or a VM. Native supervision and durable-provenance work remain gated. Authorization, tool approvals, protected document operations, artifact publication, generated-code execution and the disabled launcher retain their existing boundaries.

## Antigravity verification

From `frontend`, use the reviewed Node and local dependencies under the applicable offline execution approval. Use the preceding integrated backend command from the repository root.

```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a41-history-sidebar.test.cjs .\tests\r74-dev-proxy.test.cjs
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```

For separately approved live testing, use R7.3's disposable-state and private account setup and the R7.5 acceptance workflow. Require reviewed local executables, dependencies and the existing Ollama model digest, synthetic data and disposable accounts, monitored loopback and egress, and an independently enforced deadline with process-tree and listener cleanup evidence.

1. Run the approved listener preflight with `Get-NetTCPConnection -State Listen -ErrorAction Stop`. Attribute Ollama on `127.0.0.1:11434`. Proceed with the backend only if 8001 is free. Continue to Vite only if 5173 is free for the approved run. If 5173 still conflicts, record the browser test as blocked and use the R7.3 API-only workflow at `http://127.0.0.1:8001/api` if that scope is approved.
2. In the approved backend shell set `BACKEND_PORT=8001`, `BACKEND_HOST=127.0.0.1`, `CORS_ORIGINS=http://127.0.0.1:5173` and `OLLAMA_BASE_URL=http://127.0.0.1:11434`, retaining R7.3's `APP_ENV=development`, `LOCAL_CHAT_ONLY=true`, `AUTH_ENABLED=true`, `AUTH_DEV_OVERRIDE=false` and disposable directories. The reviewed entry point from the repository root is `& .\backend\venv\Scripts\python.exe -m backend` if that is the approved interpreter. Use one owned process without reload. Confirm its listener and `/api/health/ready`. Where approved, set `$env:BACKEND_PORT = '8001'` in the Vite shell and use `npm run dev -- --host 127.0.0.1 --strictPort` from `frontend`. Verify readiness through the proxy before sending credentials. Use a disposable browser profile for `http://127.0.0.1:5173`. Host-only cookies are shared across ports on the same host, so keep the profile away from unrelated local services.
3. Authenticate as a disposable operator. Select `qwen2.5:7b` and confirm inventory alone keeps sending disabled. Perform the explicit inference check and observe the matching model and expiry. Stream an ordinary prompt and record deltas and a single `done`. Verify the arithmetic and conversion sequence using registered tool events and result provenance. A second submit while the first stream is active should produce one request and one user turn. Verify history reload, a switch during refresh, rename and deletion, and a cross-owner access denial. Stop a separate stream after partial output and check the visible interruption. Log out during another generation and check that old callbacks cannot update the new session.
4. Record approved command identities, model and readiness evidence, redacted event types, history outcomes, exit status, and listener cleanup. Omit credentials, cookies and auth tokens. Stop only the test-owned processes through the approved runner and preserve the existing Ollama and Automata services. Keep live results blocked where authorization, a free bind or cleanup authority is missing.

## Next product feature

Prioritize **read-only image chat with explicit vision readiness** after the chat acceptance run. Inventory can currently yield `prerequisites_observed` for multimodal, which leaves the image composer disabled. The existing authenticated route in `backend/api/chat.py`, bounded decoder in `backend/multimodal/image_processor.py` and vision call in `backend/multimodal/service.py` already support isolated synthetic-image tests. Add model-specific vision readiness through `backend/health/routes.py` and `frontend/src/api/capabilities.ts` using the existing gate, with `tests/backend/test_a61_multimodal_offline.py` and `frontend/tests/a61-chat-attachment.test.cjs` as starting points.

Local document ingestion and RAG remain gated in `backend/api/documents.py`. Spreadsheet and document workflows have offline foundations in `backend/multimodal/workflow.py`, and protected artifact creation and access remain gated. Role administration and audit APIs exist in `backend/auth/routes.py` and `backend/audit/routes.py`. Secure organizational deployment remains unverified.

**Next action.** Hand this working-tree snapshot to Antigravity for the R7.6 offline frontend checks and the port-8001 acceptance run if its separate live prerequisites are satisfied.

---

## Antigravity local verification evidence

### 1. Current branch and HEAD
- Branch: `main`
- Starting HEAD: `43d72b8036b27b7926a74d08af260f865259669b`
- Working tree: Clean diffs preserved without git checkout, pull, or stash.

### 2. Files modified in working tree
- `backend/agent/engine.py`: Typed `InvalidModelToolRequest` exception on unparseable tool call markup.
- `backend/api/chat.py`: Explicit `INVALID_TOOL_REQUEST` error chunk in text, tracked text, and image reasoning streams; no fabricated completion.
- `frontend/src/components/chat/ChatView.tsx`: Synchronous `abortControllerRef.current` guard against in-flight double submissions.
- `tests/fixtures/i7/model_outputs.json`: Provided model identity and `stop` terminal markers to legacy test fixtures.
- `tests/backend/test_a33_production_tools_offline.py`: Regressions for tracked and untracked malformed tool requests.
- `tests/backend/test_a61_multimodal_offline.py`: Regression for image reasoning malformed tool requests.
- `tests/backend/test_i7_model_reliability.py`: Model reliability regressions with updated fixture.
- `frontend/tests/a22-chatview-handlers.test.cjs`: Added regression test for duplicate submission suppression.
- Preserved previous R7.2–R7.5 modified files and milestone reports.

### 3. Exact commands executed
All commands executed in standard non-elevated user space using locally installed binaries without external package downloads or network registry access:
1. **TypeScript App Compilation**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
   ```
2. **TypeScript Node/Vite Config**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
   ```
3. **Targeted R7.6 Regressions**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' --test .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a41-history-sidebar.test.cjs .\tests\r74-dev-proxy.test.cjs
   ```
4. **Complete Frontend Regression Suite (All 14 test files)**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
   ```
5. **Integrated Backend Regression Suite**:
   ```powershell
   $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i7_model_reliability.py tests/backend/test_a61_multimodal_offline.py
   ```
6. **Listener and Port Preflight**:
   ```powershell
   Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 8000, 8001, 5173, 5174, 11434 }
   ```
7. **Diff Integrity**:
   ```powershell
   git diff --check
   ```

### 4. TypeScript results
- `tsconfig.app.json`: **PASS** (0 errors, 0 disk writes).
- `tsconfig.node.json`: **PASS** (0 errors, 0 disk writes).

### 5. Targeted frontend tests
Command:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a41-history-sidebar.test.cjs .\tests\r74-dev-proxy.test.cjs
```
- **Result**: **37 passed, 0 failed** (463 ms).
- **Verified**:
  - `a22-chatview-handlers.test.cjs`: Rapid duplicate submission from the same render is dropped synchronously before React updates loading state; exactly one request is dispatched; composer releases properly upon terminal completion.

### 6. Complete frontend test counts
Command:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```
- **Result**: **67 passed, 0 failed, 0 skipped** across all 14 test files (823 ms).
- Baseline increased from 66 in R7.5 to **67** in R7.6:
  - `tests/a1-capability-render.test.cjs`: 6 passed.
  - `tests/a2-stream-client.test.cjs`: 7 passed.
  - `tests/a22-chatview-handlers.test.cjs`: 19 passed (+1 test for duplicate submission prevention).
  - `tests/a41-history-render.test.cjs`: 4 passed.
  - `tests/a41-history-sidebar.test.cjs`: 8 passed.
  - `tests/a51-documents-render.test.cjs`: 3 passed.
  - `tests/a52-source-render.test.cjs`: 2 passed.
  - `tests/a61-chat-attachment.test.cjs`: 4 passed.
  - `tests/a82-artifact-presentation.test.cjs`: 1 passed.
  - `tests/a91-access-render.test.cjs`: 1 passed.
  - `tests/historyRequests.test.mjs`: 6 passed.
  - `tests/i2-logout-client.test.cjs`: 1 passed.
  - `tests/i91-document-client.test.cjs`: 2 passed.
  - `tests/r74-dev-proxy.test.cjs`: 3 passed.

### 7. Backend regression counts
Command:
```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i7_model_reliability.py tests/backend/test_a61_multimodal_offline.py
```
- **Result**: **186 passed, 0 failed, 0 skipped** across 20 test files (19.67s).
- Baseline increased from 150 in R7.5 to **186** in R7.6 (+36 tests from multimodal, model reliability, and production tool error streams).
- **Warnings**: 8 configuration warnings (1 `asyncio_mode` option warning, 7 unregistered `pytest.mark.asyncio` mark warnings under disabled autoload). Zero functional failures.

### 8. Invalid tool-call verification
- Verified in `test_a33_production_tools_offline.py` and `test_a61_multimodal_offline.py`:
  - When the model returns malformed tool markup (`<tool_call>...`), `backend/agent/engine.py` raises `InvalidModelToolRequest`.
  - Routes in `backend/api/chat.py` emit a terminal `error` chunk with `error_code="INVALID_TOOL_REQUEST"` and a fixed public message.
  - Zero fabricated `done` events are emitted.
  - Raw unparsed markup is kept out of assistant responses.
  - Monitored tools are never dispatched.
  - Conversation history records only the user turn, preventing fabricated completions in memory.

### 9. Duplicate-send verification
- Verified in `frontend/tests/a22-chatview-handlers.test.cjs`:
  - `ChatView.tsx` checks `abortControllerRef.current` synchronously at entry.
  - Rapid double-clicks or repeated Enter keypresses before React applies the state update are dropped synchronously.
  - Exactly one backend request is dispatched.
  - Upon legitimate stream completion, the composer unlocks and accepts subsequent submissions.

### 10. Port availability findings
- `127.0.0.1:8001`: **FREE** (available for SovereignAI backend).
- `127.0.0.1:5174`: **FREE** (available for SovereignAI frontend via `npm run dev -- --host 127.0.0.1 --port 5174 --strictPort` without code changes).
- `127.0.0.1:5173`: **FREE** (previous Automata PID 35460 exited).
- `127.0.0.1:8000`: **FREE** (previous Automata PID 28724 exited).
- `127.0.0.1:11434`: **LISTENING** (Pre-existing local Ollama service, PID `35408`).

### 11. Live integration results or blockers
**BLOCKED.** Live acceptance testing was not launched due to:
- Missing explicit operator authorization for spawning live server processes.
- Absence of an external process supervisor / verified process-containment wrapper on the Windows 11 host.
- Preserved boundary preventing automated uncontained background daemon launches.

### 12. Confirmed defects and fixes
Zero new regressions found on the R7.6 snapshot. All Codex R7.6 changes in `engine.py`, `chat.py`, and `ChatView.tsx` operate cleanly together without defect.

### 13. Security restrictions preserved
- Outbound network blocked; administrative elevation forbidden; production deployment strictly NO-GO.
- Protected document ingestion/RAG and artifact publication remain gated.
- R5.5 supervisor and R6.4 durable provenance remain blocked.
- Tool authorization and ownership boundaries maintained.

### 14. R7.6 final scoped status
**R7.6: PARTIAL.**
- Offline Frontend Verification: **PASS** (TypeScript 0 errors, 67/67 tests passed).
- Offline Backend Verification: **PASS** (186/186 tests passed).
- Live Acceptance: **BLOCKED** pending explicit operator authorization and process containment controls.
- Production: **NO-GO**.

### 15. Handoff for Codex R8.1
1. Core workbench chat is functionally complete and offline-verified (0 TypeScript errors, 67 frontend passes, 186 backend passes).
2. The next priority is **read-only image chat with explicit vision readiness**:
   - Model-specific vision readiness through `backend/health/routes.py` and `frontend/src/api/capabilities.ts`.
   - Build upon `tests/backend/test_a61_multimodal_offline.py` and `frontend/tests/a61-chat-attachment.test.cjs`.
   - Resolve `prerequisites_observed` for multimodal into active image chat readiness.
