# R6.7 — Approval stream terminal integrity

**R6.7: PASS for scoped synthetic backend correction. Production: NO-GO.** Starting branch `main`, HEAD `2b96fee1f24777247f4efb7a18293eef4275e9cb`. Existing uncommitted work was preserved.

The natural-language approval interceptor in `backend/api/chat.py` checked whether the caller had approval permission, then emitted `error` followed by `done` for a denied approval. Elsewhere `done` terminates a successful stream, but it can also close an approval-pending stream without implying task completion. In either interpretation an error followed by `done` was inconsistent with the client's first-terminal-event contract and could mislead another client. The denial path now emits only `error` and closes. The adjacent intercepted-resume wrapper now treats `error`, `task_failed` and `task_cancelled` as terminal and closes the underlying async generator using `aclosing`; it does not forward subsequent text or tool events. No new event type, approval authority or receipt mechanism was introduced.

Approval checks, conversation ownership and the production `resume_agent_task` source-authorization denial remain in place. The real resume path refuses continuation before approving or rejecting the token. The focused route tests verify viewer denial and cross-owner rejection, no resume dispatch or approval consumption, and real blocked resume for approve and reject. Separate injected synthetic resume streams test all three terminal event types with and without preceding text, no later tool-result/delta/`done`, and generator cleanup. The injected stream tests verify the wrapper contract only; they do not enable the production resume gate or prove real side-effect execution behavior. Existing planning, tool authorization freshness, numeric receipt and blocked artifact tests were included in the regression run; they do not independently establish every expired, missing, reused or wrong-operation approval case through this SSE route.

Files changed for this milestone: `backend/api/chat.py`, `tests/backend/test_r67_approval_stream_offline.py`, and this report. The existing frontend JSON and multipart SSE parsers recognize `error`, `task_failed` and `task_cancelled` as terminal and retain their defenses against subsequent events; this is static compatibility inspection only. Frontend tests, TypeScript, browser, live Ollama and application startup remain **NOT VERIFIED**.

Offline backend verification from the repository root using synthetic providers, temporary storage and in-process ASGI; no real sockets or services:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_r67_approval_stream_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_i101_artifact_boundary.py
git diff --check
```

**65 passed, 1 warning, 0 skipped**, 16.17 seconds. The warning is pytest's unknown `asyncio_mode` configuration option with plugin autoload disabled. No test failed in this run. `git diff --check` passed. No independent external hard timeout was configured. No services, VM, native fixtures, frontend tooling, protected operations or launcher were started.

**Next action:** separately authorize the reviewed offline frontend test environment and execute the existing terminal-event regressions.
