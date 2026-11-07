# Security regression results

## Security implementation continuation, 2026-11-07

An isolated archive copy with modified modules/tests overlaid, pytest plugin autoload disabled and `pytest_asyncio.plugin` explicitly loaded: **57 passed, 0 failed, 0 skipped** in 9.04 seconds. A separate targeted RBAC/tool compatibility run yielded **99 passed** in 19.17 seconds, before the final simulated-clearance-header and task-owner changes. Included tests for a two-user in-process ASGI chat flow, legacy data, restart, same-process and two-process concurrent JSON writers, retrieval clearance ceilings with synthetic classification metadata, simultaneous request contexts, one-use SQLite approvals, mismatched arguments, disabled dispatch, multipart size boundaries and interrupted input, and configured launcher binding. HTTP/model and retrieval dependencies in these tests are fake or synthetic; no real model or production document was involved.

Full suite, external browser network validation, actual production host binding, application end-to-end workflows, and telemetry isolation remain BLOCKED / NOT RUN.

## Telemetry investigation, 2026-11-07

3 isolated fake-client configuration tests passed; they do not measure network traffic or prove real Chroma startup safe. A model-download-capable default embedding function was disabled for both collection creation paths. Exact telemetry source remains unknown. No full-suite retry was made; no OS-level egress isolation was demonstrated. See TELEMETRY_AND_EGRESS_AUDIT.md.

## Continuation, 2026-11-07

Isolated current-copy runs: 48 passed (memory, approval, plan validation, previous phase1.5 targeted tests and new clearance, persistence and registry tests); 103 passed (legacy tool/RBAC tests and initial continuation tests, after correcting tests for the deliberately stricter registry policy); 33 passed (task, recovery and ownership tests). The 103 and 48 test sets overlap; do not add them into a unique total. Three role/clearance combinations include admin with lower clearance. Approval dispatch tests include a temporary SQLite approval manager, mismatched arguments, viewer denial and valid approval. These checks do not establish conversation ownership, replay prevention or end-to-end authorization.

An initial older RBAC/tool run produced 8 failures and 91 passes: its tests assumed role-free dispatch and writes without approval. Tests were updated to provide roles for allowed read operations and expect denial without approval, then the 103-test run passed. No application policy was weakened to satisfy them.

Isolated synchronous tests in `tests/backend/test_phase15_targeted.py`: 8 passed, 3 deselected, 2 warnings. These cover config binding/auth, production auth override, external font references and evaluation reporting. Async tests for upload size and multimodal dispatch were deselected because the environment cannot complete a minimal asyncio event loop; no result is claimed for those cases. Tests use mocks for some boundaries and do not establish end-to-end security.

Outstanding: session ownership, cross-user guessed IDs, legacy-data migration, concurrent writes, exact-limit and interrupted upload, multipart ingress limits, all-path clearance, registry-level tool disable, approval-to-action binding and live browser network capture. All remain unverified or unimplemented.
