# R7.3 Ollama integration readiness

**Scoped offline backend verification passed. R7.3 remains PARTIAL and production remains NO-GO.** Starting branch `main`, HEAD `43d72b8036b27b7926a74d08af260f865259669b`. The R7.2 `RolePortalView.tsx` correction and report were preserved. R7.2's reported TypeScript passes and 59 frontend tests predate this milestone's frontend changes.

| Confirmed issue | Evidence and correction |
| --- | --- |
| Composer helper accepted inventory alone when the selected-model record was absent | `ChatView.tsx` calls `chatDisabled()` for both the composer and send handler. The parsed API normally rejects missing selected-model evidence and the backend also checks readiness. The helper could still return enabled for an inventory-only report with generic chat availability, and the tests expected that bypass. `frontend/src/api/capabilities.ts` now requires a matching, available record with a finite future expiry. The separate `modelInInventory()` tests retain `:latest` normalization and alternate selection. The model status text describes the backend check without implying that the full application workflow was verified. |
| Generation accepted an Ollama reply labeled with a different model | `backend/models/ollama_provider.py` previously returned the request's model name without validating the provider's reported `model`. The decoder now requires a matching nonempty model name in both buffered replies and every streamed record before accepting text or completion. Untagged names still match their implicit `:latest` form. A mismatch after partial text produces an error, no successful `done`, and no mismatched text in history. This validates a provider-reported name. Digest evidence comes separately from inventory and is not attached to each answer. |
| Disconnect test expected the pre-R6.3 history behavior | The ASGI fixture in `tests/backend/test_a22_async_offline.py` now requires a closed provider and one interrupted record matching the attempted delta, with no successful terminal event. The failed transport does not establish receipt of that delta by the client. |

Files changed for R7.3 are `backend/models/ollama_provider.py`, `frontend/src/api/capabilities.ts`, `frontend/tests/a1-capability-render.test.cjs`, `tests/backend/test_a2_provider_offline.py`, `tests/backend/test_a22_async_offline.py` and this report.

## Provider and startup contract

`python -m backend` validates settings, then uses `backend.main:app` and the configured Uvicorn bind. `LOCAL_CHAT_ONLY=true` is accepted only for authenticated development, a numeric loopback backend bind, no auth override and validated plain HTTP loopback Ollama. The lifespan initializes auth, the SQLite conversation registry/task store and JSON conversation memory, planner, and tool registry. It substitutes unavailable adapters for the Chroma vector store and embedding client. Its exit stack closes acquired resources, including a previously acquired router when later startup fails. Readiness starts empty and is cleared on shutdown.

An installed Ollama executable establishes only binary presence. A reachable server can return `GET /api/tags`, which records inventory and optional provider-reported digests. The selected name must uniquely match a configured chat-capable model in `config/models.yaml`. An authenticated explicit check sends a bounded synthetic `POST /api/chat` to Ollama with `stream:false`, a short `Reply with OK.` prompt, an eight-token limit and immediate unload. It accepts a nonempty assistant answer with matching model and successful termination. This can fail on slow cold loads or reasoning-heavy models. Backend readiness is process-local, bound to endpoint, model and configured metadata, invalidated by inventory rescans and digest/configuration changes, and limited by the shorter remaining inventory/inference TTL (each 60 seconds). The chat route checks it again before starting a stream. A provider outage or unobserved model replacement can still occur after a check. A fresh inventory scan and new inference check are required after changing the endpoint or replacing a model.

Actual buffered and streamed chat use Ollama `POST /api/chat` with role/content messages, the routed model and `stream:false` or `stream:true`. The streaming adapter consumes newline-delimited JSON, requires a `done` response and rejects reported non-`stop` reasons. The existing compatibility behavior allows an omitted `done_reason`. Provider HTTP errors, timeouts or EOF without completion remain failure paths. Both Ollama's [chat response contract](https://docs.ollama.com/api/chat) and [streaming format](https://docs.ollama.com/api/streaming) were checked against the implementation. SovereignAI converts the result to SSE and uses its own registered tool dispatch, authorization and verified numeric receipts. Real streaming success requires observed deltas and a terminal `done`. Backend startup, inventory, a successful readiness probe and a real streamed answer are distinct results.

## Offline verification

From the repository root, this command used deterministic providers, HTTPX mock transport, in-process ASGI and temporary stores. The included `run_offline` fixtures forbid socket creation.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py
git diff --check
```

Final result **131 passed, 0 failed, 0 skipped, 1 warning in 24.08 seconds**. The warning is pytest's unknown `asyncio_mode` option with plugin autoload disabled. The first run had 130 passes, one failure from the stale disconnect expectation, and three additional async-generator finalizer warnings in the new HTTPX fixture on the socket-free event loop. The fixture now exercises HTTPX's explicit EOF flush for the last record, while the existing explicit-close and cancellation fixtures independently exercise early closure. The final run had no finalizer warnings. The HTTPX mock asserts request serialization and parses split NDJSON bytes. Route and engine tests cover the 42 → 126 → 131 sequence, `5000 m`, ordinary chat, provider errors, readiness expiry and identity mismatch, approval denial, authenticated ownership, task interruptions, history and protected gates. Receipts apply to the verified conversation in the current process. A disconnect after dispatch does not establish that a tool never ran.

## Antigravity operator handoff

Scope A is limited to the previously authorized local offline frontend commands, if that authorization covers the current snapshot. From its `frontend` directory, confirm the previously reviewed Node/TypeScript paths and execute:

```powershell
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a1-capability-render.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a22-chatview-handlers.test.cjs
```

Collect the commands, snapshot, exit codes and counts. Verify that inventory accepts alternate/implicit tags without enabling chat, that missing, expired, invalid or unrelated readiness disables sending, and that a matching fresh inference result enables a selected installed model. If live startup is blocked, finish this permitted scope and report the exact blocker.

Scope B requires explicit separate authorization for the live backend process, use of the actual Ollama listener and model inference, loopback requests and cancellation. Use an operator-approved environment with reviewed offline binaries, dependencies and model artifacts, monitored egress restrictions, an independent deadline and verified process-tree/listener ownership and cleanup. The unverified R5.5 supervisor and disabled `scripts/dev.ps1` cannot supply that evidence. If the operator cannot establish containment, record **BLOCKED**. Keep Ollama's already running authorized service separate from the owned backend test process. If no approved Ollama service exists, its startup needs separate approval and an ownership plan. `ollama --version` and `ollama list` are the inventory commands used by `scripts/setup.ps1`. Run only these approved probes, rather than the setup script, which has broader effects. Compare `qwen2.5:7b` and its digest from the approved listener's `GET http://127.0.0.1:11434/api/tags` against the reviewed model inventory. The configured alternate is `llava:7b` if approved and present. `nomic-embed-text` is unnecessary for this test. Record returned identity evidence rather than assuming reported installations are current.

From the **root of the approved repository snapshot**, apply the following process-scoped configuration inside the reviewed runner. Use a new writable test-state directory and a matched task DB/history pair. Use a reviewed `.env` or no `.env` in the snapshot. In particular `ENVIRONMENT` is an alias that must agree with `APP_ENV`. The existing interpreter path comes from `scripts/dev.ps1` and was absent in the Codex workspace. Use that path only if verified on the operator's instance, or record the operator-approved absolute interpreter path for the same entry point. The launch is permitted only after the live authorization and process gates above pass.

```powershell
$state = Join-Path $env:TEMP ('sovereign-r73-' + [guid]::NewGuid().ToString('N'))
$env:APP_ENV = 'development'
$env:ENVIRONMENT = 'development'
$env:LOCAL_CHAT_ONLY = 'true'
$env:AUTH_ENABLED = 'true'
$env:AUTH_DEV_OVERRIDE = 'false'
$env:ALLOW_DEV_CLEARANCE_SIMULATION = 'false'
$env:BACKEND_HOST = '127.0.0.1'
$env:BACKEND_PORT = '8000'
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
$env:AUTH_COOKIE_SECURE = 'false' # Disposable plain HTTP loopback only
$env:AUTH_COOKIE_SAMESITE = 'lax'
$env:TASKS_DIR = Join-Path $state 'tasks'
$env:TASKS_DB_PATH = Join-Path $env:TASKS_DIR 'tasks.db'
$env:CONVERSATION_MEMORY_PATH = Join-Path $state 'memory.json'
$env:UPLOAD_DIR = Join-Path $state 'uploads'
$env:LOG_DIR = Join-Path $state 'logs'
$env:SANDBOX_DIR = Join-Path $state 'sandbox'
$env:CHROMA_PERSIST_DIR = Join-Path $state 'chroma'
& .\backend\venv\Scripts\python.exe -m backend
```

No reload or multiworker mode. Before sending credentials or prompts, confirm the backend listener is owned by the test process at `127.0.0.1:8000` and Ollama's approved listener matches `127.0.0.1:11434`. Confirm the `Document vector store unavailable in local chat-only profile` startup message, no Chroma directory at `$env:CHROMA_PERSIST_DIR` in the test process's state root, and available backend readiness at `GET /api/health/ready`. Use an isolated `operator` account with read-tool and view permissions. On an empty test DB the app provisions a temporary administrator and requires a password change. In a private console, authenticate via `POST /api/auth/login` with `username` and `password`, use authenticated `POST /api/auth/change-password` with `current_password` and `new_password`, then `POST /api/auth/users` with `username`, `password` and `role:"operator"` if provisioning is needed. Authenticate as the operator for testing. Keep bootstrap output, tokens and credentials out of shared logs. The browser and Vite are optional for the backend workflow and require separate approval. The existing development command from `frontend` is `npm run dev -- --host 127.0.0.1 --strictPort`, serving port 5173 with `/api` proxied to `http://localhost:8000`. Do not use the combined launcher. The denied production build remains out of scope.

For API-only verification, use PowerShell 7 from a separate approved console. The following authenticates the test operator in memory, makes the explicit checks and creates an owned conversation. Launch the backend in its own operator-controlled process first. Use the prompts below only after a successful selected-model check. Bound HTTP and stream reads inside the independent operator deadline.

```powershell
$api = 'http://127.0.0.1:8000/api'
$model = 'ollama/qwen2.5:7b'
$cred = Get-Credential -Message 'Disposable SovereignAI operator account'
$loginBody = @{ username = $cred.UserName; password = $cred.GetNetworkCredential().Password } | ConvertTo-Json -Compress
$login = Invoke-RestMethod -Method Post -Uri "$api/auth/login" -ContentType 'application/json' -Body $loginBody -NoProxy -MaximumRedirection 0 -TimeoutSec 15
Remove-Variable cred,loginBody
$auth = @{ Authorization = "Bearer $($login.token)" }
Remove-Variable login
$selection = [uri]::EscapeDataString($model)
$inventory = Invoke-RestMethod -Method Post -Uri "$api/health/capabilities/ollama-check?model=$selection" -Headers $auth -NoProxy -MaximumRedirection 0 -TimeoutSec 10
$inventory | Select-Object installed_models,chat_models,selected_chat_model
$check = Invoke-RestMethod -Method Post -Uri "$api/health/capabilities/inference-check" -Headers $auth -ContentType 'application/json' -Body (@{ model = $model } | ConvertTo-Json -Compress) -NoProxy -MaximumRedirection 0 -TimeoutSec 35
$check.selected_chat_model
if ($check.selected_chat_model.id -ne $model -or $check.selected_chat_model.state -ne 'available') { throw 'Model inference not ready' }
$conversation = Invoke-RestMethod -Method Post -Uri "$api/chat/sessions" -Headers $auth -NoProxy -MaximumRedirection 0 -TimeoutSec 10
```

Use this streaming request helper in the same console. It reads events as they arrive and disposes the response on success, failure or simulated caller interruption. `-StopAfterDelta` detaches after the first observed delta. Record only synthetic output and sanitize conversation identifiers before sharing evidence.

```powershell
function Send-R73Chat([string]$Message, [bool]$Tools = $true, [switch]$StopAfterDelta) {
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $http = [System.Net.Http.HttpClient]::new($handler)
    $deadline = [System.Threading.CancellationTokenSource]::new([TimeSpan]::FromSeconds(135))
    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$api/chat")
    $body = @{ session_id = $conversation.session_id; model = $model; message = $Message; stream = $true; tools_enabled = $Tools } | ConvertTo-Json -Compress
    $request.Headers.Add('Authorization', $auth.Authorization)
    $request.Content = [System.Net.Http.StringContent]::new($body, [System.Text.Encoding]::UTF8, 'application/json')
    try {
        $response = $http.SendAsync($request, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead, $deadline.Token).GetAwaiter().GetResult()
        try {
            "HTTP $([int]$response.StatusCode)"
            if (-not $response.IsSuccessStatusCode) { return }
            $reader = [System.IO.StreamReader]::new($response.Content.ReadAsStream())
            try {
                while ($null -ne ($line = $reader.ReadLineAsync($deadline.Token).AsTask().GetAwaiter().GetResult())) {
                    if (-not $line.StartsWith('data: ')) { continue }
                    $event = $line.Substring(6) | ConvertFrom-Json
                    $line
                    if ($StopAfterDelta -and $event.type -eq 'delta') { return }
                }
            } finally { $reader.Dispose() }
        } finally { $response.Dispose() }
    } finally { $request.Dispose(); $deadline.Dispose(); $http.Dispose() }
}

# Refresh inventory then inference promptly if either 60-second window expires.
Send-R73Chat 'Hello. What can you help me with?' $false
Send-R73Chat 'What is 25 + 17?'
Send-R73Chat 'Multiply that result by 3.'
Send-R73Chat 'Add 5 to that result.'
Send-R73Chat 'Convert 5 kilometres to metres.'
Invoke-RestMethod -Uri "$api/chat/sessions/$($conversation.session_id)" -Headers $auth -NoProxy -MaximumRedirection 0 -TimeoutSec 10
```

Required live evidence and negative checks:

1. **Readiness and authentication.** Before the inventory/inference calls, test an unauthenticated inference request (401) and an authenticated chat request against an owned conversation (503 with no provider execution). Search for the configured default in inventory, then check its exact ID. Record `state`, `reason`, `expires_at` and `identity_evidence`. A listed alternate alone must not unlock chat. Set the request model to `ollama/r73-missing:latest` and expect inference-check 409 and chat 503. Restore the default. After both readiness windows expire, repeat authenticated chat and expect 503. Refresh both checks to continue. For real missing **configured** model coverage, use an already-absent configured alternate if available in the approved test environment and record `configured_model_missing`. Do not remove an installed model for this case.
2. **Chat, tools and history.** Record the ordinary chat deltas and one `done`, with no tool event. For the four tool prompts, record `tool_result`, tool name, success and numeric value **42 → 126 → 131**, then **5000 m**. Check `task_completed` and `done` where the route emits them. Text alone is insufficient. Keep the turns in the same owned conversation and backend process. Record the server-owned history by reopening it or reissuing the history GET. Confirm user turns are present without duplicates, check the plain assistant reply in the same process, and accept appropriately withheld tool/source-derived text. Confirm a second authorized test user cannot fetch the same conversation (404). Restart readability remains subject to R6.4.
3. **Interruption.** After refreshing readiness as needed, run `Send-R73Chat 'Explain how water moves in a pump in 12 numbered paragraphs.' $false -StopAfterDelta` in a separate test conversation. Record that the caller detached after a delta and did not observe `done`. Inspect history and sanitized process logs. If the application observed cancellation before completion, expect an interrupted record rather than success. If generation already finished, record the race and classify interruption as inconclusive. Do not infer tool rollback or provider-side cancellation from client disposal. Provider shutdown or fault injection needs separate explicit approval and may not target a pre-existing unrelated service.
4. **Cleanup.** Log out via authenticated `POST /api/auth/logout`, clear the in-memory token, and stop the owned backend gracefully through the approved runner. Use its independently enforced deadline and authorized cleanup for timeout. Confirm the owned listener and process tree are gone and no unexpected network traffic or Chroma listener appeared. Preserve unrelated processes and any pre-existing Ollama instance. Report exit codes, observed listener ownership, model/version and digest evidence, request/SSE results, timeouts, cleanup outcome and any omissions. Redact credentials and retain only synthetic logs. Failure to establish process attribution or cleanup leaves the live scope unverified.

Codex did not execute any live run, Ollama process, browser, frontend test/build or VM. Authentication, tool approval, protected storage, RAG, artifact publication and R6.4 provenance controls remain in force. The backend `aclose()` checks and synthetic transport tests do not establish native Windows containment or shutdown behavior.

**Next action.** Give this report and the R7.3 diff to Antigravity for the scoped checks, with live execution conditional on the separate authorization and process controls above.

---

## Antigravity local verification evidence

### 1. Starting branch and HEAD
- Branch: `main`
- Starting HEAD: `43d72b8036b27b7926a74d08af260f865259669b`
- Working tree: Clean diffs preserved without git checkout, pull, or stash.

### 2. Files modified in working tree
- `backend/models/ollama_provider.py`: Validates model identity in streaming/buffered response chunks.
- `frontend/src/api/capabilities.ts`: Enforces server-confirmed fresh readiness for chat composer while permitting inventory selection.
- `frontend/src/components/auth/RolePortalView.tsx`: Strict null safety fix preserved from R7.2.
- `frontend/tests/a1-capability-render.test.cjs`: Updated readiness assertions and expiry scenarios.
- `tests/backend/test_a2_provider_offline.py`: Unit tests for model identity validation and truncated completion rejection.
- `tests/backend/test_a22_async_offline.py`: ASGI transport fixture for consumer disconnect with partial delta.
- `RELEASE_R7_2_FRONTEND_VERIFICATION_REPORT.md`: R7.2 verification documentation.
- `RELEASE_R7_3_OLLAMA_INTEGRATION_REPORT.md`: This report.

### 3. Exact TypeScript commands and results
Working directory: `frontend`
- App project check:
  ```powershell
  & 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
  ```
  **Result**: Exit code `0` (0 errors, 0 disk writes).
- Node/Vite config check:
  ```powershell
  & 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
  ```
  **Result**: Exit code `0` (0 errors, 0 disk writes).

### 4. Frontend regression test counts
Command:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```
**Result**: **59 passed, 0 failed, 0 skipped** across all 13 test suites (1.42s).
- `tests/a1-capability-render.test.cjs`: 5 passed.
- `tests/a2-stream-client.test.cjs`: 6 passed.
- `tests/a22-chatview-handlers.test.cjs`: 18 passed.
- `tests/a41-history-render.test.cjs`: 3 passed.
- `tests/a41-history-sidebar.test.cjs`: 7 passed.
- `tests/a51-documents-render.test.cjs`: 3 passed.
- `tests/a52-source-render.test.cjs`: 2 passed.
- `tests/a61-chat-attachment.test.cjs`: 4 passed.
- `tests/a82-artifact-presentation.test.cjs`: 1 passed.
- `tests/a91-access-render.test.cjs`: 1 passed.
- `tests/historyRequests.test.mjs`: 6 passed.
- `tests/i2-logout-client.test.cjs`: 1 passed.
- `tests/i91-document-client.test.cjs`: 2 passed.

### 5. Backend regression test counts
Command:
```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py
```
**Result**: **131 passed, 0 failed, 0 skipped, 1 warning in 16.98s**.
- Warning: PytestConfigWarning for `asyncio_mode` with plugin autoload disabled.
- Model identity matching, rejection of mismatched models before and after partial deltas, absence of `done` after mismatch, and disconnect handling all verified.

### 6. Readiness behavior
Verified through `a1-capability-render.test.cjs`, `a22-chatview-handlers.test.cjs`, and backend health test fixtures:
- An installed model without inference readiness is selectable in inventory but cannot send chat messages.
- Fresh server-confirmed model readiness enables chat when all other capabilities permit.
- Expired readiness (`expires_at <= Date.now() / 1000`), non-finite expiry, missing selected model, or mismatched model IDs reliably lock the composer and disable sending.
- `:latest` model name normalization conforms to backend inventory matching.

### 7. Ollama inventory and endpoint evidence
Read-only preflight queries against local host:
- Local installation observed: `<LOCALAPPDATA>\Programs\Ollama\ollama.exe` (Version: 0.0.0.0 per file metadata).
- Active Ollama process: PID `35408` (`ollama.exe`), PID `20140` (`ollama app.exe`).
- Listening endpoint: `127.0.0.1:11434` (Owned by PID 35408).
- Query `GET http://127.0.0.1:11434/api/tags` returned:
  - `qwen2.5:7b`: 4.68 GB; digest `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`; capabilities: `completion`, `tools`. Matches configured default chat model.
  - `llava:7b`: 4.73 GB; digest `8dd30f6b0cb19f555f2c7a7ebda861449ea2cc76bf1f44e262931f45fc81d081`; capabilities: `completion`, `vision`. Matches configured multimodal fallback model.
  - `nomic-embed-text:latest`: 274 MB; digest `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`; capabilities: `embedding`.

### 8. Live chat-only tests executed or blocked
**BLOCKED.** Live backend service launch was blocked due to:
- **Port 8000 Conflict**: TCP port `8000` is currently occupied by an active external development process (PID `31576`, running `Automata_26_ApexCode\.venv\Scripts\python.exe -m uvicorn app.main:app`). Preserving unrelated processes is strictly required; this process was not terminated.
- **Separate Live Authorization**: Live backend process spawning and live loopback model inference require explicit operator authorization and an isolated supervised execution context with clean process-tree termination.

### 9. Actual defects corrected
Zero new regressions found on the R7.3 snapshot. The prior R7.2 `RolePortalView.tsx` fix and Codex's R7.3 fixes in `ollama_provider.py` and `capabilities.ts` operate cleanly together without defect.

### 10. Startup and shutdown evidence
- No live backend processes were launched; zero process leaks.
- Pre-existing Ollama service (PID 35408) and unrelated Uvicorn process (PID 31576) were left undisturbed.
- All temporary test stores and ASGI test connections were closed within their respective in-memory pytest/node test runners.

### 11. Unverified or blocked behaviors
- Live HTTP/SSE network transport between frontend and backend.
- Live real-time token streaming from local Ollama under concurrent load.
- Durable restart recovery across hard backend termination (blocked by R6.4).
- Production build and production deployment (NO-GO).

### 12. R7.3 status
**R7.3: PARTIAL.**
- Offline Frontend Verification: **PASS** (TypeScript 0 errors, 59/59 tests passed).
- Offline Backend Verification: **PASS** (131/131 tests passed).
- Live Ollama Integration: **BLOCKED** by port 8000 collision and missing live process authorization.
- Production: **NO-GO**.

### 13. Exact next integration handoff for Codex
1. The codebase is verified healthy (0 TypeScript errors, 59 frontend passes, 131 backend passes).
2. For the live integration milestone:
   - Clear the port 8000 listener conflict (or configure `BACKEND_PORT` to an available port such as `8001`).
   - Grant explicit authorization for launching the isolated chat-only backend process under `LOCAL_CHAT_ONLY=true`.
   - Run the documented live verification sequence (readiness probe -> ordinary chat -> math tools 42 -> 126 -> 131 -> unit conversion 5000 m -> clean shutdown).
