# Audit command log

All commands run from project root unless stated; `rg`, `Get-Content`, `Get-ChildItem`, Git read-only inspection, Python/Node version and path discovery. No secrets, source or user documents were sent externally.

- `git status --short --branch`, `git rev-parse HEAD`, `git remote -v`, `git log -5 --oneline`, `git branch -a`, `git tag`: clean baseline, main at commit in executive summary; origin configured; no tags printed.
- `rg --files` and targeted `rg -n` across source, docs, tests and configuration: inventory and evidence. Original statement not found; ignored user uploads exist and were not used as test fixtures.
- `python --version`: 3.13.7; `node --version`: 22.20.0; `python -m pytest --version`: 8.4.2 (slow startup).
- `npm run lint` in frontend: exit 0 with warnings.
- `frontend/node_modules/.bin/tsc.cmd --noEmit --incremental false -p tsconfig.app.json`: exit 0.
- `ollama list`: exit 1, loopback socket denied by execution environment; no models pulled.
- Isolated Git archive extracted to temporary directory; pytest launched using `python -m pytest tests -q -p no:cacheprovider --tb=short` with telemetry disabled. Both the ordinary and explicitly loaded pytest-asyncio attempts produced no collection summary and were interrupted; no test counts are available.
- `frontend/node_modules/.bin/tsc.cmd --noEmit --incremental false -p tsconfig.node.json`: exit 0.
- In an isolated Git archive, `python -m pytest tests/backend/test_config.py -q -p no:cacheprovider --tb=short` with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`: 3 passed, 1 warning, 0.34s; asyncio plugin intentionally not loaded for this synchronous smoke suite.
- Targeted isolated RAG/auth/approval tests and a standalone retrieval-quality run loaded pytest-asyncio explicitly with plugin autoload disabled. Both displayed progress markers but stopped producing output and were interrupted without a summary. Counts and cause remain unknown; see exact commands in TEST_BASELINE.md.
- A first attempted test launch failed to find its isolated working directory; pytest briefly started from the project directory and was interrupted during startup, with no test result. A subsequent isolation attempt against C:/workspace failed because that path did not exist. Neither is claimed as a passing run. An additional isolated attempt disabled third-party plugin autoload and explicitly loaded pytest-asyncio.
- Original `eval.run_all` not run against the checkout because it writes `eval/results/*_latest` and RAG evaluation directories, and may use local inference. No destructive tests, dependency installation, model pull, web egress, live production data mutation, commit or push was intentionally performed.
