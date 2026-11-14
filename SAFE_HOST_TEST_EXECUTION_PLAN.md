# Safe host security test execution plan

No commands below were executed in this review. They are a proposed narrowly scoped next batch, not a release authorization. Keep the VM powered off; no network settings, production services, host security settings or production data are needed. Review the matrix and the current source at execution time, confirm the `python` executable is the intended trusted interpreter, and use an ordinary non-elevated host session. Temporary files may remain if a process is forcibly interrupted. Do not touch the `unused` reparse point.

## Smallest initial batch

From the repository root in PowerShell, set the external pytest autoload flag even though the selected commands use stdlib `unittest` directly:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -I -B tests/backend/test_batch2b3e1_offline_stdlib.py AuditTests.test_task_approval_and_artifact_guard AuditTests.test_code_execution_direct_factory_is_denied AuditTests.test_document_and_graph_tools_deny_before_access AuditTests.test_ingestion_fails_before_any_work_for_all_backend_states
python -I -B tests/backend/test_batch2b3e2_offline_stdlib.py
```

The first command is four selected test methods covering task approval/resume, artifact preview/download, direct code execution, workspace/graph/retrieval and ingestion denials. It does not establish positive authorization or actual route middleware behavior. The second covers synthetic workflow failure reporting, temporary SQLite PENDING/REVOKED preservation and dormant manifest/readback validation. Each unittest runner prints its actual case count and returns nonzero on failure. Record exact command, source revision and working-tree state, interpreter identity, stdout/stderr, passed/failed/skipped counts and temporary cleanup outcome. Do not invent expected counts for the second test file or treat a historical result as a current pass.

The `test_document_and_graph_tools_deny_before_access` case passes the literal path `unused` to a file-read factory, but the inspected factory does not open it and the tested denial occurs before any path operation. If the factory or policy changes, omit this method until re-reviewed; never inspect, remove or modify that reparse point as part of the test.

## Optional second batch after reviewing initial results

```powershell
python -I -B tests/backend/test_batch2b3e_offline_stdlib.py
python -I -B tests/backend/test_batch2b3f_protocol_model.py
python -I -B tests/backend/test_batch2b3g_broker.py
```

These respectively assess non-authorizing synthetic index comparison with fake clients, conceptual coordination, and in-memory broker denial. They do not prove independent-writer exclusion, a real persistent backend, a native process boundary, or recovery. Run separately so a failure has an identifiable source.

## Higher-fidelity tests deferred

Full `test_approval.py`, `test_phase153c_task_security.py`, `test_phase153g_registry_authorization_guard.py`, artifact error routes and execution-disablement tests require pytest and/or FastAPI/Pydantic and larger transitive imports. Their collection paths, plugin behavior, runtime package versions and outbound behavior have not been completely reviewed. Disabling external plugin autoload is necessary but insufficient. Before execution, review every collection hook and dependency initialization, establish an independently effective monitored default-deny boundary covering native code and child processes, and approve the exact selection. Do not retry the automatically rejected full suite.

## Follow-up

Record failures without weakening expectations or production gates. Fixes require a separately scoped implementation task and suitable regression tests. Protected identity, SCM, pipe and ACL tests require separately approved disposable VM provisioning and cannot be inferred from these host tests. Preserve the Lifecycle evidence and snapshots. Production remains **NO-GO**.
