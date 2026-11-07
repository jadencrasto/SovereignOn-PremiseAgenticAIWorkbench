# Test baseline

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
