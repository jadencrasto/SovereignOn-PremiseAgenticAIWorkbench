# Production SQLite approval-consumption boundary

**Production NO-GO.** The selected boundary is the existing `TaskStore.consume_approved_approval` operation. It is a single SQLite-backed transition, requires no Chroma, HTTP, application startup or artifact filesystem access, and can be tested by importing the actual stdlib-only production persistence module. SQLite itself provides the cross-process writer exclusion for one live database; the synthetic cross-store fixture was **not** integrated because its path and lock identity cannot safely protect production data. This does not enable approval, task resume or any other denied workflow.

## Actual change and guarantee

`backend/agent/task_store.py` now opens the existing database using SQLite URI `mode=rw` (missing DB is not created), with a one-second busy timeout, and obtains `BEGIN IMMEDIATE` before reading an approved row. It parses and validates a timezone-aware expiry and reads the wall clock **after** acquiring the writer lock. Missing, malformed, naive or expired timestamps return false. It then conditionally consumes the same approved row/expiry and commits; exceptions roll back if necessary and propagate, including busy or unavailable database. There is no fallback to a process-only lock. The pre-existing process mutex is acquired before SQLite; no other lock or external store is acquired inside the transaction.

Previously the timestamp was taken before the update's potential SQLite lock wait, so an approval that expired during the wait could be compared to a stale time. SQLite already made the conditional update atomic; the additional guarantee is the bounded explicit writer admission and the expiry decision made after admission, not newly invented cross-store exclusion. A direct caller remains responsible for authorization and binding to task, owner and arguments. SQLite's lock coordinates writers to the same live DB; it does not prevent direct unauthorized SQL, database replacement/rollback, clock manipulation, or access to a different DB. No Windows ancestry, inode or handle identity is asserted. A missing DB is detectable; replacement with another valid database at the same path is **not reliably detected** and remains a blocker. Constructor behavior elsewhere may create a database; this change only affects the consume method after construction.

## Tests

Changed `tests/backend/test_storage_integrity_offline.py` exercises the real imported `TaskStore` against synthetic temporary SQLite DBs. It does not import FastAPI or Pydantic. External pytest plugin autoload was disabled; `python -I -B` used. Local child Python processes are explicitly created for tests; no network use is intended or monitored.

| Command | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| `python -I -B tests/backend/test_storage_integrity_offline.py` | 13 | 0 | 0 |
| `python -I -B tests/backend/test_batch2b3e2_offline_stdlib.py` | 6 | 0 | 0 |

The real-module tests cover two independent processes racing to consume, SQLite contention, no fallback on a held writer lock, duplicate and consumed-ID replay, normal invalidation and protected-task ownership, restart after child-process uncommitted-write termination, injected interruption after the actual consume update (rollback), malformed/naive/stale expiry, a deterministic lock-before-clock check, and missing DB refusal without recreation. The child-crash test interrupts a synthetic SQLite transaction, not the production consume method mid-commit. The e2 tests confirm PENDING/REVOKED rows remain unchanged under denied ingestion. No valid-database replacement, malicious writer, reparse-point, OS power-loss, independently controlled clock or actual cross-store test is claimed. Full ApprovalManager/FastAPI integration and previous pytest suite were not run, so compatibility beyond reviewed call sites remains to be verified in an appropriately isolated environment.

## Boundaries and next step

Conversation registry/provenance already use `immediate_transaction` for short SQLite transitions and may be reviewed for analogous bounded checks; do not assume a single per-database lock coordinates JSON memory. TaskStore other write methods, audit records and artifact metadata need separate transaction/ownership review. Chroma, uploads, artifact bytes, conversation JSON, backups and independent maintenance writers **do not share this protection**. No cross-store fixture, Chroma, Ollama, FastAPI startup, Docker, service, VM, firewall or network policy was accessed.

Next, review the per-database security transition callers and their rollback/identity requirements; implement a trusted restricted broker and pinned Windows handle-based root only after separately authorized native reparse/ACL/process tests. An independently administered freshness witness and recovery protocol remain prerequisites. Do not extend the SQLite primitive to file/Chroma operations under a false security claim.
