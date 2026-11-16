# R7.4 Workbench integration

**R7.4 is PARTIAL. Production remains NO-GO.** Started on `main` at `43d72b8036b27b7926a74d08af260f865259669b`. This milestone preserves the uncommitted R7.2 and R7.3 changes and reports.

Antigravity's R7.3 report records passing app and Node/Vite TypeScript checks, 59 frontend passes and 131 backend passes. Its live inventory check at `127.0.0.1:11434` observed `qwen2.5:7b`, `llava:7b` and `nomic-embed-text:latest`. It reported no new TypeScript or fixture failures. Ollama inference, actual backend startup, networked SSE and browser behavior remain unverified. Port 8000 was occupied by an unrelated Automata_26_ApexCode Uvicorn process. That collision and the absence of live authorization blocked the launch. R7.4 removes the hardcoded proxy dependency on that port.

| Confirmed integration defect | Correction |
| --- | --- |
| The backend can bind a validated `BACKEND_PORT`, but Vite always proxied `/api` to the default port. Settings also displayed that port as the API URL. | Vite development now reads the same `BACKEND_PORT` from its shell, validates it as a decimal port from 1 to 65535 and forwards unchanged `/api` paths to `127.0.0.1` on that port. Invalid overrides fail configuration rather than falling back. The default remains 8000, and build and preview ignore the development override. The Settings view shows the actual browser request path, `/api` on the current site. |
| On stream error, the frontend replaced received text with the error, even when the server had retained the partial answer. Tool activity also disappeared from the error view. | Chat retains partial content and displays the error separately. A tool start without a terminal result remains visible as an uncertain outcome. Both parser errors and thrown transport errors use the existing request guard and release the composer. The chat UI still has no automatic retry. |
| A FastAPI 422 response can return a structured `detail` array, which JSON chat passed through to a string callback and then to React rendering. Multipart chat stringified raw details. | Both streaming clients preserve readable string details and show `The chat request is invalid.` for structured 422s. Other non-string or non-JSON failures fall back to the HTTP status. |

Backend startup still uses `backend/__main__.py` and `ConfigValidator` directly. No backend or production configuration was changed. Browser auth, readiness, chat, approval and history clients continue to request relative `/api` URLs. Cookies and the CSRF header still travel on that path, and the dev proxy forwards HTTP SSE without endpoint rewrites. Chat uses credentialed `fetch` over HTTP streams. There is no application WebSocket chat endpoint to remap. Vite's HMR channel remains on the frontend origin. The browser uses a single origin, so this change does not require wildcard CORS, cookie domain rewriting or client-side readiness bypasses.

Backend readiness still requires a fresh inference result for the chosen model. An installed alternate can be selected independently of its send eligibility. Successful SSE still requires a terminal `done`, and an error or cancellation cannot become success. Conversation ownership, stale callback guards, logout invalidation, server-confirmed rename/delete, and deduplicated history retain their existing paths. Same-process chat history can show a retained partial reply. Incomplete or withheld replies are excluded from inference as before. SQLite stores security and task state, while conversation messages use the existing JSON store. Durable provenance after restart remains R6.4 BLOCKED.

R7.4 production source changes are in `frontend/vite.config.ts`, `frontend/src/api/client.ts`, `frontend/src/types/index.ts`, `frontend/src/components/chat/ChatView.tsx`, `frontend/src/components/chat/MessageItem.tsx` and `frontend/src/components/settings/SettingsView.tsx`. `.env.example` documents the shell setting. Regression coverage is in `frontend/tests/r74-dev-proxy.test.cjs`, `a2-stream-client.test.cjs`, `a22-chatview-handlers.test.cjs` and `a41-history-render.test.cjs`. The new proxy fixture evaluates the real config with stub plugins and covers alternate, default and invalid ports, plus build and preview isolation. No Vite process is needed for that fixture.

## Codex verification

Run from the repository root for the same socket-free synthetic and in-process backend scope, including the mocked Uvicorn entry point and configuration checks:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py
git diff --check
```

**137 passed, 0 failed, 0 skipped in 22.46 seconds.** Pytest reported eight warnings with plugin autoload disabled: one unknown `asyncio_mode` config option and seven unregistered `asyncio` marks encountered while collecting the module containing the selected synchronous binding test. The suite verifies calculator `42 → 126 → 131`, conversion `5000 m`, ordinary chat, SSE failures, cleanup, owner checks, numeric receipt provenance, approval gating and protected storage denial. `git diff --check` passed. R7.4 frontend tests and type checks were left for the authorized Antigravity environment. Codex did not launch Uvicorn, Vite, Ollama, Chroma, a VM or a browser. R5.5, R6.4 and the protected RAG and artifact controls remain in force.

## Antigravity verification handoff

### Offline scope

Use the reviewed R7.3 Node executable and installed dependencies with approval covering the R7.4 snapshot. From `frontend`, run both no-emit checks and the focused fixtures. The backend command above can be rerun from the root under the same offline scope.

```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' --test .\tests\r74-dev-proxy.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a22-chatview-handlers.test.cjs .\tests\a41-history-render.test.cjs .\tests\a1-capability-render.test.cjs .\tests\a41-history-sidebar.test.cjs .\tests\i2-logout-client.test.cjs
```

### Separately authorized live scope

Use the R7.3 operator-approved containment, egress, deadline, process ownership and cleanup requirements. Treat missing evidence as a blocker, and complete the offline scope alone in that case. Start with the approved, already-running Ollama at `127.0.0.1:11434`. Leave port 8000's process entirely alone. Before launching anything, check current listeners with `Get-NetTCPConnection -State Listen -ErrorAction Stop`. Confirm 8001 and, if using Vite, 5173 are both free and attribute the approved Ollama listener. A bind collision ends that attempt. Do not stop or probe the unrelated 8000 HTTP service.

**Backend.** From the root of the reviewed snapshot, use the R7.3 report's process-scoped chat-only environment and disposable task DB, history, logs, upload and sandbox directories. Substitute the following in the backend shell after setting the R7.3 values. Retain `APP_ENV=development`, `LOCAL_CHAT_ONLY=true`, `AUTH_ENABLED=true`, `AUTH_DEV_OVERRIDE=false`, `BACKEND_HOST=127.0.0.1` and `OLLAMA_BASE_URL=http://127.0.0.1:11434`. Use the approved interpreter path from R7.3, and do not use reload, a launcher or multiple backend workers.

```powershell
$env:BACKEND_PORT = '8001'
$env:CORS_ORIGINS = 'http://127.0.0.1:5173'
& .\backend\venv\Scripts\python.exe -m backend
```

**Frontend.** If Vite startup is separately approved, from `frontend` in its own reviewed shell set the same port. `BACKEND_PORT` is read from the process environment by Vite, so changing only the root `.env` will not redirect the proxy. The strict frontend bind prevents silent frontend port changes. Confirm both new listeners are attributed to their independently supervised processes before making requests.

```powershell
$env:BACKEND_PORT = '8001'
npm run dev -- --host 127.0.0.1 --strictPort
```

**Requests.** For the R7.3 PowerShell test matrix use `$api = 'http://127.0.0.1:8001/api'` for backend-only tests, or `$api = 'http://127.0.0.1:5173/api'` to test the development proxy. Apply the port change to every R7.3 readiness, login, session, streaming and logout request. For separately approved browser testing open `http://127.0.0.1:5173` in an isolated test profile. Keep the development auth cookie host-only and use only the two disposable accounts. Cookies are scoped by host rather than port, so keep the test profile away from any other local service, including the unrelated listener on 8000. Use the R7.3 bootstrap procedure privately if the isolated store has no user. Preserve authentication, CSRF, model checks and the production cookie/CORS settings.

1. Verify backend `/api/health/ready` and the approved proxy path, with no Chroma store created. Verify login, authenticated `/api/auth/me`, and logout followed by denied access to a prior owned session. Capture no credentials or tokens.
2. Confirm the default and alternate model IDs against the approved inventory. Select the installed model, check explicit inference readiness, and verify that expiry, an alias mismatch or a missing model prevents sending. The inventory check itself must not enable sending. Refresh verified readiness before it expires as needed during the matrix.
3. Stream an ordinary synthetic chat prompt and observe deltas and a single `done`. In the same owned conversation use the R7.3 prompts for `42 → 126 → 131` and `5000 m`, and verify `tool_result` events. Confirm history reload does not duplicate responses, a second authorized user cannot read the first user's history, and rename/delete take effect after successful requests.
4. Stop a synthetic response after a delta, or exercise an approved provider failure. The composer should release, partial text should remain visible when received, and there should be no false success or automatic retry. Preserve an uncertain side-effect outcome if the tool has started. Switch conversations and log out during a stream to check that late callbacks cannot update the new session. A browser-level test requires separate approval. Do not stop the pre-existing Ollama service to induce a failure.
5. Log out, gracefully stop only the owned frontend and backend through the approved runner, and verify exit and listener cleanup. Preserve the pre-existing Ollama and Automata processes. Record port and process ownership, exit codes, elapsed deadlines, authenticated readiness and event outcomes, and sanitized synthetic logs. Report any missing native, live or browser evidence explicitly.

**Next milestone.** Antigravity should verify the R7.4 frontend diff and, after the live controls are approved, run the isolated chat-first workflow on backend port 8001.

---

## Antigravity local verification evidence

### 1. Starting branch and HEAD
- Branch: `main`
- Starting HEAD: `43d72b8036b27b7926a74d08af260f865259669b`
- Working tree: Clean diffs preserved without git checkout, pull, or stash.

### 2. Actual commands executed
All commands executed from repository root or `frontend` using locally installed binaries without registry or network access:
1. **TypeScript App Compilation**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
   ```
2. **TypeScript Node/Vite Config**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
   ```
3. **Targeted R7.4 Regressions**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' --test .\tests\r74-dev-proxy.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a22-chatview-handlers.test.cjs .\tests\a41-history-render.test.cjs .\tests\a1-capability-render.test.cjs .\tests\a41-history-sidebar.test.cjs .\tests\i2-logout-client.test.cjs
   ```
4. **Complete Frontend Regression Suite (All 14 test files)**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
   ```
5. **Focused Backend Regression Suite**:
   ```powershell
   $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py
   ```
6. **Listener and Port Preflight**:
   ```powershell
   Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 8000, 8001, 5173, 11434 }
   ```

### 3. TypeScript results
- `tsconfig.app.json`: **PASS** (0 errors, 0 disk writes).
- `tsconfig.node.json`: **PASS** (0 errors, 0 disk writes).

### 4. Frontend test counts
- **Targeted Suite**: **45 passed, 0 failed** across 7 test files (559 ms).
- **Complete Suite**: **64 passed, 0 failed, 0 skipped** across all 14 test files (836 ms).
  - `tests/a1-capability-render.test.cjs`: 5 passed.
  - `tests/a2-stream-client.test.cjs`: 7 passed (includes FastAPI structured validation handling).
  - `tests/a22-chatview-handlers.test.cjs`: 18 passed.
  - `tests/a41-history-render.test.cjs`: 4 passed (includes partial text & error presentation).
  - `tests/a41-history-sidebar.test.cjs`: 7 passed.
  - `tests/a51-documents-render.test.cjs`: 3 passed.
  - `tests/a52-source-render.test.cjs`: 2 passed.
  - `tests/a61-chat-attachment.test.cjs`: 4 passed.
  - `tests/a82-artifact-presentation.test.cjs`: 1 passed.
  - `tests/a91-access-render.test.cjs`: 1 passed.
  - `tests/historyRequests.test.mjs`: 6 passed.
  - `tests/i2-logout-client.test.cjs`: 1 passed.
  - `tests/i91-document-client.test.cjs`: 2 passed.
  - `tests/r74-dev-proxy.test.cjs`: 3 passed (proxy port configuration, defaults, error handling).

### 5. Backend test counts
- **Result**: **137 passed, 0 failed, 0 skipped** (15.10s).
- **Warnings**: 8 total (1 PytestConfigWarning for `asyncio_mode`, 7 PytestUnknownMarkWarning for uncollected `@pytest.mark.asyncio` in `test_phase15_security_controls.py` under disabled plugin autoload). Zero functional failures.

### 6. Confirmed fixes verified
1. **Port 8001 Dev Proxy**: Vite development proxy forwards `/api` to `127.0.0.1:${BACKEND_PORT:-8000}`.
2. **Invalid Port Enforcement**: Rejects non-decimal or out-of-range ports (`0`, `65536`, `8e3`, URL strings) with explicit error.
3. **Settings Presentation**: Settings view displays `/api on this site` rather than a hardcoded port.
4. **Partial Text Preservation**: Assistant messages retain partial text upon stream failure (`message.errorMessage` rendered separately from `message.content`).
5. **Separate Error Display**: Errors display with clear visual hierarchy without replacing streamed text.
6. **No False Completion**: Failure/error paths release the composer without emitting or displaying success.
7. **Uncertain Tool Retention**: Unfinished tool invocations remain visible as uncertain rather than disappearing or claiming success.
8. **Automatic Retry Suppression**: Stream errors never offer automatic retry buttons.
9. **FastAPI Structured Validation**: 422 detail arrays are converted to clean user-facing status messages (`The chat request is invalid.`).

### 7. Port 8001 configuration findings
- `127.0.0.1:8001` is currently free and available.
- Port `8000` is also currently free (the prior external process PID `31576` exited naturally).
- Port `5173` is currently free.
- Port `11434` is bound to the pre-existing Ollama service (PID `35408`).
- Backend configuration validator strictly enforces `1 <= backend_port <= 65535`.
- Vite development server strictly enforces `BACKEND_PORT` validation and defaults cleanly to `8000`.

### 8. Live integration results or blockers
**BLOCKED.** Live backend launch was not initiated due to:
- Missing explicit operator authorization for spawning live server processes.
- Absence of an external process supervisor / verified process-containment and cleanup wrapper.
- Preserved developer boundary preventing automated uncontained background daemon launches.

### 9. Ollama inference evidence
Live inference was not executed in this milestone due to the live integration block. Preflight evidence established in R7.3 remains valid:
- Endpoint: `127.0.0.1:11434`
- Default model: `qwen2.5:7b` (digest `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`)
- Multimodal model: `llava:7b` (digest `8dd30f6b0cb19f555f2c7a7ebda861449ea2cc76bf1f44e262931f45fc81d081`)

### 10. Process cleanup evidence
- No background processes, child shells, or listeners were launched.
- Port states verified clean before and after execution.
- Existing Ollama service (PID 35408) preserved intact.

### 11. Files modified in working tree
- `frontend/vite.config.ts`: Configurable `BACKEND_PORT` proxy.
- `frontend/src/api/client.ts`: Structured 422 error parsing for streams.
- `frontend/src/types/index.ts`: Added `errorMessage` to `ChatMessage`.
- `frontend/src/components/chat/ChatView.tsx`: Partial content retention and error mapping.
- `frontend/src/components/chat/MessageItem.tsx`: Visual separation of partial content and errors.
- `frontend/src/components/settings/SettingsView.tsx`: Relative API path presentation.
- `frontend/tests/r74-dev-proxy.test.cjs`: New proxy configuration test suite.
- `frontend/tests/a2-stream-client.test.cjs`: Extended SSE error handling tests.
- `frontend/tests/a22-chatview-handlers.test.cjs`: Extended handler tests for error retention.
- `frontend/tests/a41-history-render.test.cjs`: Extended history tests for partial text rendering.
- `.env.example`: Documented `BACKEND_PORT`.
- `RELEASE_R7_4_WORKBENCH_INTEGRATION_REPORT.md`: This report.

### 12. Remaining functional defects
Zero confirmed functional defects in the offline verification scope.

### 13. Exact next handoff for Codex R7.5
1. Offline verification is complete and fully green across both frontend (64 passes) and backend (137 passes).
2. For R7.5, when live execution is authorized by the operator:
   - Export `$env:BACKEND_PORT = '8001'` in both backend and frontend environments.
   - Launch backend via `& .\backend\venv\Scripts\python.exe -m backend` with `LOCAL_CHAT_ONLY=true`.
   - Launch frontend via `npm run dev -- --host 127.0.0.1 --strictPort`.
   - Execute the 6-step live chat integration matrix (readiness check, ordinary chat, tool sequence $42 \to 126 \to 131 \to 5000\text{ m}$, partial stream interruption, session ownership, graceful shutdown).

### 14. R7.4 verification status
**R7.4: PARTIAL.**
- Offline Frontend Verification: **PASS** (TypeScript 0 errors, 64/64 tests passed).
- Offline Backend Verification: **PASS** (137/137 tests passed).
- Live Ollama Integration: **BLOCKED** pending explicit live operator authorization and process containment controls.
- Production: **NO-GO**.
