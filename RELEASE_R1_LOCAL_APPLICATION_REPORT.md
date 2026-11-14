# R1.1 local application integration

**R1.1: PARTIAL. R1: PARTIAL. Production: NO-GO.** The requested end-to-end usable local slice is not established. Synthetic route coverage exists, but the real application has not been started or exercised and the current capability contract keeps chat locked even after an inventory check.

## Existing architecture and local contract

FastAPI registers authentication, chat, model, health and other routers. Lifespan validates configuration, creates directories, checks the conversation registry, initializes the SQLite-backed auth/session and audit services, initializes an administrator if needed, constructs the model router and conversation memory, and then initializes embedding, vector and agent components. On successful startup it marks readiness and closes the model and embedding HTTP clients on shutdown. Startup initializes the vector store: a chat-only startup without Chroma has not been established. The first-run administrator password is printed to the local console and requires a password change; protect that console and never capture it in reports. Startup failure cleanup before the lifespan yield is not comprehensively verified.

The intended minimum configuration, using existing settings, is `APP_ENV=development`, `DEV_MODE=false`, `AUTH_ENABLED=true`, `AUTH_DEV_OVERRIDE=false`, `BACKEND_HOST=127.0.0.1`, an explicit loopback `OLLAMA_BASE_URL=http://127.0.0.1:11434`, and a CORS origin restricted to the actual loopback frontend. Configure installed model IDs and capabilities in `config/models.yaml`; the YAML default takes precedence over the settings fallback. This is a proposed local configuration contract, not an approved startup command or a supported production profile. Bind the frontend to loopback too. Authentication and role/tool permissions remain server-side; no new login mode is introduced. The environment alone does not guarantee a loopback bind if an external server overrides it, and the application does not enforce this proposed profile as a unit. Do not enable Protected profile or protected storage. The disabled launcher remains disabled.

The authenticated UI creates server-issued conversation IDs, streams through the existing chat route, and reads owned history. Deterministic numeric plans use existing authorization, actual calculator/conversion tools, task completion and process-local receipts. Receipts do not survive restart; non-streaming follow-ups remain fail-closed. Server capabilities withhold document RAG, artifacts, generated code and approval resume.

## Findings and changes

- Configured Ollama model inventory now matches implicit `:latest` tags consistently with capability checks, without matching a model from another provider. `/api/models` derives both response sections from one provider snapshot.
- Model preload previously returned `warmed: true` even on an HTTP failure. It now returns its existing sanitized warning on non-success status. This does not prove the model actually loaded after a successful HTTP response.
- Production configuration now rejects `AUTH_DEV_OVERRIDE=true` even when authentication is enabled. This does not change organizational defaults or enable an override.
- Capability reporting deliberately returns `unknown/prerequisites_observed` for chat after inventory is observed, while the composer requires `available`. Thus the production UI cannot currently send a chat on that evidence alone. No tested inference-based capability transition exists; changing unknown to available on inventory alone would overstate readiness. The selector also exposes installed models without proving they support chat. These are unresolved integration requirements.
- Existing SSE and simulated UI tests cover terminal/error handling, cancellation, verified result presentation, server IDs and logout failures. They do not connect a mounted React UI to the ASGI route or establish real model compatibility.

## Synthetic verification

The production streaming route and fake provider exercise the authorized 42 → 126 → 131 sequence and further arithmetic/conversion, task completion, owner isolation, receipt expiry/deletion/revocation and interruption. Existing production-route tests also cover ordinary chat and a calculator-to-conversion workflow yielding 5000 m. This is not the exact standalone natural-language conversion scenario against a live model. Provider fixtures cover completion, truncated or malformed streams, 404 and timeout; broad real HTTP cancellation and model capability mismatches remain unverified. The inventory test uses injected data, not Ollama. Document and artifact boundary tests remain fail-closed.

Commands run from repository root with `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'`:

```powershell
python -m pytest -q tests/backend/test_r11_model_inventory.py tests/backend/test_a2_provider_offline.py tests/backend/test_i7_model_reliability.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_i6_workflow_offline.py tests/backend/test_i4_workflow_offline.py tests/backend/test_a1_capabilities_offline.py tests/backend/test_a41_history_offline.py tests/backend/test_i101_artifact_boundary.py
python -m pytest -q tests/backend/test_r11_model_inventory.py
python -m pytest -q tests/backend/test_a33_production_tools_offline.py tests/backend/test_a31_planning_offline.py tests/backend/test_a1_health_asgi.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_a91_admin_offline.py
```

Results respectively: **61 passed**, **4 passed**, **40 passed**, each with one pytest warning about the unknown `asyncio_mode` option with plugin autoload disabled. The first run preceded the two final tests and production validation change; the second verifies those changes. Tests use temporary storage, synthetic users/providers and in-process harnesses, not real startup or services.

From `frontend`:

```powershell
node --test tests/a22-chatview-handlers.test.cjs tests/a2-stream-client.test.cjs tests/a1-capability-render.test.cjs tests/i2-logout-client.test.cjs
```

**27 passed**; mocked fetch/simulated hooks and rendering, not a mounted browser. No TypeScript or frontend build was run.

## Remaining release gates and next task

The previous automatic approval review rejected frontend build continuation because of possible registry access. A separate explicit approval and reviewed offline dependency/build execution scope are needed before that build; no equivalent path was run here. The Windows asyncio socketpair issue and native process ownership remain unverified. No policy or event-loop workaround was applied. Real startup, Ollama inference, model selection, browser behavior and shutdown require separately authorized, bounded verification with known local dependencies and process lifecycle controls. Existing startup initializes Chroma, so a real startup also needs explicit authorization for that dependency or a separately implemented and verified chat-only startup path. No services, models or dependencies were started/downloaded here.

Next implement a safe, bounded, explicitly invoked model readiness check and a capability contract that distinguishes observed inventory from verified inference, filters selectable chat-capable models, and has production-route and frontend tests. Resolve startup dependency isolation and resource cleanup before asking for live startup verification. Preserve protected storage, publication, code execution, approval and authorization gates. No commit, merge or push was made.

## R1.2 — Model readiness and explicit chat-only startup

**R1.2: PARTIAL. R1: PARTIAL. Production: NO-GO.** A model inventory was previously insufficient to unlock chat, as intended, but no supported inference check could supply the missing evidence. A bounded check and an explicit chat-only startup path now exist. Frontend execution, live startup and live inference are not verified, and startup-failure cleanup remains unproven.

### Readiness contract and probe

`POST /api/health/capabilities/ollama-check` remains an authenticated, explicitly requested, bounded inventory observation. Inventory alone reports an installed configured chat model as `unknown/inference_not_checked`. The new authenticated `POST /api/health/capabilities/inference-check` accepts a model ID and is permitted only for one installed, explicitly configured Ollama chat model on a validated HTTP loopback endpoint. It sends a short fixed synthetic prompt to `/api/chat`, with no stored conversation or sensitive prompt, and requires a bounded valid JSON response, the tested model identity, an assistant message with nonempty text, `done: true`, no error/tool call and a non-truncated completion reason. It applies a 30-second total deadline, a 16 KiB response limit, no redirects, no proxy environment, and context-managed response/client cleanup. No live probe was run.

A check invalidates prior evidence before it begins. One inference check at a time is allowed per process; duplicate or concurrent requests return 409. The record binds the normalized model/tag, normalized endpoint and configured model metadata. A process-local record expires after 60 seconds or earlier when inventory expires. Inventory refresh invalidates inference evidence, including late in-flight results; failed, interrupted, cancelled, changed-configuration, unsupported and stale checks do not grant readiness. This cache is neither durable nor shared across processes. No immutable model digest or version identity is available, so replacement of weights under an unchanged tag between checks cannot be detected; this is a material limitation. An explicit check tests text chat only, not image inference, model quality, general tool-following, isolation or deployment safety.

The passive capability endpoint accepts the selected model as a query and reports its status, selected-model identity, eligible configured chat models and an expiry timestamp. Existing capability states plus reasons distinguish unsupported/missing, unchecked, checking, verified, failed and expired. Backend startup status and inventory must also be current. The production text chat route checks the same server-owned readiness before executing; authorization, ownership and tool-dispatch checks remain separate and unchanged. Chat readiness is not a grant to use any tool. A successful provider HTTP status or model preload alone does not qualify.

The provider now uses the same canonical numeric loopback target as the probe, without proxy environment, so a `localhost` alias does not make the probe and actual inference use different host resolution. A cached provider whose endpoint no longer matches is rejected by readiness. Non-loopback provider configuration is not granted this local readiness.

### Frontend and startup

The model inventory UI now shows installed models configured for chat, offers an explicit inference check, presents its reason, and requires a matching, unexpired server-selected model before the text composer unlocks. A stale response for a previously selected model is presented as unknown. The existing account-keyed workbench remount clears presentation state on account changes. Client expiry is advisory; the server independently gates text chat. Image capability remains separately unverified. These frontend changes were inspected but **not executed or type-checked** in this milestone.

`LOCAL_CHAT_ONLY=true` opts into a development-only, authenticated, numeric-loopback profile with no authentication override and a validated loopback Ollama URL. Configuration validation rejects production, protected/unknown environments, disabled authentication, development auth override and external binds/endpoints. The default profile is unchanged. In the explicit profile startup does not create the Chroma directory, construct Chroma's vector store or construct the embedding HTTP service. A deliberately unavailable store/service supplies the existing interfaces; ingestion and retrieval remain denied. Conversation registry, auth, task and source-security initialization remain in place. Other existing startup components including the knowledge graph and directories still initialize. The successful in-process lifespan and shutdown were tested, but cleanup on exceptions before lifespan yield remains an open issue.

### Verification and boundaries

From repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_r12_readiness_offline.py tests/backend/test_a1_health_asgi.py tests/backend/test_a1_capabilities_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_i6_workflow_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r11_model_inventory.py
```

**71 passed, 1 warning in 12.58 s.** The warning is pytest's unknown `asyncio_mode` option with plugin autoload disabled. Tests use an in-process ASGI transport, temporary SQLite/files, synthetic identities, injected responses and a timer-only event loop that forbids socket creation. They include authentication, model-specific readiness, 42 → 126 → 131 through the real streaming route and tools, ownership, timeout/cancellation/response cleanup, late results, endpoint/config change, the chat-only lifespan, and blocked document operations. No external timeout or service was involved. An earlier probe test run passed with an additional unraisable async-generator cleanup warning; the probe now explicitly closes the iterator and the final run did not reproduce it. `git diff --check` passed with Windows LF/CRLF warnings.

No frontend tests were executed: the existing test harness invokes the TypeScript transpiler, and the prior automatic approval review rejected frontend build continuation because of possible registry access. The user retained the restriction on TypeScript/build execution; no alternate path was used. **TypeScript, production build, mounted React, browser and live Ollama remain NOT VERIFIED.** No model, Chroma, application listener or other real service was started; no package/model was downloaded. Protected document ingestion/retrieval, artifact access/publication, code execution and approval resume remain blocked; the disabled Windows launcher remains untouched.

Next: in a separately authorized isolated offline environment, verify the frontend contract and build using reviewed local dependencies; then address lifespan cleanup on partial startup and perform separately authorized real local startup, inference, browser and process-lifecycle tests. Treat model tag replacement, process-local cache and cross-process authorization atomicity as unresolved release risks. No commit, merge or push was made.

## R1.3 — Chat-only lifecycle and readiness integrity

**R1.3: PARTIAL. R1: PARTIAL. Production: NO-GO.** The chat-only startup leak and model-identity gaps were narrowed with offline evidence. Full release acceptance remains open: the protected-profile vector client has no established close contract in the inspected wrapper, several async security tests could not execute with the permitted plugin-disabled harness, and frontend/runtime execution remains unverified.

### Lifecycle

The former lifespan registered cleanup only after all startup steps had succeeded, so a later failure could leave the model router or embedding HTTP client open. The lifespan now uses an application-local `AsyncExitStack` and registers each established async cleanup immediately after acquisition. Cleanup failures are logged categorically without raw exception details, do not mask the primary startup failure, and do not prevent the other registered cleanup. The router attempts to close all cached providers and clears its cache to prevent a second close. Readiness and in-flight check tokens are invalidated before cleanup and across a new lifespan. Auth, task and audit stores use short-lived SQLite connections; the graph store previously used SQLite's transaction context without closing connections, so it now explicitly closes them after success, rollback or initialization failure. The inspected vector-store wrapper does not expose an established shutdown method; protected-mode Chroma lifecycle and resources created inside a constructor that fails remain unverified.

The production chat-only lifespan was exercised with temporary data and injected failures both after router creation and after the unavailable embedding placeholder was created. Chroma and the real embedding service were asserted not to initialize. Tests confirm protected document ingestion/retrieval remain denied, normal shutdown closes only the owning instance's resources, cleanup failure is recorded while preserving the injected startup error, and graph connections close on successful initialization and failed transactions. The existing explicit authenticated development-only profile and protected feature gates were not relaxed.

### Model evidence and chat

The bounded Ollama inventory observer now retains valid provider-reported model SHA-256 digests when present. Readiness records bind the observed digest to the existing normalized model, endpoint, model configuration, chat capability, inventory generation and expiry. A changed or disappeared digest invalidates a record previously tied to it; malformed and ambiguous inventory is rejected. An absent digest is explicitly reported as unavailable identity evidence, not proof that weights are unchanged. Inventory refresh and lifespan changes invalidate readiness, and tokens prevent late checks from restoring old state. The digest is provider-reported evidence, not an independent verification of local weights. Replacements between observations, especially under a stable tag or with no digest, can still go undetected during the short freshness window.

Text chat checks readiness on request entry and again when an SSE stream starts. The initially resolved model ID is pinned through dispatch so changing the configured default between those points cannot silently switch models. These checks do not make readiness and later provider execution atomic; readiness does not guarantee inference success. A synthetic provider failure after readiness returned an error event without `done`; no agent execution retry was introduced. Existing conversation identity and tool authorization remain in force.

The UI displays a warning when a checked model has no observed digest. Static review found selected-model, expiry, account-keyed remount and separate image gating in place; these frontend changes remain **NOT EXECUTION-VERIFIED**. TypeScript, production build, mounted React and browser are **NOT VERIFIED**. The prior automatic review rejected frontend build continuation due to possible registry access; no restricted harness or alternate compiler/build path was run.

### Verification

From the repository root with `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'`:

```powershell
python -m pytest -q tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_r12_readiness_offline.py tests/backend/test_a1_health_asgi.py tests/backend/test_a1_capabilities_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_i6_workflow_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r11_model_inventory.py
python -m pytest -q tests/backend/test_i81_authorization_freshness.py tests/backend/test_i10_artifact_integrity.py tests/backend/test_i111_followup_boundary.py
```

Results: **82 passed, 1 warning in 12.34 s** and **22 passed, 1 warning in 5.13 s**. Each warning is pytest's unknown `asyncio_mode` option with plugin autoload disabled. These are in-process production modules, temporary SQLite/files, injected providers and a timer-only harness that forbids socket creation. They include the 42 → 126 → 131 streaming workflow, owner denial, expired stream-start readiness, default-model pinning, digest transitions, provider failure, lifecycle and protected-boundary regressions. No live services or real sockets were used.

A prior broader command added `tests/backend/test_phase153c_task_security.py` and `tests/backend/test_batch2b2_retrieval_boundary.py` to the focused list: **84 passed, 13 failed, 12 warnings in 13.46 s**. The 13 failures were async tests whose bodies could not run because the required pytest async plugin was disabled; warnings included unknown async marks. They were not rerun using the normal socket-sensitive event loop. This is a test-infrastructure limitation, not passing evidence for those cases. `git diff --check` passed with a Windows LF/CRLF warning.

Next, prepare an explicitly approved isolated local-runtime test covering frontend dependencies/build and mounted browser verification, authenticated chat-only startup, an operator-triggered Ollama probe, streaming chat, and verified process ownership/shutdown. Resolve the Windows asyncio and native process supervisor restrictions before executing it. No backend/frontend/Ollama/Chroma service was started, no model/dependency downloaded, and no commit, merge or push made.
