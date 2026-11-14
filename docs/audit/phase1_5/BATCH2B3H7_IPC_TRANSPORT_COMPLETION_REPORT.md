# Batch 2B.3H.7 — IPC transport completion

Date: 2026-11-12. Branch `phase1-5-recovery-security`. **NOT IMPLEMENTATION-READY; functional IPC NOT DEMONSTRATED; production NO-GO.** This batch did not finish the named-pipe transport, enable server/client entry points, or replace the preflight-only PowerShell runner. The requested acceptance criteria are not met.

## Inspection and actual code

Inspected Git status, current draft, peer-token helper, preflight runner, and H.4–H.6 findings. The designated `C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe` reports pywin32 312 and imports the requested bindings. Existing work was preserved; no dependency was installed and no production gates, storage, schema, VM, host accounts, firewall, ACLs, `unused` reparse point or services were changed. No commit, push, merge or deployment occurred.

Added `prototype/windows_broker_ipc/overlapped_lifecycle.py`, an **unwired internal coordinator**, and `tests/backend/test_batch2b3h7_lifecycle.py`. The coordinator distinguishes immediate completion with a transferred count from pending completion, requires finite requested wait and drain intervals, retains the passed resource reference during cancellation, requests cancellation on timeout or `BaseException`, waits again, drains the completion, and rejects invalid counts. If completion cannot be established it raises `UnsafePendingIO`. This is executable progress on control flow, not a Windows transport. It does not create a pipe, import pywin32, manage a real Windows event, or automatically terminate a process. The future host must retain all resources and terminate safely on `UnsafePendingIO`; the current helper cannot itself guarantee this when its caller unwinds.

The user-selected allowed commands and existing candidate specific-rights DACL remain present only in the previous unverified draft. The public `serve()` and `request()` remain disabled, and the draft is not integrated into FastAPI or production storage. No executable broker/client commands are available yet. `tests/backend/run_batch2b3h6_native_ipc.ps1` remains a preflight-only script, not the requested integration runner, and was not replaced with an unsafe or misleading launcher.

## Exact technical blockers

The pywin32 source documents an overlapped read buffer return object and recommends an allocated read buffer to avoid completion/assignment races; the draft has not been changed to use a verified adapter with that lifetime. Windows cancellation requests do not guarantee cancellation or completion, and the OVERLAPPED and associated buffer must remain valid until completion. The installed `win32file` exposes `CancelIo` but not `CancelIoEx`; a reviewed binding for precise cancellation and completion is still absent. The new coordinator explicitly requires an adapter whose `start` cannot throw after issuing I/O without communicating pending state. pywin32/native initiation, error mapping, `ERROR_MORE_DATA`, immediate write/read byte counts, and connect completion during cancellation have not been demonstrated to satisfy that contract. Its drain callback must distinguish completed-cancelled from an error whose completion is unknown, which mocks cannot establish.

Additional unfinished implementation includes per-run unique endpoint configuration, reliable server loop/disconnect state, bounded shutdown/restart, an authenticated connected-client handle after a fully read message, a finite real subprocess harness, and safe cleanup of only the harness's process tree. Windows server identity is unverified; same-account SID matching is not production authentication. `PIPE_REJECT_REMOTE_CLIENTS`, first-instance and specific-rights flags in an unverified private draft are not operational guarantees. No changes were made to enable them.

An actionable alternative if pywin32's ownership/cancellation contract cannot be made reviewable is a small Windows-only .NET host built around `NamedPipeServerStream`/`NamedPipeClientStream` with explicit current-user security descriptor, asynchronous `WaitForConnectionAsync`, `ReadAsync` and `WriteAsync`, cancellation tokens, bounded framing, and a separately supervised process lifetime. That alternative still requires precise Windows ACL/server identity review and real Windows cancellation tests; .NET async APIs alone do not prove safe cleanup or authenticate a server. It is not implemented in this batch. The coordinator's tests provide executable fault-injection evidence but do not replace that host or the missing runner.

## Offline tests and native status

With plugin autoload disabled and bytecode writes disabled, using the exact interpreter:

```powershell
& 'C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe' -I tests/backend/test_batch2b3h7_lifecycle.py -v
& 'C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe' -I tests/backend/test_batch2b3h6_offline.py -v
```

**9 passed, 0 failed, 0 skipped**: six fake-backend coordinator tests and three previous in-memory descriptor/protocol tests. They cover immediate/pending abstract outcomes, timeout, interrupt, unconfirmed drain and invalid counts; they do not initialize an OS pipe or exercise pywin32 completion. No native integration tests were run inside Codex, as requested. No full suite, VM, Chroma, Ollama, Docker or application was started. The ten native scenarios requested by the user remain **NOT RUN**, and no executable operator-run harness currently exercises them.

Next implementation must supply an independently reviewed Windows I/O adapter or a concrete .NET host, retain resources on ambiguous initiation/completion, implement the actual bounded process supervisor and unique endpoints, then allow the operator to run the approved native functional tests. A future development ping will not prove production authentication or storage isolation. **Production remains NO-GO.**

References: [pywin32 file wrapper source](https://github.com/mhammond/pywin32/blob/main/win32/src/win32file.i), [Microsoft CancelIoEx](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex), and [Microsoft cancellation limitations](https://learn.microsoft.com/en-us/windows/win32/fileio/canceling-pending-i-o-operations).
