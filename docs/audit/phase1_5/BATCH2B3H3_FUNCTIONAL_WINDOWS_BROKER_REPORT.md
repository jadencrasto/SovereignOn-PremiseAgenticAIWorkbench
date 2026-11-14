# Batch 2B.3H.3 — Development Windows broker

Date: 2026-11-12. Branch `phase1-5-recovery-security`. **NOT COMPLETE; production NO-GO.** The requested separate-process named-pipe exchange did not succeed. A development pipe was created, but a client attempting to open it received Windows error 5 (`Access is denied`). The exact cause has not been established. The draft server and client entry points are now disabled; do not treat this as a functional or security-verified broker.

## Review, scope and files

Inspected the 2B.3H, H.1 and H.2 reports, existing Windows IPC and simulated broker contracts, and working-tree status before editing. The explicitly requested interpreter `C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe` reports pywin32 312 and successfully imports `win32pipe`, `win32security`, `win32file`, `pywintypes`, `win32event` and `win32api`; no package was installed.

- `docs/audit/phase1_5/BATCH2B3H3_LOCAL_TEST_PLAN.md`: test plan written before local process testing.
- `prototype/windows_broker_ipc/dev_pipe.py`: development-only named-pipe **draft**, with `_serve_unverified_draft` and `_request_unverified_draft` retained for inspection. Public `serve()` and `request()` fail closed. The script is not imported by FastAPI and is not wired to production storage or authorization.
- `tests/backend/test_batch2b3h3_dev_pipe.py`: separate-process test, now explicitly skipped while the transport is disabled; a separate check confirms the public functions refuse to run.
- This report. Existing production gates, VM and evidence were not modified. No host accounts, host filesystem ACLs, firewall, `unused` directory, Chroma, Ollama, Docker, production database, commit, push, merge or deployment were touched.

## Draft design and limits

The draft uses `CreateNamedPipe` with a dedicated development name, `PIPE_REJECT_REMOTE_CLIENTS`, message mode, `FILE_FLAG_FIRST_PIPE_INSTANCE` and an explicit in-memory security descriptor granting generic read/write to the process account SID. It attempts sequential clients and calls the earlier token-inspection helper against the server account SID after receiving a bounded request. This only models a same-account development policy; it cannot distinguish trusted and untrusted processes under that account. The development client has **no authenticated server identity** and must never send secrets or treat responses as trusted. Only `ping`, `status`, `capabilities` parse as successful commands; responses are marked `development_only` and `trusted_broker: false`.

The draft attempts overlapped `ConnectNamedPipe`, `ReadFile` and `WriteFile` with a 1.5-second event wait and same-thread `CancelIo`, terminating the process if cancellation does not signal within a further wait. This is **unverified code**, not proof of bounded cancellation, safe kernel buffer/handle lifetime, correct immediate-completion handling, clean shutdown, first-instance enforcement or proper ACL behavior. The attempted integration never reached these end-to-end checks. The draft can be called directly by in-process Python despite its underscore names; it is not a security boundary and must not be exposed or used until independently reviewed and fixed.

The public functions intentionally raise `RuntimeError('Development IPC disabled: bounded I/O and pipe access remain unverified')`. Running the script with `serve`, `ping`, `status` or `capabilities` currently fails immediately. There are **no functional startup/client commands or running endpoint** to hand to an operator yet. Existing Batch 2B.3H `server.py` and `client.py` also remain unavailable.

## Test results and exact limitations

Initial local separate-process command:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe' -I tests/backend/test_batch2b3h3_dev_pipe.py -v
```

Before disabling the draft this ran **1 test: 0 passed, 1 error, 0 skipped**. The child broker was started with no window; the test retried the client open for five seconds and ended with `pywintypes.error (5, 'CreateFile', 'Access is denied.')`. It terminated the exact child it had started. Pipe creation and a ready message were separately observed, but no ping, token validation, message exchange or security property was verified. A separate client launched in another tool session saw error 2 (pipe not found); whether tool-session isolation, endpoint security, sandbox constraints or implementation behavior caused the differences is **unknown**. No ACL was broadened to bypass the error.

After the fail-closed change the same command returned **1 passed, 0 failed, 1 skipped**: it verifies disabled public entry points, while the unverified integration is explicitly skipped. Running the prior mocked peer identity test with the same interpreter returned **6 passed, 0 failed, 0 skipped**. Final test run total: **7 passed, 0 failed, 1 skipped**. The skipped test is not a functional pass. The initial failure is retained above and not overwritten by the later skip.

Not verified: real separate-process ping/status/capabilities, unsupported/malformed/oversize responses over the pipe, duplicate-instance refusal, timeout/cancellation, clean shutdown and restart, unauthorized distinct identity and endpoint squatting. The full suite was not run and its not-run count is unknown. No independently isolated adversarial environment or OS-level network guarantee is claimed.

## Next step

Diagnose the `CreateFile` access-denied result in a separately controlled development environment using read-only token and effective-access inspection, including the managed sandbox's named-pipe policy and both process tokens. Do not assume that error 5 proves either a secure DACL or a sandbox issue. Independently review the draft's overlapped cancellation and completion semantics, pipe message sizing, disconnect/reconnect and first-instance behavior before re-enabling any listener. Then rerun the local functional tests with safe child cleanup, followed by distinct-identity and endpoint-squatting tests. Functional success would still be separate from production authentication or storage authorization. **Production remains NO-GO.**
