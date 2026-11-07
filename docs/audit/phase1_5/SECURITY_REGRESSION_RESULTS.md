# Security regression results

Isolated synchronous tests in `tests/backend/test_phase15_targeted.py`: 8 passed, 3 deselected, 2 warnings. These cover config binding/auth, production auth override, external font references and evaluation reporting. Async tests for upload size and multimodal dispatch were deselected because the environment cannot complete a minimal asyncio event loop; no result is claimed for those cases. Tests use mocks for some boundaries and do not establish end-to-end security.

Outstanding: session ownership, cross-user guessed IDs, legacy-data migration, concurrent writes, exact-limit and interrupted upload, multipart ingress limits, all-path clearance, registry-level tool disable, approval-to-action binding and live browser network capture. All remain unverified or unimplemented.
