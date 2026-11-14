# Batch 2B.3H.2 — Windows IPC implementation

Date: 2026-11-12. Branch `phase1-5-recovery-security`. **PARTIALLY IMPLEMENTED. Production NO-GO.** No named-pipe listener, separate-process broker, client-side server authentication, or IPC exchange was implemented or run. The existing transport remains fail-closed. This does not satisfy the mission's acceptance criteria.

## Environment and implemented scope

The explicitly requested interpreter `C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe` exists and was used without substitution. It reports CPython 3.13.7 AMD64 and pywin32 312; imports of `win32pipe`, `win32security`, `win32file` and `pywintypes` succeeded. Thus the missing binding from 2B.3H.1 is **resolved for this interpreter**. No dependency or host setting was changed.

Added `prototype/windows_broker_ipc/peer_identity.py`, an **unwired** helper for checking a connected pipe client's token against a trusted configured SID. It lazily obtains pywin32 APIs, calls `ConvertStringSidToSid`, `ImpersonateNamedPipeClient`, `OpenThreadToken`, `GetTokenInformation` for impersonation level and token user, compares canonical SID strings, closes the token and calls `RevertToSelf`. An insufficient impersonation level, API failure or cleanup error produces a denial. A failed revert means the caller's thread must not be reused and the future host must stop; no running broker currently invokes this helper. Its trusted SID argument is a prospective server configuration input, not a request field. A same-SID attacker is not distinguishable by this check.

Read-only inspection confirmed the required symbols exist in the installed bindings. Notably, `EqualSid` is not exported by this pywin32 module, so the helper uses `ConvertSidToStringSid`; `CancelIoEx` was not present in the inspected `win32file` surface. These checks only establish symbol presence/absence, not the behavior of those APIs or the safety of real handles. `win32security.ImpersonateNamedPipeClient` is present. No call to these identity APIs was made against a real pipe.

Added `tests/backend/test_batch2b3h2_peer_identity.py` with fake pywin32 APIs and this report. The existing `server.py`, `client.py` and `transport.py` are unchanged and continue to refuse service; no production code, authorization gate, storage, FastAPI integration, VM, network configuration or evidence was modified. No commit, push, merge or deployment occurred.

## Specific remaining blockers

Client-side verification of the **intended server** is unresolved. A pipe server PID and a same-user SID do not by themselves prove that the connecting endpoint is the intended broker: another process under the same account can occupy the pipe name and hold the same SID, and PID lookup/handle lifetime must be handled without a race. There is no separately protected broker identity or trusted launcher/handle-binding protocol in the present deployment design. Endpoint ACL construction, allowed-SID policy protection, first-instance ownership under squatting, safe client and server authentication, bounded overlapped reads/writes, cancellation completion/handle lifetime, shutdown and sequential-client handling are also not implemented or independently verified. The available binding alone does not resolve these. A same-user-only test would not establish isolation from a same-account attacker.

Therefore no explicit DACL, `PIPE_REJECT_REMOTE_CLIENTS`, `FILE_FLAG_FIRST_PIPE_INSTANCE`, named-pipe endpoint, command authorization, timeouts or IPC framing was enabled or claimed. Existing bounded JSON parsing is an in-process parser, not a transport or authentication. The helper's token comparison is not a complete client authorization decision and must not be wired into production based on these tests.

## Offline verification

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe' -I tests/backend/test_batch2b3h2_peer_identity.py -v
& 'C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe' -I tests/backend/test_batch2b3h_ipc_offline.py -v
```

Final results: **12 passed, 0 failed, 0 skipped** (6 new mocked token-control-flow tests and 6 existing parser tests). These tests use no listener, real token, native pipe, network, Chroma, Ollama, Docker, VM or full suite. An initial run of the new tests yielded **5 passed, 1 failed** because the anonymous-token test set a mock's `return_value` while its earlier `side_effect` was still active; the fixture was corrected, then all 6 passed. `git diff --check` exited 0 with existing line-ending warnings; untracked files are not included.

## Separate-process verification plan — not run

No real broker exists to exercise. Once the server-authentication trust anchor and full transport are reviewed and implemented, a separately isolated Windows environment must test authorized separate-process ping, distinct unauthorized OS identity, same-account squatting, first-instance refusal, malicious/malformed and oversized messages, stalled/interrupted clients, bounded cancellation and process shutdown/restart, and absence of production storage access. Tests must use controlled synthetic data, per-run endpoint names and no production credentials. They must verify process and endpoint identities through trusted OS evidence, not request fields. A matching SID alone must not be represented as proof against same-account attackers. These are proposed integration scenarios, not runnable completed tests and not evidence of current IPC security.

## Next step

Choose and independently review a practical intended-server trust anchor for the development environment, such as a trusted launcher that retains and validates an appropriate process/endpoint binding or a separately provisioned broker service identity, including its same-user threat model. Then implement and review the complete explicit-DACL local pipe, token authorization, overlapped cancellation and client authentication together. Keep production storage and authorization gates closed. **Production remains NO-GO.**
