# Test baseline

## Security implementation continuation, 2026-11-07

Disposable archive of baseline commit `5dc673f` with modified modules and untracked targeted tests copied in; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`:

`python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase15_security_controls.py tests/backend/test_phase15_registry_continuation.py tests/backend/test_phase15_memory_continuation.py tests/backend/test_phase15_clearance_continuation.py tests/backend/test_phase15_targeted.py tests/backend/test_memory.py tests/backend/test_approval.py -q -p no:cacheprovider --tb=short` yielded **57 passed in 9.04s**. Earlier subsets produced 39 and 56 passes. These are targeted synthetic tests, not the full suite or live model verification. Rejected full-suite command was not retried.

The same isolated archive-and-overlay method with `python -m pytest -p pytest_asyncio.plugin tests/backend/test_rbac.py tests/backend/test_tools.py -q -p no:cacheprovider --tb=short` yielded **99 passed in 19.17s**. Earlier broad-suite rejection remains in force; this targeted run is not a substitute for it.

## Telemetry investigation, 2026-11-07

Disposable Git-archive copy with `backend/rag/store.py` and `tests/backend/test_telemetry_configuration.py` overlaid; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`. `python -m pytest tests/backend/test_telemetry_configuration.py -q -p no:cacheprovider --tb=short`: **3 passed**, 1 warning (pytest-asyncio config while plugin autoload disabled), 0.14s. Fake Chroma components only; no live client or network isolation validation. Full-suite retry **BLOCKED / NOT RUN**, honoring prior automatic review. Docker daemon unavailable, WSL access denied and no verified isolated guest was available. See TELEMETRY_AND_EGRESS_AUDIT.md.

## Continuation, 2026-11-07

The user-created feature branch is at `5dc673f55a02e1c689af62c1bc4c7fa6625b9383`. In this session `python -c 'import asyncio; print(asyncio.run(asyncio.sleep(0)))'` succeeded, as did `ollama list` (qwen2.5:7b, llava:7b, nomic-embed-text:latest). This supersedes the earlier environment limitation for this session only.

Isolated Git-archive copies with changed files overlaid, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, explicitly loading `-p pytest_asyncio.plugin`, `-q -p no:cacheprovider --tb=short`:

- `python -m pytest -p pytest_asyncio.plugin tests/backend/test_memory.py tests/backend/test_approval.py tests/backend/test_plan_validator.py tests/backend/test_phase15_memory_continuation.py tests/backend/test_phase15_clearance_continuation.py tests/backend/test_phase15_registry_continuation.py tests/backend/test_phase15_targeted.py -q -p no:cacheprovider --tb=short`: **48 passed**, 2.12s.
- `python -m pytest -p pytest_asyncio.plugin tests/backend/test_rbac.py tests/backend/test_tools.py tests/backend/test_phase15_clearance_continuation.py tests/backend/test_phase15_registry_continuation.py -q -p no:cacheprovider --tb=short`: **103 passed**, 19.41s, prior to the final added auth-header and real-approval cases; those additions are covered in the 48-test run. The first run of RBAC/tools had **8 failed, 91 passed** because older expectations allowed role-free and unapproved dispatch; tests were updated for the stricter contract.
- `python -m pytest -p pytest_asyncio.plugin tests/backend/test_tasks.py tests/backend/test_task_recovery.py tests/backend/test_task_ownership.py -q -p no:cacheprovider --tb=short`: **33 passed**, 25.79s.
- Frontend `npm run lint`: exit 0 with warnings. Both `tsc.cmd --noEmit --incremental false -p tsconfig.app.json` and `-p tsconfig.node.json`: exit 0. `vite.cmd build --outDir <new temporary directory> --logLevel warn`: exit 0, large chunk warning. `npm run build` itself was not invoked to avoid overwriting existing build artifacts.
- An isolated full test run excluding the evaluation suite was interrupted by automatic approval review: a test unexpectedly contacted an untrusted telemetry endpoint, potentially exporting environment or test metadata. It produced no final summary; pass/fail/skip counts are unknown. Do not retry that suite until network isolation and its telemetry source are established. RAG/Chroma integration and full evaluation remain blocked for this reason.

The preceding Phase 1.5 results remain historical. The full Python suite is not established; passing targeted tests are not proof of complete coverage.

Environment: Windows, Python 3.13.7, Node 22.20.0; isolated Git-archive copy with modified files overlaid for Python checks, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, `ANONYMIZED_TELEMETRY=False`.

- `python -m pytest tests/backend/test_phase15_targeted.py -k "not upload and not multimodal" -q -p no:cacheprovider --tb=short`: **8 passed, 3 deselected**, 2 warnings in 1.27s. Warnings arise because asyncio plugin autoload was intentionally disabled.
- Earlier full targeted run stalled on the async upload test after one passing progress marker; interrupted, so no final counts. Phase 1 full-suite and targeted attempts also did not complete.
- Diagnostic `python -c "import asyncio; print('before',flush=True); print(asyncio.run(asyncio.sleep(0, result='ok')),flush=True)"` printed `before`, then stalled until interrupted. This demonstrates an async runtime restriction independent of application code; it does not prove the cause of every suite interruption.
- `npm run lint`: exit 0 with warnings.
- `node_modules/.bin/tsc.cmd --noEmit --incremental false -p tsconfig.app.json` and corresponding `tsconfig.node.json`: both exit 0.
- `node_modules/.bin/vite.cmd build --outDir <new temporary directory>`: exit 0, 4157 modules, 2.30s; warns about chunks over 500kB. This plus independent typechecks is equivalent coverage of the build stages, but was not `npm run build`; it avoided changing existing generated output.
- `ollama list`: exit 1, loopback socket denied; installed models not determined.
- `git diff --check`: exit 0, line-ending warnings only.

Full Python suite, browser tests, evaluation runner and local-model tests are blocked/not run, not passed. In a permissive local Windows environment, rerun with isolated data, normal pytest plugins and bounded timeouts. Do not use production uploads or tracked evaluation results. Pin a verified Windows Python/dependency set and archive offline wheels with hashes after compatibility and licensing review; no dependency lock or wheelhouse was generated here.

## Final integration verification, 2026-11-07

Current isolated targeted tests: 81 passed, 0 failed, 0 skipped; app lifespan/route tests with fake Chroma and inference: 2 passed, 0 failed, 0 skipped. Frontend lint, both TypeScript checks and isolated Vite build exited 0, with lint/build warnings. Exact commands, durations, coverage and limitations: FINAL_INTEGRATION_REPORT.md. Full suite was not retried and remains BLOCKED; no OS-level network isolation was verified.

## Phase 1.5.1 conversation recovery

20 targeted Python tests and 4 existing frontend helper tests passed; frontend lint, both TypeScript checks and temporary Vite build succeeded with warnings. Exact command, initial test failure and boundaries are in CONVERSATION_RECOVERY_REPORT.md. Full-suite retry remains blocked.

## Final rich-history continuation, 2026-11-07

Baseline `03370df` on `phase1-5-recovery-security`. Status remains **PARTIAL**. In `AgentEngine.chat_stream_with_tools`, successful and failed tool calls are now recorded atomically with a nonempty final assistant message, in bounded `tool_events_v1` metadata: up to 32 entries with generated invocation ID, restricted tool name and boolean result. Arguments, tool output, summaries, tokens, source text and filesystem paths are not persisted in that metadata. An actual engine test uses a fake model and fake tool registry. Authenticated history emits only validated allowlisted fields; the sidebar maps them to existing tool activity UI, which now labels failures correctly. No hydration invokes a tool or makes an approval actionable. Existing owners, unknown fields, local legacy export and the same JSON file/locking scheme remain in use.

This does not record interrupted calls (a final assistant message may never be saved), tools when the final response is empty, or events emitted by `chat_stream_with_tools_multimodal`, the planning/agent execution and resume paths, and arithmetic shortcut. It does not implement persisted source citations, artifact/attachment references, task status, approval history, interruption/failure state, retry/reconnect deduplication, full browser reload or authorization revalidation of source/artifact access. Invocation IDs are generated for this stored record and are not existing execution registry identifiers. Do not infer these missing features from this partial implementation. No live inference, artifact, browser, real Chroma or OS egress test was run. No persisted migration executed against user data; existing backup and rollback advice continues to apply.

Validation from a temporary Git-archive copy with modified backend/test files overlaid, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`: `python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase151_tool_history.py tests/backend/test_phase151_conversation_records.py tests/backend/test_phase15_app_integration.py tests/backend/test_phase15_memory_continuation.py tests/backend/test_memory.py tests/backend/test_phase15_registry_continuation.py -q -p no:cacheprovider --tb=short` — **26 passed, 0 failed, 0 skipped in 14.89s**. Covers fake model/tool engine loop, allowlist, restart/owner isolation, actual route serialization with synthetic persistence, and existing approval controls. `node --experimental-strip-types --test tests/historyRequests.test.mjs` — **4 passed, 0 failed, 0 skipped**; these are request-helper tests, not browser tests. Frontend `npm run lint` exit 0 with warnings, app and node `tsc.cmd --noEmit --incremental false -p ...` exit 0, and `vite.cmd build --outDir <new temporary directory> --logLevel warn` exit 0 with large-chunk warning. Full Python suite and OS egress proof remain BLOCKED after the previous automatic review rejection.

## Phase 1.5.2 addendum — 2026-11-07

Partial: multimodal, planned and resumed registry tool statuses persist only with a nonempty final response; conversation reads expose owner-scoped, read-only task/approval statuses. Synthetic targeted run: 11 passed; frontend helper tests: 4 passed; lint, both typechecks and temporary build passed with warnings. Images, clearance-revalidated citations, artifact links and interrupted-stream journaling remain unimplemented. Actual browser, live models, full suite and OS egress isolation remain unverified or blocked. Static review found the artifact API grants MANAGE_TASKS roles access before checking ownership. See PHASE152_IMPLEMENTATION_REPORT.md for evidence and limitations.

## Phase 1.5.3 security addendum — 2026-11-07

Partial: owner-bound artifact policy, conservative provenance-gated assistant history and owner-bound image descriptors were added. Unprovenanced assistant history is withheld and prior assistant turns are excluded from model context; attachment retrieval is not exposed. Synthetic targeted Python runs: 19 passed and 31 passed (overlapping); frontend helpers 4 passed; lint/typechecks/build passed with warnings. Task/step clearance revalidation, complete provenance, artifact lifecycle and authorized attachment retrieval remain open; full suite and OS isolation remain blocked. See PHASE153_SECURITY_REPORT.md for exact commands, initial test failure, compatibility impact and limitations.
