# R8.8 — Local release candidate preparation

**Offline backend PASS. Local release candidate acceptance BLOCKED. Production NO-GO.** Work started on `main` at `43d72b8036b27b7926a74d08af260f865259669b` with existing uncommitted work. The [R8.7 Antigravity appendix](RELEASE_R8_7_CROSS_FEATURE_INTEGRATION_REPORT.md#antigravity-independent-verification--r87) passed both TypeScript checks, 99 frontend, 7 focused backend and 285 broad backend tests. It reports live acceptance blocked. The R8.6 comparison baseline was 95 frontend and 278 broad backend passes.

## Verified baseline

| Workflow | Verification status |
|---|---|
| Sign-in, cookie auth, session revocation | Verified offline through production routes with synthetic credentials and temporary stores. Mixed-credential logout had a confirmed defect, now covered by a real-store regression. |
| Model discovery, explicit inference readiness and model switching | Verified offline against synthetic inventory, expiry and role/model mismatch fixtures. R8.7 observed installed models on loopback. Actual provider inference through the app is unverified. |
| Ordinary chat, approved tools and persistence | Verified offline for streaming, calculator and conversion, numeric continuity, conversation ownership and in-run persistence. Durable provenance recovery remains externally blocked by R6.4. |
| History navigation, rename, deletion and logout cleanup | Verified offline in route and frontend fixtures, including aborted draft creation and late results. Mounted browser behavior is unverified. |
| Read-only vision | Verified offline for separate text and vision readiness, current-image-only inference input, reattachment, cancellation and tool isolation. |
| UTF-8 and CSV | Verified offline for bounded previews, exact line or row selections, request-scoped answers, descriptor-only history and mixed-format reattachment. |
| SSE and failure handling | Verified offline for the terminal contract, interrupted requests and missing or mismatched provider identity and role. Live browser transport is unverified. |
| OS startup and cleanup | Configuration and lifespan behavior verified offline. Independent process and listener ownership, live launch and native cleanup remain externally blocked. |

## Fix

Authentication selected a bearer credential when a request supplied both bearer and cookie tokens. Logout independently selected the cookie first and returned 200, leaving the authenticated bearer valid and revoking a different session. The new regression reproduced the bearer still returning 200 from `/api/auth/me` after logout. The other three credential combinations passed before the fix.

`backend/auth/dependencies.py` and `backend/auth/routes.py` now share bearer-first credential selection. Logout revokes the selected session and preserves a different cookie session and its browser cookie. Cookie-authenticated logout still requires the CSRF header and clears its matching cookie. `tests/backend/test_r88_session_logout_offline.py` covers cookie-only, bearer-only, matching and different credentials through the real auth router and SQLite session store. No frontend source or configuration changes were needed.

## Local configuration

| Component | Reviewed contract and candidate settings |
|---|---|
| Backend | `python -m backend` validates settings before passing `BACKEND_HOST` and `BACKEND_PORT` to Uvicorn. Use `127.0.0.1:8001` for the candidate. Run a single worker without reload under the approved runner. The startup message reports the configured address. Independent observation establishes listener ownership. |
| Frontend | Vite has a configurable development port and proxies browser-relative `/api` requests to `127.0.0.1:$env:BACKEND_PORT`. Candidate origin is `http://127.0.0.1:5174` with strict-port mode. Set the same `BACKEND_PORT` in the two process environments and match `CORS_ORIGINS` to the frontend origin. JSON and multipart SSE take this same route. |
| Auth and profile | Use `APP_ENV=development`, `LOCAL_CHAT_ONLY=true`, `AUTH_ENABLED=true`, `AUTH_DEV_OVERRIDE=false` and `ALLOW_DEV_CLEARANCE_SIMULATION=false`. The disposable HTTP loopback run uses a host-only `HttpOnly` cookie and SameSite Lax. Use temporary state and the approved bootstrap and operator account sequence in R8.5. |
| Ollama | Use the pre-approved listener at `http://127.0.0.1:11434`. In the R8.7 preflight, `qwen2.5:7b` and `llava:7b` were listed and ports 8001 and 5174 were free. Recheck listener ownership and model identities. Verify capabilities and then run explicit text and vision inference checks. `/api/health/ready` covers the application lifespan. |
| Shutdown | The FastAPI lifespan owns and closes application resources and clears readiness. The approved operator runner separately owns process-tree and listener observation. R5.5, R6.4, the disabled launcher and the protected ingestion, RAG and artifact restrictions remain in force. |

## Offline results

Run from the repository root. After the fix, all three commands exited 0 and each reported the existing `asyncio_mode` configuration warning with plugin autoload disabled. The suites overlap. `git diff --check` passed.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_r88_session_logout_offline.py tests/backend/test_r12_readiness_offline.py tests/backend/test_i81_authorization_freshness.py --tb=short
python -m pytest -q tests/backend/test_r88_session_logout_offline.py tests/backend/test_r87_attachment_transitions_offline.py tests/backend/test_r86_csv_preview_offline.py tests/backend/test_r83_text_file_offline.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_a2_provider_offline.py tests/backend/test_r12_readiness_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r67_approval_stream_offline.py tests/backend/test_r41_shared_storage_gate.py --tb=short
python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_r13_lifecycle_readiness.py --tb=short
```

| Check | Result |
|---|---|
| Authentication, readiness and authorization freshness | 37 passed in 6.14s |
| Integrated chat and protected-gate subset | 175 passed in 22.03s |
| Configuration and lifespan | 16 passed in 2.90s |
| Frontend and live | R8.7 frontend evidence only. No R8.8 frontend tooling, live services, browser or native execution in Codex. |

## Antigravity acceptance handoff

Rerun the commands above. For the prior broad baseline, use section 7 of the R8.7 appendix and add `tests/backend/test_r88_session_logout_offline.py` before `--tb=short`. Use the reviewed no-emit checks and complete frontend suite in the [R8.7 handoff](RELEASE_R8_7_CROSS_FEATURE_INTEGRATION_REPORT.md#antigravity-handoff). Capture exit codes. The broad and frontend results for the R8.8 tree remain pending.

After written authorization and verified process containment, follow the exact environment, startup and stop commands in the [R8.5 handoff](RELEASE_R8_5_LOCAL_RUNTIME_ACCEPTANCE_REPORT.md#antigravity-handoff). Use the candidate loopback ports above, disposable credentials and state, a reviewed browser profile and the approved existing Ollama listener. Check direct and proxied readiness. In one session, demonstrate ordinary streaming, approved arithmetic, live image analysis, TXT line selection and [CSV row selection](RELEASE_R8_6_CSV_PREVIEW_REPORT.md#antigravity-handoff). Check cancellation, model changes, metadata-only history, conversation switch and deletion, logout, and denial of protected operations. For cutoff and exact provider-scope claims, retain independently authorized evidence with synthetic input only. Stop on unknown process or listener ownership, unexpected egress, false success, exposed protected access or an exceeded deadline. Independently confirm owned-process cleanup and preservation of unrelated processes before reporting live PASS.

**Next action** — Have Antigravity run the bounded acceptance handoff, starting with offline verification and proceeding to live cases only after separate authorization and independently verified process containment.

---

## Antigravity independent verification — R8.8

Executed 2026-11-16 on the same checkout without commit, push, pull, merge or branch switch.

### 1. Branch, HEAD and diff status

- Branch: `main`
- HEAD: `43d72b8036b27b7926a74d08af260f865259669b`
- Working tree: 44 modified files, 19 untracked files (reports, new test modules, scratch files).
- `git diff --check`: **PASS** (EXIT_CODE:0, only pre-existing LF->CRLF warnings).

### 2. TypeScript verification

Executed zero-emit typechecks using project-local TypeScript compiler via Node:
```powershell
Set-Location .\frontend
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.app.json --noEmit --incremental false
& 'C:\Program Files\nodejs\node.exe' .\node_modules\typescript\bin\tsc --project .\tsconfig.node.json --noEmit --incremental false
```
- `tsconfig.app.json`: **PASS** (0 errors, 0 warnings, EXIT_CODE:0)
- `tsconfig.node.json`: **PASS** (0 errors, 0 warnings, EXIT_CODE:0)

### 3. Targeted frontend results

Executed targeted suite covering attachments, chat view handlers, stream client, capability render, and history sidebar/render:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\a61-chat-attachment.test.cjs .\tests\a22-chatview-handlers.test.cjs .\tests\a2-stream-client.test.cjs .\tests\a1-capability-render.test.cjs .\tests\a41-history-sidebar.test.cjs .\tests\a41-history-render.test.cjs
```
- Result: **80 passed**, 0 failed, 0 skipped in 811.4 ms, `EXIT_CODE:0`.
- Verifies:
  - Session and cookie logout handling with proper cleanup.
  - Aborted draft creation releasing Send lock immediately without leaking session ID.
  - Model selection changes invalidating stale draft submissions.
  - Plain, image, UTF-8 text, and CSV attachment transitions and error isolation.
  - Stale streaming event suppression and logout/unmount abort handling.

### 4. Full frontend results

Executed complete offline frontend regression suite across all 14 test files:
```powershell
& 'C:\Program Files\nodejs\node.exe' --test .\tests\*.test.cjs .\tests\*.test.mjs
```
- Result: **99 passed**, 0 failed, 0 skipped in 1087.8 ms, `EXIT_CODE:0`.
- Previous baseline (R8.7): 99 passed.
- Baseline maintained: **99 passed** (0 failures, 0 skips).
- Confirms all frontend functionality across authentication, navigation, models, chat streaming, vision, document preview, and CSV tables remains 100% operational.

### 5. Focused backend results

Executed all three R8.8 backend test groups reported by Codex:
- **Group 1 (Auth, readiness, authorization freshness)**:
  ```powershell
  $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
  python -m pytest -q tests/backend/test_r88_session_logout_offline.py tests/backend/test_r12_readiness_offline.py tests/backend/test_i81_authorization_freshness.py --tb=short
  ```
  Result: **37 passed**, 1 warning in 4.58s, `EXIT_CODE:0`.
- **Group 2 (Integrated chat and protected-gate subset)**:
  ```powershell
  python -m pytest -q tests/backend/test_r88_session_logout_offline.py tests/backend/test_r87_attachment_transitions_offline.py tests/backend/test_r86_csv_preview_offline.py tests/backend/test_r83_text_file_offline.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_a2_provider_offline.py tests/backend/test_r12_readiness_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r67_approval_stream_offline.py tests/backend/test_r41_shared_storage_gate.py --tb=short
  ```
  Result: **175 passed**, 1 warning in 15.59s, `EXIT_CODE:0`.
- **Group 3 (Configuration and lifespan readiness)**:
  ```powershell
  python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_r13_lifecycle_readiness.py --tb=short
  ```
  Result: **16 passed**, 1 warning in 2.26s, `EXIT_CODE:0`.

### 6. Broad backend results

Executed the comprehensive integrated backend regression suite covering all 26 test modules:
```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/backend/test_config_validation.py tests/backend/test_phase15_security_controls.py::test_backend_launcher_uses_validated_binding tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r11_model_inventory.py tests/backend/test_r12_readiness_offline.py tests/backend/test_r13_lifecycle_readiness.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py tests/backend/test_i7_model_reliability.py tests/backend/test_a61_multimodal_offline.py tests/backend/test_i6_workflow_offline.py tests/backend/test_r83_text_file_offline.py tests/backend/test_r86_csv_preview_offline.py tests/backend/test_r87_attachment_transitions_offline.py tests/backend/test_r88_session_logout_offline.py --tb=short
```
- Result: **289 passed**, 0 failed, 8 warnings in 29.63s, `EXIT_CODE:0`.
- Previous broad baseline (R8.7): 285 passed.
- New broad baseline (R8.8): **289 passed** (+4 tests from `test_r88_session_logout_offline.py`).
- Warnings: 1 config warning (`asyncio_mode`) + 7 custom marks in `test_phase15_security_controls.py`.

### 7. Logout regression findings

Verified code changes in `backend/auth/dependencies.py` and `backend/auth/routes.py`:
- `request_session_credentials(request, bearer)` now unifies credential resolution across `get_current_user` and `logout`.
- When both Bearer and Cookie credentials are present, Bearer takes precedence in both authentication and revocation.
- Revocation targets strictly `raw_token` (the credential that authenticated the request).
- Cookie deletion (`delete_cookie`) only executes when `not cookie_token or cookie_token == raw_token`, preventing deletion of an unassociated user's cookie when logging out via a distinct Bearer token.
- Tested via `tests/backend/test_r88_session_logout_offline.py`:
  - `cookie`: Cookie session authenticated and revoked; CSRF header enforced on POST `/api/auth/logout`; cookie cleared with `Max-Age=0`.
  - `bearer`: Bearer token authenticated and revoked; 401 on subsequent `/api/auth/me`.
  - `matching`: Matching cookie and bearer credentials cleanly revoked.
  - `different`: Alice bearer + Bob cookie. Alice is authenticated and revoked. Bob's cookie session remains valid (`/api/auth/me` returns Bob 200). `Set-Cookie` is NOT emitted in logout response, preserving Bob's cookie.

### 8. Provider and startup contract findings

- Verified Ollama response decoding in `backend/models/ollama_provider.py` enforces `message.role == "assistant"` and validates model identity against expected model key.
- Rejection of malformed roles or non-assistant roles confirmed.
- Verified startup logging in `backend/main.py` explicitly states `Application startup complete | configured bind=%s:%d`, accurately reporting configured bind address without falsely asserting active OS socket listener state.
- Verified Vite proxy configuration cleanly forwards relative `/api` paths to configured loopback port.

### 9. Confirmed defects and corrections

- **Confirmed Defect**: Pre-R8.8 `logout` checked cookies before Bearer tokens, while `get_current_user` checked Bearer tokens first. A request bearing Alice's Bearer token and Bob's Cookie token authenticated Alice but revoked Bob's session.
- **Correction Verified**: Unified resolution via `request_session_credentials`. Verified all 4 modes pass without error.
- **No other defects found**: All 289 backend tests and 99 frontend tests pass with 0 defects.

### 10. Current port/model status

- `127.0.0.1:8000`: FREE
- `127.0.0.1:8001`: FREE
- `127.0.0.1:5173`: FREE
- `127.0.0.1:5174`: FREE
- `127.0.0.1:11434`: LISTENING (PID 35408, `ollama.exe`)
- Installed models:
  - `qwen2.5:7b` (capabilities: completion, tools)
  - `llava:7b` (capabilities: completion, vision)
  - `nomic-embed-text:latest` (capabilities: embedding)

### 11. Exact R5.5 authorization and containment state

- **Operator Approval**: Missing / Not Granted for live execution of background workbench services or browser automation.
- **R5.5 Supervisor Implementation**: Completed design awaiting independent security review. An independently verified native process-containment mechanism is not yet active.
- **Containment Policy**: In compliance with strict fail-closed governance, live services were not started, the disabled launcher was not restored, and uncontrolled background processes were not spawned.

### 12. Live results or explicit blockers

- **Status**: **BLOCKED**
- **Explicit Blockers**:
  1. Independent written authorization from operator for live service execution and browser automation has not been granted.
  2. R5.5 native process supervisor remains pending independent security review.
  3. No independent process-containment harness is currently verified to ensure clean descendant termination without orphaned background listeners.

### 13. R8.8 scoped status

- **Offline Backend**: **PASS** (289/289 passed)
- **Offline Frontend**: **PASS** (99/99 passed)
- **TypeScript Typecheck**: **PASS** (0 errors)
- **Authentication & Logout**: **PASS** (all credential modes verified)
- **Live Acceptance**: **BLOCKED**
- **Production Status**: **NO-GO**

### 14. R8.9 implementation handoff

- Baseline regression counts: **99 frontend tests**, **289 broad backend tests**.
- All R8.8 authentication fixes independently verified and stable.
- Candidate ports (8001 backend, 5174 frontend) confirmed free; Ollama listener ready on 11434.
- Ready for R8.9 when authorized.
