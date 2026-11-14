# Storage integrity and recovery foundation

**Production NO-GO.** This batch makes a narrow approval replay hardening change. It does not implement a protected storage broker, independent writer exclusion, trustworthy filesystem identity, independent freshness or production recovery. Existing document/task/artifact/code denial gates remain in place; no feature was enabled.

## Findings and changes

1. `TaskStore.save_approval` previously used `INSERT OR REPLACE`. Reusing an existing approval ID could replace a consumed row with an approved row. It now uses a plain `INSERT` and propagates an SQLite uniqueness failure. Existing rows and schemas are unchanged. This prevents that application-level replacement route; arbitrary direct SQLite writers, database replacement/rollback and caller-created new IDs remain outside this guarantee.
2. `TaskStore.update_approval_status` previously permitted any transition, including terminal-to-approved. It now supports only invalidating pending/approved records to rejected/expired. `update_approval_status_atomic` now permits only the existing pending-to-approved or pending-to-rejected transitions. Concurrent consume/invalidate operations use SQLite conditional statements; no consumed record is converted back by these methods. Invalidation of an already terminal/missing record remains a no-op. The `ApprovalManager` paths use these allowed transitions. Existing interfaces that relied on arbitrary status mutation or replacing IDs will now fail; full application compatibility has not been tested.
3. `ApprovalManager._is_expired` previously treated missing, malformed or timezone-incompatible expiry as not expired. It now treats them as expired. This is conservative and may reject historical malformed approvals. The test extracts this actual function body without importing Pydantic or initializing the application; it is not a full manager/route test. The database's `consume_approved_approval` still relies on lexical ISO timestamp comparison and wall-clock freshness; database rollback, clock manipulation, other direct writers and the check-to-consume boundary are not solved here.

Files changed: `backend/agent/task_store.py`, `backend/agent/approval.py`, `tests/backend/test_storage_integrity_offline.py` (new), and this report. All pre-existing working-tree modifications were preserved.

## Writer and filesystem boundary

SQLite's `BEGIN IMMEDIATE` and conditional `UPDATE` coordinate writers to the same live SQLite database. A bounded-contention test uses two separate Python processes and the existing one-second-or-shorter transaction helper, and a simultaneous conditional consume test permits only one winner. This does not lock Chroma, source files, conversation JSON, artifact files, database sidecars, independent database copies or a replaced database. The normal `TaskStore` connection uses the SQLite default busy timeout; no new cross-store lock or independent broker was added. A process can ignore cooperative locks or use pre-existing handles. Do not delete presumed stale lock files.

Windows path normalization, `Path.resolve()`, stat or link checks alone do not protect against reparse points, replacement, hard links or already-open writers. The approved F design requires independently provisioned protected roots and retained handle-based identity; those production guarantees and an independently administered freshness witness are unavailable. No unsupported path-based substitute was added and no claim of secure filesystem access is made. The test suite's snapshot comparison tests receive synthetic source bytes; they do not perform adversarial filesystem substitution. The egress-monitor durable evidence export remains unsupported. Artifact registration conflict protection exists in prior work but does not establish filesystem identity.

## Recovery and freshness

The new crash test ends an actual child process with an uncommitted SQLite update and confirms a new `TaskStore` observes the prior pending row. It does not demonstrate power-loss durability, real multi-store recovery or successful commit persistence under device failure. Prior e2 tests verify pending/revoked rows are not altered by currently denied ingestion; e tests verify supplied corrupt/missing/extra source and chunk snapshots remain non-authorizing. `mark_restart_unverified` does not authorize records. Ownership reassignment remains prevented only for task rows with existing provenance; legacy rows and direct database writers need additional review.

No independent freshness authority exists. A local clock or current SQLite row is not a rollback-resistant witness. No immutable model digest, protected source handle, complete index snapshot, valid Windows reparse guarantee or cross-store fence is fabricated. Keep persistent ingestion, RAG retrieval, document disclosure, task resume/approval, artifact reads and generated code execution denied until their own prerequisites are implemented and independently verified.

## Offline verification

External pytest plugin autoload disabled; Python 3 stdlib direct runners (`-I -B`), temporary synthetic SQLite/files and explicitly created short-lived local child Python processes. No Chroma, Ollama, FastAPI startup, Docker, SCM or network-sensitive tests. No OS-level network monitoring was established; these inspected test paths do not intentionally use network.

| Command | Final pass | Fail | Skip |
| --- | ---: | ---: | ---: |
| `python -I -B tests/backend/test_storage_integrity_offline.py` | 8 | 0 | 0 |
| `python -I -B tests/backend/test_batch2b3e2_offline_stdlib.py` | 6 | 0 | 0 |
| `python -I -B tests/backend/test_batch2b3e_offline_stdlib.py` | 7 | 0 | 0 |

The first version of the expanded storage test had **7 passes and 1 teardown error** because a test-owned SQLite connection was not closed before temporary directory cleanup on Windows. The test was fixed to close it and rerun: 8 passed. Its failed run may have left its temporary test directory; no unrelated files were removed. Tests use mocks for Chroma classification and do not initialize it.

## Remaining verification

Separately design and test cross-process exclusive storage ownership, Windows handle-relative no-reparse traversal and live file identity, independent anti-rollback witness, complete Chroma namespace verification and interruption/revocation races. Establish a monitored isolated environment before real Chroma or other network-sensitive dependencies. Review full approval integration and legacy caller compatibility under suitable isolation. Demonstrate tampered database/rollback, source replacement and reparse attacks using authorized native synthetic fixtures, with no destructive operation on unknown resources. Independent security and operational review is required before release.
