# Test baseline

## Environment

Windows 11; Python 3.13.7; Node 22.20.0; pytest 8.4.2; repository commit `6f6a167f66f2fb5128f89acd5940fe0fd4656db3`. Existing frontend `node_modules` and backend Python packages were available; exact installed dependency inventory and GPU/model versions were not established. No downloads or upgrades. No live data was intentionally used for tests. The tests were launched on a temporary Git-archive copy, with Chroma telemetry disabled; ignored user files were excluded.

| Suite/check and exact command | Result | Counts / detail |
|---|---|---|
| Frontend `npm run lint` | PASS (exit 0) | Warnings present, including React refs/effects and hook dependencies. Not an individual-test count. |
| Frontend `node_modules/.bin/tsc.cmd --noEmit --incremental false -p tsconfig.app.json` | PASS (exit 0) | Application typecheck. |
| Frontend `node_modules/.bin/tsc.cmd --noEmit --incremental false -p tsconfig.node.json` | PASS (exit 0) | Vite configuration typecheck. |
| Backend `python -m pytest tests -q -p no:cacheprovider --tb=short` | BLOCKED / interrupted | No collection summary or test result produced before interruption. Passed/failed/skipped counts **unknown**, not zero. |
| Backend same command with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `-p pytest_asyncio.plugin` | BLOCKED / interrupted | No collection summary or test result produced before interruption. Root cause unverified; counts unknown. |
| Isolated `python -m pytest tests/backend/test_config.py -q -p no:cacheprovider --tb=short`, plugin autoload disabled | PASS | **3 passed, 0 failed, 0 skipped**, 1 configuration warning because pytest-asyncio was not loaded; 0.34s reported by pytest. |
| Isolated `python -m pytest -p pytest_asyncio.plugin tests/backend/test_rag.py tests/backend/test_rag_retrieval_quality.py tests/backend/test_approval.py tests/backend/test_auth.py -q -p no:cacheprovider --tb=short` | BLOCKED / interrupted | Some progress markers appeared, then no further progress or final summary; total counts unknown. |
| Isolated `python -m pytest -p pytest_asyncio.plugin tests/backend/test_rag_retrieval_quality.py -q -p no:cacheprovider --tb=short` | BLOCKED / interrupted | Some progress markers appeared, then no further progress or final summary; total counts unknown. |
| `ollama list` | BLOCKED (exit 1) | 127.0.0.1:11434 socket denied by execution environment; cannot distinguish service availability from sandbox restriction. |
| Frontend production build | NOT RUN | Existing build writes `frontend/dist` and TypeScript build info; a copy with isolated dependencies was not established. |
| Eight `eval.run_all` suites and scripts | NOT RUN | Runner overwrites tracked/latest evaluation files, creates indexes, and may perform model inference; not run against existing data. Isolated suite needs a working local model and network permission. |
| Browser E2E, security isolation and performance | NOT RUN | No safe runnable isolated deployment/live models established. |

The initial test launcher failed its copy-location step and briefly started pytest in the original checkout before it was interrupted during startup; no test completion was observed. Later launches were isolated. These interruptions do not prove a code or test defect. `README.md`'s historical 647-passed count is not a current result. Only the three configuration tests above produced individual results.

To reproduce safely, archive the exact baseline into an empty temporary directory, disable telemetry, check all test data paths and isolate any model service, then run the full pytest command and store output outside the checkout. Investigate collection with verbose `--collect-only` and bounded timeouts; use explicit plugin loading as a diagnostic, not as a substitute for the normal suite. Execute the eight evaluations in a disposable copy with local synthetic data and a deliberately controlled model server; capture unavailable separately. Run the frontend build in a disposable copy using a provisioned matching dependency tree. Preserve original data and outputs.
