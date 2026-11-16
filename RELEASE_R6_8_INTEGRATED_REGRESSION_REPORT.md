# R6.8 — Integrated regression consolidation

**R6.8: PARTIAL. Local demonstration: BLOCKED. Production: NO-GO.** Starting branch `main`, HEAD `2b96fee1f24777247f4efb7a18293eef4275e9cb`; existing uncommitted changes retained. This milestone changes only this report. R6.1/R6.2 provider and SSE fixes, R6.3 partial-message persistence, R6.4 process-local incomplete-message MAC, R6.6 fixture corrections and R6.7 approval-stream changes were tested together where backend tests exist. R6.4 durable key and R6.5 frontend execution remain blocked.

| Workflow | Expected behavior | Existing evidence | Current verification / gap |
| --- | --- | --- | --- |
| Ordinary chat | Complete and persist once | `test_r51_core_slice`, `test_a21_conversation_offline` | Offline synthetic routes; live model unverified. |
| Incomplete provider response | Error, no successful done | `test_a2_provider_offline` | Offline synthetic provider; real Ollama unverified. |
| Provider failure after partial | Failed state, no done | `test_a2_provider_offline` | Offline route; detection buffer is not persisted. |
| User cancellation | Stop displaying; avoid false success | `test_a21_conversation_offline`, `test_a31_planning_offline` | Synthetic generator/task cancellation; frontend not executed. |
| Client disconnect | No fabricated completion | `test_a21_conversation_offline` | Synthetic closure; real transport disconnect unverified. |
| Planning failure | Terminal failure without done | `test_r51_core_slice`, `test_a31_planning_offline` | Offline routes/fixtures; live inference unverified. |
| Tool followed by response failure | No fabricated completion or automatic retry | `test_a33_production_tools_offline`; frontend fixture | Backend synthetic boundaries; frontend fixture not executed and side effects remain uncertain. |
| Approval denied | Error, no dispatch or done | `test_r67_approval_stream_offline` | Offline production route; production resume remains blocked. |
| Approval expired | No invalid approval execution | `test_a31_planning_offline` / approval gates | Resume blocked; explicit expired approval through SSE not demonstrated in this run. |
| Conversation reload | Owned history from server | `test_r51_core_slice`, `test_a21_conversation_offline` | Offline; browser reload unverified. |
| Interrupted message reload | Retain state, expose only authorized content | `test_a21_conversation_offline` | State persists; readable text after restart blocked. |
| Backend restart | Do not trust process-local proof | `test_a21_conversation_offline` | New memory instance rejects old proof; full abrupt process restart not run. |
| Cross-conversation isolation | No receipt/proof transfer | `test_a21_conversation_offline`, `test_i112_numeric_receipts` | Offline synthetic, not all concurrent stream races. |
| Cross-user authorization | Deny other owners | `test_r51_core_slice`, `test_r67_approval_stream_offline` | Offline in-process route. |
| Model readiness expiry | Deny stale readiness | `test_r12_readiness_offline` | Synthetic model/clock; real endpoint and digest unverified. |
| Unsafe tool retry | No automatic retry after uncertain outcome | `ChatView.tsx`, `a22-chatview-handlers.test.cjs` | Source inspected; frontend test not executed; no backend exactly-once guarantee. |
| Frontend stale stream | Old callbacks cannot update selected conversation | `guardChatCallbacks`, frontend tests | Source inspected; frontend runtime not verified. |
| Logout during generation | Old session cannot update new session | `App.tsx`, `ChatView.tsx`, `i2-logout-client.test.cjs` | Source inspected; mounted logout during stream not tested. |
| Conversation deletion | Deny stale writes and access | `test_a21_conversation_offline`, `test_r51_core_slice` | Offline ownership/deletion; all transport races not proven. |
| Resource cleanup | Close provider and stream generators | `test_a2_provider_offline`, `test_r67_approval_stream_offline` | Synthetic closure; native processes and actual provider not tested. |

The JSON and multipart frontend parsers treat error, task failure, cancellation and done as terminal. The backend emits task failure/cancellation without subsequent done on the tested planning, tracked-tool and approval-interception paths. `done` can also end an approval-pending stream and does not itself prove task success. Frontend callback guards and retry suppression are source-inspected only; the new R6.6 fixtures remain unexecuted. No new source defect was corrected in R6.8.

The conversation store is JSON-backed with SQLite used for separate task/security records. New independently authenticated incomplete messages bind state, identity, content and time to a **process-local** MAC; incomplete messages are excluded from future model input. The API's status field is not itself an independent authentication result. Legacy v1 message proofs do not bind status, ID or timestamp; durable proofs and restart-readable text remain blocked by R6.4. Plain streaming records only emitted text on finalization/closure, not every token, and process loss before finalization has no checkpoint guarantee. Verified numeric receipts remain process-local; side effects and external stores are not atomically committed with task or conversation state. No automatic replay or exactly-once claim is made. The protected writer, document/artifact gates, role checks and approval-resume block remain in force.

## Integrated backend verification

Repository root; synthetic providers, temporary files/SQLite and in-process ASGI:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_r67_approval_stream_offline.py tests/backend/test_a2_provider_offline.py tests/backend/test_r51_core_slice.py tests/backend/test_r12_readiness_offline.py tests/backend/test_a21_conversation_offline.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a31_planning_offline.py tests/backend/test_a33_production_tools_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_r41_shared_storage_gate.py
git diff --check
```

**106 passed, 0 failed, 0 skipped; 1 warning; 19.04 seconds.** Warning: pytest unknown `asyncio_mode` configuration option with plugin autoload disabled. The Python pytest process was launched; no additional native test process, application or network service was intentionally started. The `run_offline` ASGI helper patches socket creation for tests using it; this is not proof of system-wide network isolation for the entire command. No external hard timeout was configured and no interruption occurred. `git diff --check` passed.

Frontend tests, TypeScript, build, mounted React, browser, live startup and Ollama remain **NOT EXECUTED / NOT VERIFIED**. A restricted local chat demonstration still needs reviewed frontend dependency/executable provenance, explicit operation-specific frontend/build approval, independently verified process ownership and cleanup, monitored network isolation and loopback policy, verified local Ollama/runtime and model identity, authenticated readiness, synthetic test accounts/data, and an approved command/deadline/cleanup plan. No claim is made that these exist. No services, VM, restricted frontend tooling, protected operations or disabled launcher were started; R5.5 and R6.4 were not modified.

**Next action:** obtain operator/security approval for the reviewed isolated frontend verification environment and its exact test commands.
