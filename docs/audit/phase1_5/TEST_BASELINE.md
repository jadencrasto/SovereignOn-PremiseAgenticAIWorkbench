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
