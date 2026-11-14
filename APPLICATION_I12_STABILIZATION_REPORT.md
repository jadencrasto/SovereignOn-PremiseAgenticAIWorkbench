# I12 core stabilization

**I12: PARTIAL. Production: NO-GO.** The conversation-revocation race during receipt lookup now returns a sanitized 404 instead of allowing a permission exception to escape as an internal server error. This check uses the existing conversation authority and does not create a receipt or dispatch a task after denial.

Streaming numeric follow-ups reuse the process-local receipt and validated task workflow from I11.2. Non-streaming follow-ups remain fail-closed with HTTP 409 because the current non-streaming path uses `engine.chat` and does not create an authoritative completed task and verified tool result. No assistant prose is used as evidence.

Receipts are immutable process-local objects bound to owner, conversation generation, completed task and invocation. The existing memory store applies a 15-minute expiry, requires the immediately preceding eligible message, bounds retained entries and removes them on conversation deletion. Existing tests exercise expiry, account and conversation isolation, authorization loss before dispatch, interrupted execution and memory-instance replacement. Adjacency and receipt identity are rechecked before dispatch; these in-process checks do not establish cross-process atomicity. Overlapping actual requests were not exercised in this run, and multi-step final-result selection remains unsupported.

Verification from the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q tests/backend/test_i112_numeric_receipts.py tests/backend/test_i111_followup_boundary.py tests/backend/test_i6_workflow_offline.py tests/backend/test_i4_workflow_offline.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_a31_planning_offline.py tests/backend/test_a61_multimodal_offline.py::test_real_chat_route_image_integration_and_ownership tests/backend/test_a61_multimodal_offline.py::test_real_image_route_provider_failure_is_error_not_completion tests/backend/test_a72_verified_report_offline.py::test_report_request_reaches_planner_and_fails_closed_without_source
```

**50 passed, 1 warning in 14.36s.** The warning is the unknown pytest `asyncio_mode` option with plugin autoload disabled. Tests use synthetic identities/providers, temporary storage, production modules and in-process ASGI or immediate/timer-only harnesses. No real services or sockets were used. No external hard timeout was configured and none occurred. The new deterministic lookup test injects revocation after the initial identity check and confirms 404, no exception disclosure and no task creation.

From `frontend`:

```powershell
node --test tests/a22-chatview-handlers.test.cjs tests/a2-stream-client.test.cjs tests/a61-chat-attachment.test.cjs
```

**25 passed, no warnings in 1.94s.** These use simulated hooks and mocked fetch, not mounted React or browser interaction. Historical interrupted test runs remain interrupted and were not rerun.

Existing document retrieval, artifact writing/publication, generated-code execution, approval-resume and provenance restrictions remain in force. **TypeScript, production build, mounted React, browser and live Ollama remain NOT VERIFIED.** The earlier automatic approval review rejected frontend build continuation because of possible registry access; no indirect retry was attempted. Next, add deterministic overlapping-stream and deletion tests with a reviewed ordering contract, then consider reusing the same verified task execution for non-streaming requests.
