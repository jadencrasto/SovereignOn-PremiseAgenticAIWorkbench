# Phase 1 safe host security test results

Production **NO-GO**. The two initial commands from `SAFE_HOST_TEST_EXECUTION_PLAN.md` ran after static inspection of their imports, selected AST-extracted paths, synthetic collaborators and temporary-file behavior. Python resolved to `C:\Python313\python.exe`, version 3.13.7; isolated `-I -B` mode was used. Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; extensive pre-existing tracked modifications and untracked files remain, so HEAD alone does not identify the tested tree.

The tests use stdlib, fake dependencies and temporary files/SQLite; Chroma's import is deferred and was not invoked by the selected paths. The literal `unused` path is passed to a read factory; the inspected denial occurs before filesystem access. No network monitoring was installed, so absence of network attempts was not independently measured. No unexpected activity or unsafe initialization was reported by test output. `TemporaryDirectory` cleanup completed without a test error; residual files were not independently enumerated.

## Results

| Group | Command and coverage | Pass | Fail | Skip | unittest time | Exit |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Selected e1 methods | `python -I -B tests/backend/test_batch2b3e1_offline_stdlib.py AuditTests.test_task_approval_and_artifact_guard AuditTests.test_code_execution_direct_factory_is_denied AuditTests.test_document_and_graph_tools_deny_before_access AuditTests.test_ingestion_fails_before_any_work_for_all_backend_states` | 4 | 0 | 0 | 0.121 s | 0 |
| e2 workflows | `python -I -B tests/backend/test_batch2b3e2_offline_stdlib.py` | 6 | 0 | 0 | 0.083 s | 0 |
| **Total** | | **10** | **0** | **0** | **0.204 s** | |

In each separate PowerShell session `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'` preceded the command. These commands use unittest, not pytest. Process elapsed times reported by the execution tool were approximately 0.698 s and 0.646 s; the table gives unittest's own reported test times. Both outputs ended `OK`; there were no unexpected errors.

The first group passed each selected method: task approval/resume and artifact guard; direct code-execution factory denial; workspace/graph/retrieval denial; ingestion denial before work. The e2 group passed each of six methods: demo seeder reports failure/skips; evaluation blocked before backend/network check; evaluation requires all requested documents eligible; ingestion failure sanitized; PENDING/REVOKED rows preserved without upload/embedding/index writes; dormant manifest/readback corruption rejected using synthetic collaborators.

## Findings and limits

The selected negative boundaries behaved as asserted; no newly unsafe dependency was discovered within these inspected paths. The methods extract source AST without normal decorators, framework setup or application startup. Mocks and dummy actors cannot demonstrate real ownership isolation, FastAPI route enforcement, valid positive approval flows, actual vector storage, OS sandboxing, independent-writer safety or persistent recovery. The e2 dormant fixture is explicitly not an authorization bypass or proof of authorized ingestion. Existing deny controls remain unchanged.

Recommended next batch: independently review and, only after ensuring a trustworthy monitored default-deny boundary where needed, run selected higher-fidelity approval, conversation ownership and artifact route tests identified as UNKNOWN in `SAFE_HOST_SECURITY_TEST_MATRIX.md`. The optional e1/e2-independent synthetic index/protocol/broker tests are reviewed candidates but were not part of this approved execution and were not run. The previously rejected full pytest suite was not retried. No VM, ISO, host firewall, SCM, Docker, Ollama, Chroma or FastAPI startup was accessed or modified. No product source was changed.
