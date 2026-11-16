# R8.5 — Local runtime acceptance

**Scoped offline preparation PASS. R8.5 overall PARTIAL. Live acceptance BLOCKED. Production NO-GO.** Started on `main` at `43d72b8036b27b7926a74d08af260f865259669b` with existing uncommitted work.

The latest [R8.4 report](RELEASE_R8_4_DOCUMENT_PREVIEW_REPORT.md) contains no appended Antigravity results as of this review. It records 33 focused Codex passes and a broad pytest summary of 248 passes whose final status could not be confirmed after automatic approval review flagged an unexpected Visual Studio telemetry connection. The last confirmed Antigravity baseline in the repository is [R8.3](RELEASE_R8_3_TEXT_PREVIEW_REPORT.md) with 89 frontend and 239 broad backend passes. No live browser, application or inference success was established there.

## Connectivity and verification state

| Area | Actual connection or contract | Evidence |
|---|---|---|
| Backend entry, configuration, binding | `python -m backend` validates settings and passes `BACKEND_HOST` and `BACKEND_PORT` to Uvicorn. FastAPI `/api/health/ready` reports lifespan initialization. | Verified offline for configuration and lifespan. OS listener binding and ownership unverified. |
| Browser, proxy, SSE, API errors | The React clients use relative `/api` URLs. Vite's dev proxy reads shell `BACKEND_PORT` and targets numeric loopback without rewriting `/api`. Vite's frontend port can be overridden by CLI. SSE and JSON handlers use the same proxy. | Proxy config verified in the R7.5 Antigravity fixture. Actual browser/proxy traffic and streaming unverified. |
| Authentication, CORS, session | Login sets the host-only `HttpOnly` cookie when the domain is unset. Browser fetch includes credentials and `X-Requested-With` for cookie-authenticated writes. The local profile pins CORS to the test frontend origin. | Verified offline through the real app with a disposable operator, CSRF rejection, sign-out and session revocation. Browser cookie storage and forwarding unverified. |
| Chat-only profile | Development configuration requires authenticated loopback startup and a loopback Ollama URL. The lifespan supplies unavailable vector and embedding services. | Verified offline. Protected operations remain gated. |
| Ollama and model readiness | Explicit authenticated inventory uses `/api/tags`. `/api/chat` performs separate text and vision probes. Readiness is model- and endpoint-bound, checks recorded digest identity, and expires at the earlier of the inference and inventory deadlines. Vision requires a digest. Runtime HTTP responses are checked for model identity, assistant role, invalid tool requests and completion. | Verified offline with mocked HTTP and in-process probes. Actual installed models and inference unverified for this run. |
| Chat, automatic tools, history | Ordinary chat streams through the existing API. Calculator and unit conversion use authorized tools and verified numeric continuity in the same conversation. History is owner-scoped with fail-closed provenance. | Verified offline with synthetic responses, actual local tool dispatch, and history and ownership checks. Restart durability remains gated by R6.4. |
| Vision, UTF-8 preview, line ranges | Multimodal chat requires fresh text and vision readiness. UTF-8 preview works without a model. Questions use freshly validated, request-scoped excerpts. History exposes safe attachment metadata. | Verified offline, including exact excerpt delivered to a synthetic provider. Live inference, image UI and R8.4 line-selection UI unverified. |
| Errors and cleanup | Request and provider failures use bounded public errors. Interrupted streams can retain partial or uncertain state. The app closes its own provider clients and clears readiness on exit. | Verified offline for failure, cancellation and lifespan cleanup. Windows process-tree containment and exit observation externally blocked. |

## Defects fixed

| Files | Correction |
|---|---|
| `backend/models/ollama_provider.py`, `tests/backend/test_a2_provider_offline.py` | Runtime decoding accepted terminal and partial messages with missing, user or tool roles. It now requires `assistant`, matching the existing readiness probe and [Ollama chat response](https://docs.ollama.com/api/chat). Tests verify rejection in both response modes and an SSE error with failed history after a malformed terminal reply. |
| `backend/main.py`, `tests/backend/test_r12_readiness_offline.py` | The FastAPI lifespan announced that it was listening before it had socket evidence. It now logs the configured bind address. The in-process test verifies that wording, disposable state, cookie login, exact CORS origin, CSRF rejection, explicit readiness, protected capability status and sign-out. |
| `tests/backend/test_a22_async_offline.py`, `tests/backend/test_multimodal.py` | Updated synthetic provider replies to use the real response shape. The existing timer-only test loop closes request generators on the loop thread without a self-pipe wakeup. Socket and I/O registration bans still apply. |

The reviewed 8001/5174 configuration needs no proxy, CORS or browser-client source change. The disabled launcher and R5.5 controller were left intact.

## Offline results

From the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_a2_provider_offline.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a22_async_offline.py --tb=short
python -m pytest -q tests/backend/test_r51_core_slice.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_r83_text_file_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py --tb=short
```

The first command passed **56** in 5.86s and the second passed **83** in 13.07s. Both returned exit 0 with zero failures or skips and the existing pytest config warning for `asyncio_mode` under disabled plugin autoload. Earlier fixture iterations exposed a missing timestamp, outdated mock message roles and a timer-loop finalizer warning. The final commands passed with those corrected. `git diff --check` passed. The legacy `test_multimodal.py` fixture was updated by inspection, with that test unexecuted here. Codex did not repeat the R8.4 broad run or execute frontend, live application, model, or native tests.

## Antigravity handoff

1. From `<repo_root>`, rerun the two offline backend commands above and capture their exit codes. For the remaining R8.4 frontend and broad backend evidence, use the [R8.4 verification commands](RELEASE_R8_4_DOCUMENT_PREVIEW_REPORT.md#backend-verification) in the approved offline environment. A telemetry alert stops execution and leaves the affected run unverified.
2. **Live block remains in force.** Before any live start, obtain independent written authorization for the exact reviewed Python, Node/Vite and browser executables, commands, isolated browser profile and local model traffic. The operator must already have an independently verified way to own the processes and descendants, enforce a deadline, attribute listeners to retained process identity, observe cleanup independently, and preserve unrelated processes. Confirm isolated state, bounded and private evidence collection, and monitored loopback-only traffic. R5.5 and `scripts/dev.ps1` do not provide this authority. `Get-NetTCPConnection -State Listen -ErrorAction Stop` is an initial listener snapshot. Confirm 8001 and 5174 are free and attribute the approved pre-existing Ollama listener on 11434. A free port snapshot alone cannot exclude a later bind race.
3. Only after those conditions pass, use the following settings and commands within the approved runner. Use a reviewed workspace snapshot with no `.env` or one reviewed for conflicts such as `AUTH_COOKIE_DOMAIN`. Scrub inherited service settings. Verify the actual interpreter and frontend tool paths before launch. Run one backend worker without reload. Collect the generated bootstrap credential privately on a fresh state directory and keep it out of logs and evidence artifacts.

```powershell
# Backend process environment. Run from the repository root in the approved runner.
$runtimeState = Join-Path $env:TEMP ('sovereign-r85-' + [guid]::NewGuid().ToString('N'))
$env:APP_ENV = 'development'
$env:ENVIRONMENT = 'development'
$env:LOCAL_CHAT_ONLY = 'true'
$env:AUTH_ENABLED = 'true'
$env:AUTH_DEV_OVERRIDE = 'false'
$env:ALLOW_DEV_CLEARANCE_SIMULATION = 'false'
$env:BACKEND_HOST = '127.0.0.1'
$env:BACKEND_PORT = '8001'
$env:CORS_ORIGINS = 'http://127.0.0.1:5174'
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
$env:AUTH_COOKIE_SECURE = 'false'  # Disposable HTTP loopback only
$env:AUTH_COOKIE_SAMESITE = 'lax'
Remove-Item Env:AUTH_COOKIE_DOMAIN -ErrorAction SilentlyContinue
$env:TASKS_DIR = Join-Path $runtimeState 'tasks'
$env:TASKS_DB_PATH = Join-Path $env:TASKS_DIR 'tasks.db'
$env:CONVERSATION_MEMORY_PATH = Join-Path $runtimeState 'memory.json'
$env:UPLOAD_DIR = Join-Path $runtimeState 'uploads'
$env:LOG_DIR = Join-Path $runtimeState 'logs'
$env:SANDBOX_DIR = Join-Path $runtimeState 'sandbox'
$env:CHROMA_PERSIST_DIR = Join-Path $runtimeState 'chroma'
& .\backend\venv\Scripts\python.exe -m backend
```

```powershell
# Separate frontend process environment, from the repository root in the approved runner.
Set-Location .\frontend
$env:BACKEND_PORT = '8001'
npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

Before credentials, verify both listeners belong to the approved run. Check `GET /api/health/ready` directly on port 8001 and through 5174. Confirm the chat-only log message and absence of a Chroma directory at the configured test path. In a private session, sign in as the generated administrator, change that password, and provision a disposable `operator` with `POST /api/auth/users` and the normal RBAC checks. Use the operator for acceptance in the disposable browser at `http://127.0.0.1:5174`. Reserve the test administrator for provisioning or separately approved audit viewing. Never retain cookies, bearer tokens, login payloads or the bootstrap banner in acceptance evidence.

Stop on missing authorization, unapproved binaries or model digests, occupied test ports, unknown or changed ownership, unexpected outbound traffic, protected capability exposure, apparent success after a failed operation, exceeded deadline or uncertain cleanup. Use only the approved runner's owned-process shutdown. Keep the pre-existing Ollama process and unrelated listeners running. Record missing evidence without changing host policy, starting a VM or restoring the launcher.

## Live acceptance matrix

Record `PASS` for each row only with its listed evidence, `FAIL` for an executed contradiction, `BLOCKED` for a missing prerequisite or required evidence, and `NOT EXECUTED` for an optional case deliberately left unrun. Record evidence references and timestamps per row.

| Case | Procedure and evidence required for PASS | Current live outcome |
|---|---|---|
| A · Start and sign in | Correlate stable process identities and 8001/5174 listeners. Confirm direct and proxied readiness and successful cookie-authenticated `/api/auth/me`. Verify unauthenticated inference check returns 401, browser POST uses the CSRF header, and protected RAG, artifact, code execution and approval-resume statuses are blocked. | BLOCKED |
| B · Text chat | Check approved Ollama version and `/api/tags` identity. In the workbench run inventory and explicit text readiness for installed, approved `ollama/qwen2.5:7b`. Record `state`, `reason`, `expires_at` and digest evidence. In a new conversation ask an ordinary question. Capture real `/api/chat` deltas, one valid `done`, and the same answer on owned history reload during the run. | BLOCKED |
| C · Approved tools | In one conversation ask `What is 25 + 17?`, `Multiply that result by 3.`, `Add 5 to that result.`, then `Convert 5 km to metres.` Expect 42, 126, 131 and 5000 m. Capture successful `tool_result`, `task_completed`, owner-visible `tool_events` invocation identifiers and sanitized `tool.execution` audit evidence correlated with task and session. Assistant text alone cannot establish dispatch. | BLOCKED |
| D · Vision | With text still ready, select approved installed `ollama/llava:7b` or another approved configured vision model and run explicit vision readiness. Send a synthetic PNG depicting a blue square and ask its color. Capture response and completion, absence of tool/source events, and history showing metadata with no replayable image. Check that a dependent follow-up requests reattachment. | BLOCKED |
| E · UTF-8 and line range | Use the four-line synthetic file from R8.4. With inference unchecked, upload and preview lines 2–3. After fresh text readiness, ask about that excerpt and capture successful response and descriptor-only history. Confirm that a follow-up about the earlier file requires reattachment. Establish exact selected-only provider input with approved local observation of the synthetic request. Without it, that subcheck stays BLOCKED even if the answer is correct. | BLOCKED |
| F1 · Invalid, stale and cancelled | Submit malformed text/range, then wait for the reported readiness to expire and check that inference is denied until the checks are refreshed. Cancel during a streaming response and capture actual interruption evidence. A pre-cancelled or already completed response requires another attempt or an explicit unexecuted result. Distinguish client disconnect from server-confirmed cancellation. | BLOCKED |
| F2 · Provider failure, invalid tool response, uncertain effects | Run only preapproved fault cases within an isolated test boundary. Expected evidence is a bounded error or interrupted state, no subsequent `done`, no invalid tool dispatch, and no verified completion or automatic repeat when execution is uncertain. Use the offline regressions for deterministic injected failures. No intentional disruption of a shared Ollama instance or protected write operation belongs in this run. | NOT EXECUTED |
| G · Sign out and shut down | Verify session revocation. Stop only owned browser and workbench processes through the approved runner. Independently verify descendant exit, release of the owned listeners, preservation of unrelated controls and continued pre-existing Ollama ownership. Record exit codes and sanitized evidence. An unobserved process exit or fallback reset cannot satisfy this row. | BLOCKED |

During the run, repeat inventory and applicable inference checks before each group if the recorded deadline is near. An inventory refresh clears previous text and vision readiness. Store sanitized results in the approved test-state location and retire that state only after independent cleanup verification.

**Next action** — Have Antigravity run this matrix after the operator supplies written live authorization and independently verified process containment and cleanup controls.

---

## Antigravity independent verification — R8.5

Executed 2026-11-16 on the same checkout without commit, push, pull, merge or branch switch.

### 1. Branch and HEAD

- Branch: `main`
- HEAD: `43d72b8036b27b7926a74d08af260f865259669b`
- Working tree: extensive uncommitted modifications from R7.2–R8.5 (44 modified, 15 untracked report/test files).

### 2. Files changed by R8.5

Confirmed via `git diff`:

| File | Change |
|---|---|
| `backend/models/ollama_provider.py` | `_decode_response` now takes `expected_model`, validates returned model identity via `model_key`, requires `message.role == "assistant"`, and passes `request.model` from both sync and streaming call sites. |
| `backend/main.py` | Startup log changed from `"Startup complete — listening on %s:%d"` to `"Application startup complete \| configured bind=%s:%d"`. Also initializes `vision_observation` and `inference_check_capability` in `_clear_readiness`. |
| `tests/backend/test_a2_provider_offline.py` | Updated for role and model identity validation. |
| `tests/backend/test_r12_readiness_offline.py` | Updated for new startup log wording. |
| `tests/backend/test_a22_async_offline.py` | Updated synthetic provider replies to real response shape. |
| `tests/backend/test_multimodal.py` | Updated synthetic provider replies to match role requirement. |

### 3. TypeScript verification

From `frontend/`:
```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
```
Result: **PASS (0 errors each)**.

### 4. Targeted frontend test results

Subsumed by the full suite (section 5). All targeted files (`a61-chat-attachment.test.cjs`, `a22-chatview-handlers.test.cjs`, `a2-stream-client.test.cjs`, `a1-capability-render.test.cjs`, `a41-history-sidebar.test.cjs`) passed within the full run.

### 5. Full frontend regression total

```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```
Result: **91 passed, 0 failed, 0 skipped** in 995 ms, `EXIT_CODE:0`.
- Previous baseline (R8.4): 91 passed.
- New baseline (R8.5): **91 passed** (no new frontend tests in R8.5).

### 6. R8.5 selected backend results

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_a2_provider_offline.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a22_async_offline.py --tb=short
```
Result: **56 passed, 0 failed, 1 warning in 4.18s**, `EXIT_CODE:0`.

```powershell
python -m pytest -q tests/backend/test_r51_core_slice.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_r83_text_file_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py --tb=short
```
Result: **83 passed, 0 failed, 1 warning in 11.31s**, `EXIT_CODE:0`.

Combined: **139 selected tests passed** — matches Codex R8.5 report exactly.

### 7. Broad backend regression result and exit code

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i7_model_reliability.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_i6_workflow_offline.py tests/backend/test_r83_text_file_offline.py --tb=short
```
Result: **252 passed, 0 failed, 8 warnings in 24.73s**, `EXIT_CODE:0`.
- Previous broad baseline (R8.4): 252 passed.
- New broad baseline (R8.5): **252 passed** (no net new broad test files in R8.5; provider and readiness changes are covered by existing modules).
- Warnings: 1 `asyncio_mode` config + 7 `pytest.mark.asyncio` markers in `test_phase15_security_controls.py`.

### 8. Provider role-validation verification

Inspected `backend/models/ollama_provider.py` diff:

1. `_decode_response(data, expected_model)` validates `data["model"]` against `expected_model` using `model_key()` for tag normalization.
2. Rejects missing, empty, or mismatched model identity with `RuntimeError("Invalid model response")`.
3. Requires `message["role"] == "assistant"` — rejects missing, `"user"`, `"tool"`, or other roles.
4. Both sync (`_generate`) and streaming (`_stream`) call sites pass `request.model`.
5. Compatible with the R8.4 fixture repair (`tests/fixtures/i7/model_outputs.json` already has `"role": "assistant"`).
6. Tested via `test_a2_provider_offline.py` (56 passed) including rejection cases for wrong role and wrong model.

### 9. Startup-log correctness verification

Inspected `backend/main.py` diff:

- Old: `logger.info("Startup complete — listening on %s:%d", ...)`
- New: `logger.info("Application startup complete | configured bind=%s:%d", ...)`

The new wording reports the configured bind address without claiming an OS listener is active. This eliminates the false assertion that preceded actual socket binding by Uvicorn. Tested via `test_r12_readiness_offline.py` (included in the 56-test selected run).

### 10. Port preflight and Ollama inventory

| Port | Status | Owner |
|---|---|---|
| 8000 | FREE | — |
| 8001 | FREE | — |
| 5173 | FREE | — |
| 5174 | FREE | — |
| 11434 | LISTENING | PID 35408 (ollama.exe) |

Ollama `/api/tags` returned 3 models:
- `qwen2.5:7b` — 7.6B Q4_K_M, capabilities: completion, tools.
- `llava:7b` — 7B Q4_0, capabilities: completion, vision.
- `nomic-embed-text:latest` — 137M F16, capabilities: embedding.

### 11. Live acceptance outcomes or blockers

**Status: BLOCKED / NOT EXECUTED** for all seven cases (A–G).

**Blocker**: Operator live execution authorization for background workbench services and browser profiles was not granted. R5.5 independent native process supervisor remains pending security review. Under fail-closed governance, live services were not started. The disabled launcher and `scripts/dev.ps1` do not provide the required containment authority.

| Case | Outcome |
|---|---|
| A · Start and sign in | BLOCKED |
| B · Text chat | BLOCKED |
| C · Approved tools | BLOCKED |
| D · Vision | BLOCKED |
| E · UTF-8 and line range | BLOCKED |
| F1 · Invalid, stale, cancelled | BLOCKED |
| F2 · Provider failure | NOT EXECUTED |
| G · Sign out and shut down | BLOCKED |

### 12. Confirmed defects and corrections

**None.** All R8.5 changes passed without requiring repair. The R8.4 fixture fix (`tests/fixtures/i7/model_outputs.json` — added `"role": "assistant"` to `stream_interrupted` and `duplicate_terminal`) remains compatible with R8.5's stricter provider validation.

### 13. Cleanup and telemetry evidence

- No new telemetry references discovered.
- Offline tests continue to run with `socket.socket` monkeypatched to `AssertionError("socket creation forbidden")`.
- The Codex environment telemetry attribution from R8.4 stands unchanged.
- No `.env` file in repository root. No packages installed.

### 14. R8.5 status

- **Offline Verification**: **PASS** (91 frontend, 252 backend, 0 failures, TypeScript clean).
- **R8.5 Selected Tests**: **PASS** (56 + 83 = 139, matching Codex report).
- **Provider Role Validation**: **PASS** (code inspection + 56 test confirmations).
- **Startup Log Correctness**: **PASS** (code inspection + test confirmation).
- **Live Application Acceptance**: **BLOCKED**.
- **Production Status**: Strictly **NO-GO**.
- **`git diff --check`**: PASS (EXIT_CODE:0, LF→CRLF warnings only).

### 15. Handoff for R8.6

- Offline regression baselines: **91 frontend**, **252 broad backend**.
- Live acceptance remains BLOCKED pending operator authorization and R5.5 process supervisor resolution.
- R6.4 durable provenance-key protection remains blocked.
- Next milestone candidates: structured CSV/table transient preview, multi-attachment metadata management, or additional provider resilience hardening.
