# R7.5 Chat-first acceptance

**R7.5 is PARTIAL. Production remains NO-GO.** Started on `main` at `43d72b8036b27b7926a74d08af260f865259669b`. Existing R7.2–R7.4 changes and reports were preserved.

Antigravity's R7.4 report records both no-emit TypeScript checks passing, 64 frontend tests passing across 14 files, and 137 offline backend passes. The alternate-port proxy, streaming error display and uncertain tool state passed those offline checks. At that preflight, port 8001 and frontend port 5173 were free, the former port 8000 owner had exited on its own, and Ollama was listening on loopback port 11434. Port availability must be rechecked for the actual run. The reports identify the installed default `qwen2.5:7b`, but startup, live inference and browser integration were not executed because live authorization and process containment were unavailable.

## Core workflow findings

| Workflow | Evidence and scope |
| --- | --- |
| Sign in and load capabilities | Implemented. R7.4's offline coverage includes role and logout handling. Backend ownership and authorization are exercised in process. End-to-end sign-in and browser cookie flow remain unverified. |
| Select an installed text model and check readiness | Implemented. Model inventory, identity mismatch and expiry are covered offline. The composer requires fresh server-confirmed readiness, and the backend independently enforces it. R7.5 preserves the server's typed reason on a conflicting readiness check. |
| Submit authenticated chat and receive streamed completion | Implemented and verified with synthetic providers and in-process routes. Failed or cancelled streams do not receive a later successful terminal event. Live Ollama inference and browser SSE remain unverified. |
| Automatically choose and execute tools | Offline routes verify ordinary chat without a tool, calculator, numeric continuity and the unit converter. The conversion uses synthetic model-supplied arguments in this test. R7.5 fixes the exact shorthand prompts in the acceptance sequence. Authenticated identity, registered tool and approval gates remain in force. |
| Save, reload, switch, rename and delete | R7.4 frontend fixtures and in-process backend tests cover these server-owned paths. R7.5 fixes a source-confirmed race where refresh superseded an explicit history click. The new frontend fixture awaits execution. R6.4 restart-readable provenance remains blocked. |
| Stop receiving a response, represent failure and log out | Implemented. Offline tests cover cancellation and cleanup, incomplete history, stale callbacks and logout handling. Partial text can remain visible and unfinished tool activity remains uncertain. Live provider cancellation and account isolation in the browser still require operator testing. |

## Defects fixed

- `backend/agent/numeric_receipts.py` and `backend/api/chat.py` now share the bounded arithmetic grammar between routing and argument resolution. Previously `multiply that by 3` and bare `add 5` in the requested sequence could bypass the verified-result path. They now require an eligible same-conversation result and go through the existing calculator plan and verifier. A missing or ineligible receipt yields `RESULT_REFERENCE_UNAVAILABLE` without invoking the provider. Plain arithmetic on a unit-bearing receipt still fails closed. `tests/backend/test_i111_followup_boundary.py`, `test_i112_numeric_receipts.py` and `test_r51_core_slice.py` cover these paths.
- `frontend/src/api/health.ts` now accepts a valid unavailable or pending capability report in an HTTP 409 response to the explicit checks. This preserves the inventory and the backend's specific readiness reason where present. A conflict cannot grant chat readiness, and authentication errors remain errors. The regression is in `frontend/tests/a1-capability-render.test.cjs`.
- `frontend/src/components/chat/ChatHistorySidebar.tsx` now separates automatic list refresh from explicit history selection. A background refresh no longer drops a pending click. Closing the drawer or another user action can still invalidate pending work. Confirmed rename and delete updates invalidate stale list responses. The regression is in `frontend/tests/a41-history-sidebar.test.cjs`.

Dev routing remains the validated backend `BACKEND_PORT` and Vite's matching IPv4 loopback proxy, with relative browser `/api` paths for auth, models, SSE and history. No application WebSocket is needed for chat. Cookie, CSRF and CORS behavior was not changed. Protected RAG, artifact publication, tool approvals, R5.5 process verification and R6.4 provenance restrictions retain their existing boundaries.

## Offline verification

Run from the repository root. The suite uses synthetic providers, in-process ASGI, temporary stores and mocked backend binding.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py
git diff --check
```

**150 passed, 0 failed, 0 skipped in 30.64 seconds.** The eight pytest warnings with plugin autoload disabled were one unknown `asyncio_mode` config option and seven unregistered `asyncio` marks during collection of the binding test's module. The exact acceptance sequence returned verified tool events for `42 → 126 → 131` and `5000 m` through the production route in process. `git diff --check` passed. R7.5 frontend TypeScript, fixture execution, live startup, browser integration and live inference were not run in Codex.

## Antigravity handoff

### Offline verification

From `frontend`, use the reviewed R7.4 Node and dependencies if the offline approval covers the current snapshot. The last command checks the full existing suite. The backend command above can be repeated under the same offline scope from the repository root.

```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a1-capability-render.test.cjs .\tests\a41-history-sidebar.test.cjs .\tests\r74-dev-proxy.test.cjs
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```

### Live acceptance after separate approval

Use the existing R7.3 chat-only settings, temporary state, credentials and process controls together with the R7.4 port overrides. No launcher or process supervisor changes are required by this milestone. If operator authorization or verified containment and cleanup are unavailable, record the blocker and finish the offline scope alone.

1. Check listeners with `Get-NetTCPConnection -State Listen -ErrorAction Stop`. Verify test ports 8001 and 5173 are free and attribute the existing Ollama listener on 11434. Keep unrelated services and port 8000 outside the test. In the reviewed backend shell, set `$env:BACKEND_PORT = '8001'` and `$env:CORS_ORIGINS = 'http://127.0.0.1:5173'`. Retain `APP_ENV=development`, `LOCAL_CHAT_ONLY=true`, `AUTH_ENABLED=true`, `AUTH_DEV_OVERRIDE=false`, `BACKEND_HOST=127.0.0.1`, `OLLAMA_BASE_URL=http://127.0.0.1:11434` and the R7.3 disposable directories. From the repository root launch the approved interpreter using the existing entry point `& .\backend\venv\Scripts\python.exe -m backend` if that interpreter matches the approved path. Use one supervised process without reload. Confirm the backend's owned listener and `GET /api/health/ready` on port 8001.
2. If Vite and browser execution are approved, from `frontend` export `$env:BACKEND_PORT = '8001'` in that shell and run `npm run dev -- --host 127.0.0.1 --strictPort` under the same ownership controls. Verify the owned listener on 5173 and `/api/health/ready` through the proxy. Open `http://127.0.0.1:5173` in the disposable test profile. For an approved API-only run, use the R7.3 PowerShell workflow with `$api = 'http://127.0.0.1:8001/api'`, or port 5173 to exercise the proxy.
3. Log in with the disposable operator account. Use only synthetic prompts. In Models, check Ollama inventory and select `qwen2.5:7b`. Confirm the composer is disabled with inventory alone. Run the explicit inference check and record matching model, readiness reason and expiry. Verify an unrelated, missing or expired model does not enable sending. Recheck readiness as needed for the remaining steps.
4. Send an ordinary question such as `What is a centrifugal pump?`. Capture model identity, observed incremental SSE deltas and one terminal `done`. Verify no tool ran. In the same owned conversation, submit `25 + 17`, `multiply that by 3`, `add 5`, and `convert 5 km to metres`. Verify successful registered calculator and unit-converter tool events for 42, 126, 131 and 5000 m. Answer text alone is insufficient evidence of execution.
5. Reopen and reload history during the same backend process. Verify no duplicate turns or unreadable text presented as verified. Select another conversation while a prior response or sidebar refresh is finishing. Confirm the intended selection and no old deltas in the new conversation. Rename and delete a disposable conversation and verify the confirmed results persist on reload.
6. Stop a separate synthetic generation after observing a partial delta. Observe an interrupted state, no fabricated `done`, and no automatic tool retry. Inspect any server history that was retained and preserve uncertainty for dispatched tools. Log out during a separate stream, then confirm the second disposable account cannot read the first account's history. Capture no cookies, credentials or tokens.
7. Shut down only the owned backend and frontend through the approved runner. Record exit status, process and listener cleanup, readiness and inference results, event evidence, skipped steps and any timeouts. Preserve the pre-existing Ollama service and unrelated processes.

**Next milestone.** Run the separately authorized acceptance workflow above in Antigravity and record the first live evidence for the port 8001 chat-first stack.

---

## Antigravity local verification evidence

### 1. Starting branch and HEAD
- Branch: `main`
- Starting HEAD: `43d72b8036b27b7926a74d08af260f865259669b`
- Working tree: Clean diffs preserved without git checkout, pull, or stash.

### 2. Actual commands executed
All commands executed in standard non-elevated user space using locally installed binaries without external package downloads or network registry access:
1. **TypeScript App Compilation**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
   ```
2. **TypeScript Node/Vite Config**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
   ```
3. **Targeted R7.5 Regressions**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' --test .\tests\a1-capability-render.test.cjs .\tests\a41-history-sidebar.test.cjs .\tests\r74-dev-proxy.test.cjs
   ```
4. **Complete Frontend Regression Suite (All 14 test files)**:
   ```powershell
   Set-Location frontend; & 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
   ```
5. **Focused Backend Regression Suite**:
   ```powershell
   $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py
   ```
6. **Listener and Port Preflight**:
   ```powershell
   Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 8000, 8001, 5173, 11434 }
   ```
7. **Diff Integrity**:
   ```powershell
   git diff --check
   ```

### 3. TypeScript results
- `tsconfig.app.json`: **PASS** (0 errors, 0 disk writes).
- `tsconfig.node.json`: **PASS** (0 errors, 0 disk writes).

### 4. Targeted frontend test results
Command:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a1-capability-render.test.cjs .\tests\a41-history-sidebar.test.cjs .\tests\r74-dev-proxy.test.cjs
```
- **Result**: **17 passed, 0 failed** (583 ms).
- **Verified Behaviors**:
  - `a1-capability-render.test.cjs`: HTTP 409 conflict capability reports preserve inventory and backend readiness reason without granting chat readiness.
  - `a41-history-sidebar.test.cjs`: Explicit history selection survives background list refresh but closes gracefully on drawer cleanup.
  - `r74-dev-proxy.test.cjs`: Dev proxy forwards `/api` to configured decimal port, defaults to 8000, and rejects invalid inputs.

### 5. Full frontend test total
Command:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```
- **Result**: **66 passed, 0 failed, 0 skipped** across all 14 test files (1023 ms).
- Baseline increased from 64 in R7.4 to **66** in R7.5:
  - `tests/a1-capability-render.test.cjs`: 6 passed (+1 test for 409 conflict handling).
  - `tests/a2-stream-client.test.cjs`: 7 passed.
  - `tests/a22-chatview-handlers.test.cjs`: 18 passed.
  - `tests/a41-history-render.test.cjs`: 4 passed.
  - `tests/a41-history-sidebar.test.cjs`: 8 passed (+1 test for selection/refresh race fix).
  - `tests/a51-documents-render.test.cjs`: 3 passed.
  - `tests/a52-source-render.test.cjs`: 2 passed.
  - `tests/a61-chat-attachment.test.cjs`: 4 passed.
  - `tests/a82-artifact-presentation.test.cjs`: 1 passed.
  - `tests/a91-access-render.test.cjs`: 1 passed.
  - `tests/historyRequests.test.mjs`: 6 passed.
  - `tests/i2-logout-client.test.cjs`: 1 passed.
  - `tests/i91-document-client.test.cjs`: 2 passed.
  - `tests/r74-dev-proxy.test.cjs`: 3 passed.

### 6. Backend test total
Command:
```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py
```
- **Result**: **150 passed, 0 failed, 0 skipped** across 18 test files (15.30s).
- Baseline increased from 137 in R7.4 to **150** in R7.5 (+13 tests in `test_i111_followup_boundary.py`).
- **Warnings**: 8 configuration warnings (1 `asyncio_mode` option warning, 7 unregistered `pytest.mark.asyncio` mark warnings under disabled autoload). Zero functional failures.

### 7. Confirmed defects and fixes
1. **Arithmetic Follow-Up Shorthand & Receipt Binding**:
   - `backend/agent/numeric_receipts.py` and `backend/api/chat.py`: Unified shorthand arithmetic parsing (`multiply that by 3`, `add 5`) with receipt resolution. Enforces eligible same-conversation receipts and returns `RESULT_REFERENCE_UNAVAILABLE` on missing/ineligible receipts without model guessing.
2. **Readiness Conflict Handling (409 Response)**:
   - `frontend/src/api/health.ts`: Conflict responses (HTTP 409) containing valid capability reports are parsed safely, surfacing the backend's specific readiness failure reason without granting false chat readiness.
3. **History Selection & Refresh Decoupling**:
   - `frontend/src/components/chat/ChatHistorySidebar.tsx`: Separated list refresh generation from conversation action generation. Background list reloading no longer cancels an in-flight user conversation selection.

### 8. Port availability findings
- `127.0.0.1:8001`: **FREE** (available for SovereignAI backend binding).
- `[::1]:5173`: **OCCUPIED** by external development process (PID `35460`, running `Automata_26_ApexCode` Vite server).
- `127.0.0.1:8000`: **OCCUPIED** by external development process (PID `28724`, running `Automata_26_ApexCode` Uvicorn server).
- `127.0.0.1:11434`: **LISTENING** (Pre-existing local Ollama service, PID `35408`).

### 9. Live integration results or blockers
**BLOCKED.** Live acceptance testing was not launched due to:
- **Port 5173 Conflict**: The standard frontend development port `5173` is occupied by an active external project (`Automata_26_ApexCode`, PID `35460`). Per isolation rules, unrelated developer processes cannot be terminated.
- **Separate Live Authorization**: Live backend process spawning and live loopback model inference require explicit operator authorization and an isolated supervised execution context with clean process-tree termination.
- **Process Containment**: No external process supervisor or verified process-tree cleanup wrapper is active on the host.

### 10. Process cleanup evidence
- No backend or frontend daemon processes were spawned.
- Pre-existing Ollama service (PID `35408`) and Automata processes (PIDs `28724`, `35460`) were preserved without disturbance.
- Zero orphaned processes or leaked listening sockets.

### 11. Files modified in working tree
- `backend/agent/numeric_receipts.py`: Follow-up arithmetic pattern grammar and receipt resolution.
- `backend/api/chat.py`: Route-level receipt requirement checking.
- `frontend/src/api/health.ts`: 409 conflict report parsing.
- `frontend/src/components/chat/ChatHistorySidebar.tsx`: Generational separation of list refresh vs selection.
- `frontend/tests/a1-capability-render.test.cjs`: 409 conflict regression test.
- `frontend/tests/a41-history-sidebar.test.cjs`: History selection race regression test.
- `tests/backend/test_i111_followup_boundary.py`: Shorthand arithmetic and receipt boundary tests.
- `tests/backend/test_i112_numeric_receipts.py`: Numeric receipt validation tests.
- `tests/backend/test_r51_core_slice.py`: Full calculation sequence tests ($42 \to 126 \to 131 \to 5000\text{ m}$).
- `RELEASE_R7_5_CHAT_FIRST_ACCEPTANCE_REPORT.md`: This report.

### 12. Remaining functional gaps
- End-to-end HTTP/SSE streaming between frontend and backend over loopback.
- Live model token generation from local Ollama under live inference.
- Durable message recovery across backend restarts (blocked by R6.4).
- Production build and deployment (NO-GO).

### 13. Exact next handoff for Codex
1. The offline suite is verified 100% healthy: 0 TypeScript errors, 66 frontend test passes, 150 backend test passes.
2. For live acceptance:
   - Free port `5173` or configure an alternate frontend port (e.g. `5174`) before launching Vite with `--strictPort`.
   - Ensure the operator provides explicit live launch authorization.
   - Run the 7-step live acceptance matrix on backend port `8001`.

### 14. R7.5 verification status
**R7.5: PARTIAL.**
- Offline Frontend Verification: **PASS** (TypeScript 0 errors, 66/66 tests passed).
- Offline Backend Verification: **PASS** (150/150 tests passed).
- Live Acceptance: **BLOCKED** by port 5173 collision and missing live operator authorization.
- Production: **NO-GO**.
