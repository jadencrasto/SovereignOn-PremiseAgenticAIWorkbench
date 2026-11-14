# Independent freshness and authorization rollback assessment

**Decision: BLOCKED; production NO-GO.** No trustworthy independently administered freshness anchor is present in the production approval-consumption path. A real-module synthetic regression demonstrates that restoring an earlier valid SQLite database can restore an approved record that has since been consumed. After restoration the low-level `TaskStore.consume_approved_approval` primitive can consume it again. This does **not** show that the blocked application task-resume gate granted execution: the test calls the persistence primitive directly, and task content revalidation remains unavailable.

No local counter or matching hash was added to production as a substitute for an independent witness. Production authorization and recovery gates were not enabled or changed.

## Actual evidence and trust boundary

| Evidence | What it provides | What it cannot establish |
| --- | --- | --- |
| Approval `status`, IDs, timestamps and transaction state in TaskStore SQLite | Conditional single consumption and bounded SQLite writer admission for the current database. `expires_at` is checked against the local wall clock after obtaining the lock. | A restored approved row, clock rollback, replaced database, or independently modified DB can defeat its view of history. Transaction IDs and local timestamps are not externally anchored monotonic history. |
| Task event and audit tables | Useful application records; `AuditLogger` uses SQLite and its configured DB path, with retention pruning. | It is not independently protected immutable evidence and can be restored, changed or removed with the same storage/authority. No proof of a separate rollback domain was found. |
| Provenance source revisions and conversation generations | Internal identification and conservative pending/revoked handling. | Database-local generations and schema versions can roll back; they do not prove current owner, non-revocation or freshness of the database. |
| Same-directory sidecar or separate ordinary file | May detect accidental disagreement when separately checked. | An actor or restore process controlling both can replace both consistently; directory separation alone is not independent control or a monotonic witness. No such file was promoted to an anchor. |
| OS-protected state | Separately provisioned identity/ACL and protected append-only storage could contribute to an independent trust boundary if verified. | Merely using an OS file, service SID or ACL does not prove anti-rollback, independent administration, protection from privileged restores or an atomic witness protocol. No suitable production anchor was discovered. |
| Prototype `DurableRecovery` local witness | Synthetic chained generation/commit tests and quarantine decisions. | Report P explicitly states it is under the same authority and restore domain, so it is not an independently durable production witness or rollback resistance. |

`TaskStore` initialization creates its DB and tables if absent; `consume_approved_approval` itself now uses an existing database only. The application audit logger and TaskStore can use the same configured tasks DB. SQLite/WAL backups and restored copies must be treated as a coordinated set; no independently authenticated backup/restore checkpoint was found. File hashes or VM development evidence do not independently prove which valid historical database is current. Independent off-host verification is not required to be online: a separately administered offline hardware-backed or append-only authority might be suitable, but its threat model, monotonicity, authentication, crash ordering and rollback domain must be designed and verified.

## Added non-authorizing comparison and tests

New `prototype/freshness_assessment.py` compares **caller-supplied synthetic** generations and commitment format. It returns `MISSING`, `INVALID`, `MISMATCH`, or `MATCH_UNVERIFIED`; `authorization_available()` always returns false. It does not open storage, verify a witness, advance a counter, write production state or integrate into approval handling. Matching observations remain untrusted and cannot authorize. `tests/backend/test_freshness_assessment_offline.py` tests missing, corrupt, stale, matching and fail-closed decisions, and uses actual `TaskStore` against a temporary SQLite backup to reproduce the rollback limitation.

| Command (external pytest plugin autoload disabled) | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| `python -I -B tests/backend/test_freshness_assessment_offline.py` | 2 | 0 | 0 |
| `python -I -B tests/backend/test_storage_integrity_offline.py` | 13 | 0 | 0 |
| `python -I -B tests/backend/test_batch2b3e2_offline_stdlib.py` | 6 | 0 | 0 |

The last two include two-process consume, live terminal-state preservation, and PENDING/REVOKED preservation under denied ingestion. Those outcomes apply only before database rollback. SQLite backup/restore test uses generated temp files and ordinary local operations; it does not prove OS filesystem isolation or real independent rollback resistance. No Chroma, Ollama, FastAPI, Docker, VM, SCM, host security or network setting was accessed. Local child processes in the existing storage tests and temporary fixture files were used; no network monitoring was claimed.

## Next security boundary

Specify and independently provision a protected, authenticated witness outside the DB's restore domain, with unique deployment/incarnation identity, monotonic sequence, approved transaction and revocation commitments, atomic compare-and-advance, durable write semantics and an explicit crash/recovery protocol. Bind it to a restricted broker with verified Windows handle-based storage identity, ownership/ACL enforcement and coordinated writers. Missing, stale, corrupt, ambiguous, out-of-sync or unavailable evidence must remain denied; a database restore cannot itself reset the witness. Test witness-ahead/behind, crashes, backups and revocation across separately controlled domains before considering any authorization integration. This is a design prerequisite, not an assertion that a proposed technology is already secure.

Files added: `prototype/freshness_assessment.py`, `tests/backend/test_freshness_assessment_offline.py`, and this report. No production source or authorization gate changed. No commit, push or merge.
