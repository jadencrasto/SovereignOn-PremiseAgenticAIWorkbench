# Batch 2B.3H.5 — AppContainer and Windows IPC verification

Date: 2026-11-12. Branch `phase1-5-recovery-security`. **NOT COMPLETE: no real named-pipe message exchange. Production remains NO-GO.** The existing public server and client remain disabled. No completed functional integration runner or corrected production-capable transport exists in this batch.

## Execution boundaries

- **Codex-managed environment:** read-only token observations in this session identify the current process and its newly spawned child as AppContainer processes. The following details apply only to those observed processes and their managed execution context.
- **Ordinary Windows PowerShell:** not independently inspected in this batch. A different command, tool session, or PowerShell window must not be assumed to be outside the AppContainer; run the token diagnostic in that environment to establish its actual token.
- **Approved isolated Windows VM:** not started, modified or inspected in this batch. The operator's earlier Chroma durability observations do not establish the VM's current IPC identity, network controls or approval for a new test.

Used only `C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe`; pywin32 312 and the required imports were confirmed. Existing tracked and untracked changes were preserved. No dependency, production gate, FastAPI route, production store, host account/ACL/firewall, `unused` reparse point or VM evidence was modified. No commit, push, merge or deployment occurred.

## Read-only AppContainer evidence

`tests/backend/batch2b3h5_token_diagnostic.py` uses `GetTokenInformation` with the exact parent and a newly spawned child, plus `ConvertSidToStringSidW`. It does not create/open a pipe or change tokens. Both reported the same package SID:

`S-1-15-2-2968813833-811790644-2202111208-3784096404-1081847329-2708967783-1438471679-88862194-3122826727-1320232320-2618743482`

Both reported the following capability SIDs (these are identifiers, not proofs that a named pipe grants them access):

- `S-1-15-3-1024-1065365936-1281604716-3511738428-1654721687-432734479-3232135806-4053264122-3456934681`
- `S-1-15-3-1024-530591407-3118584226-222893001-1659895842-537460181-133087082-1363364250-4100990745`
- `S-1-15-3-2968813833-811790644-2202111208-3784096404-1081847329-2708967783-1438471679`

The user SID, logon SID list and session ID matched; the user/logon values were not printed. Both integrity SIDs were `S-1-16-4096` (low). These are observed current process tokens, not an assertion about all future tool calls, ordinary PowerShell, or the original already-exited H.3 processes.

H.4's ephemeral pipe diagnostics reported a single user-SID allow ACE with observed mapped access mask `0x0012019f`, requested generic mask `0xc0000000`, a low mandatory label and client `CreateFile` error 5. The user-SID ACE did **not** explicitly contain the observed AppContainer package or capability SID. Microsoft documents that the AppContainer and user/group portions both participate in resource access checks; it also documents a `\\.\pipe\LOCAL\` namespace rule for AppContainer pipe contexts. These facts support the AppContainer/namespace hypothesis, but do **not** uniquely establish the failing check. No live object was created in this batch and no complete AppContainer-aware `AuthzAccessCheck` or independently verified supported equivalent was executed. The precise cause remains unresolved, including namespace, token/package rights and managed-platform policy. A package SID grant, `Everyone`, or `All Application Packages` grant was not added.

## Least-privilege design

The current disabled draft still uses generic read/write in its user ACE; it has not been silently changed. For a future reviewed endpoint, `prototype/windows_broker_ipc/rights_design.py` records a **candidate**, not an applied descriptor:

| Purpose | Candidate specific bits |
| --- | --- |
| Client message data | `FILE_READ_DATA=0x1`, `FILE_WRITE_DATA=0x2` |
| Client pipe attributes | `FILE_READ_ATTRIBUTES=0x80`, `FILE_WRITE_ATTRIBUTES=0x100` |
| Client synchronization | `SYNCHRONIZE=0x00100000` |
| Client total | `0x00100183`; no generic bits, `WRITE_DAC`, `WRITE_OWNER`, or `FILE_CREATE_PIPE_INSTANCE=0x4` |
| Server instance creation | Separate `FILE_CREATE_PIPE_INSTANCE=0x4` authority, never delegated through the client ACE by accident |

No blanket `READ_CONTROL` or other standard right is proposed for the client unless a reviewed operation requires it. A same-user server/client design cannot guarantee separation of an attacker sharing the server's identity, and the rights table does not resolve an AppContainer's second access check. Exact object and token effective access, pipe-creation semantics, DACL ownership, package identity, `PIPE_REJECT_REMOTE_CLIENTS` and first-instance handling must be independently tested before adoption. First-instance protection does not authenticate the server. The candidate mask is unverified and not used to create a pipe.

## Overlapped I/O and test package

The H.4 review found unresolved immediate-versus-pending completion, cancellation/drain races, event and buffer lifetime on exceptions, partial writes, disconnected or raced connects, message-size handling, and shutdown/restart. No corrected I/O state machine was implemented or claimed here. Public entry points remain disabled; the private draft is unreviewed and must not be invoked as a workaround.

`tests/backend/batch2b3h5_integration_runner.py` is a **fail-closed plan-only placeholder**, not the requested completed runner: it does not start any process, select an endpoint, exchange messages, or test timeouts. It lists the ten required scenarios and exits 2. Implementing the requested runner which starts a live broker is blocked until a reviewed, bounded listener exists; enabling the private draft to make a test run would violate that prerequisite. There is no environment variable, CLI flag or fallback to bypass this status.

For an operator-controlled, separately approved Windows environment, these exact commands perform only the current safe diagnostics and report the block; they do not start a VM or broker:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe' -I tests/backend/batch2b3h5_token_diagnostic.py
& 'C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe' -I tests/backend/batch2b3h5_integration_runner.py
```

The first prints package and capability SIDs, so share its output only as appropriate; the second reports `NOT_RUN` and returns 2. A future complete runner must use a unique endpoint, identify and terminate only its own child, exercise synthetic ping/status/capabilities and denials, duplicate instance, bounded timeout, disconnect and restart, and leave production and host configuration untouched. It must not be presented as an adversarial security test.

## Tests and results

The token diagnostic ran once and exited 0; the child exited 0. It is a read-only observation, not an IPC or effective-access success. With `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`, the specified interpreter ran `tests/backend/test_batch2b3h5_rights.py` and `tests/backend/test_batch2b3h3_dev_pipe.py`: **3 passed, 0 failed, 1 skipped**. The skip is the existing real IPC test. The integration plan-only runner returned expected exit 2 with **10 scenarios not run**: separate process, ping, status, capabilities, command denial, malformed/oversized, duplicate instance, timeout, disconnect and restart. The full suite was not collected or run. No network, Chroma, Ollama, Docker, VM, real IPC listener or production application was started.

## Next implementation work

Obtain an independently approved environment and verify its tokens, effective access and documented namespace with narrowly scoped rights; do not assume this managed AppContainer can be escaped. Independently implement and test a bounded overlapped state machine with live cancellation and buffer-lifetime evidence before adding an executable integration runner. Establish intended-server authentication and authorization, OS writer exclusion, storage identity, rollback protection and each consumer's policy separately. A future successful ping establishes functional IPC only. **Production remains NO-GO.**

References: [Microsoft AppContainer access model](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer), [named-pipe security and rights](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights), [CreateNamedPipeW](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createnamedpipew), and [overlapped I/O](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-server-using-overlapped-i-o).
