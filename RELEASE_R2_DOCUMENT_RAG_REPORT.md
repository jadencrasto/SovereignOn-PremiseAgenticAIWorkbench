# R2.1 Document and RAG integration

**R2.1: PARTIAL. Production RAG: BLOCKED. Production: NO-GO.** Source lifecycle and reconciliation checks are stronger, and a synthetic internal grounding workflow passes. The existing production retriever has no authorized positive path, and this milestone does not establish trusted storage, independent freshness or real persistent-index integrity.

## Existing path and changes

The existing API calls `DocumentService`, which blocks ingestion before source reservation because independently verified persistent storage is unavailable. The intended path uses server-issued quarantined source IDs, owner and clearance, source bytes/digest, revision 1, parser/chunker, embeddings, a Chroma adapter, source manifest and readback. Protected retrieval is denied by `document_content_allowed()`; the source registry never grants retrieval. The grounding helpers are internal and are not connected to production routes. PDF/DOCX/TXT/Markdown parsing, deletion, restart and reconciliation foundations were inspected alongside the A5, I9 and Phase 1.5 gates.

Three related changes were made:

1. **Quarantined lifecycle validation.** Manifest registration now requires a nonempty, ordered, source-bound chunk manifest, well-formed hashes, explicit model, positive dimensions and integer revision 1. Duplicate JSON fields, invalid numeric constants and malformed configuration fail before mutation. The transition checks owner, current clearance, pending state, canonical storage reference and a well-formed source digest. Readback rechecks the stored manifest/configuration and digest shape, requires a boolean durability value, and remains one-shot. Revocation records cleanup review and clears the verification timestamp. Registry reads now close their SQLite connection. Digests and metadata remain assertions, not ownership or access evidence. Existing `manifest_pending` callers must now supply a valid configuration; this intentionally rejects prior empty diagnostic manifests.
2. **Index and readback consistency.** The existing snapshot comparator rejects missing/invalid owner or classification, unsupported revisions, non-integer revision/dimension metadata, noncontiguous chunk indices, duplicate JSON fields and clearance mismatch. When an index generation is supplied, its identity must match the configuration, snapshot and chunk metadata; absence of generation is not treated as a verified generation. The existing ingestion readback path checks source/revision, chunk index, model, clearance and text digest with strict integer typing. It cannot verify embedding values through that readback API. A snapshot match still returns only `match_unverified`, with no registry mutation or access grant.
3. **Grounded-evidence boundary.** The internal answer helper now requires an explicit evidence verifier and checks every selected passage before generation and every included passage after generation, in addition to the existing current-scope checks. A denied-only candidate set raises an error instead of returning an empty successful result. Direct blocked vector chunk reads now raise `PermissionError` instead of returning an empty chunk result. These callback interfaces are non-authorizing; a caller-supplied `True` is not a production identity, source or freshness authority.

## Synthetic evidence

The new offline test reserves a source in the existing SQLite registry, parses and chunks synthetic text, records and reads back its manifest, marks restart status, compares a fake index snapshot, selects synthetic candidates and produces a request-scoped citation. A **test-only** scope and evidence verifier compare the candidate to fixture records and recheck the pending record; they are not a production permission system. Tests reject cross-owner use, forged text/filename, revocation during generation, replaced source bytes, missing chunks, wrong source/clearance/generation, invalid vectors and corrupt metadata. Duplicate upload creates a distinct server ID; duplicate callbacks are denied. Storage failure propagates without granting eligibility. Existing tests confirm the production retrieval and ingestion gates remain closed.

The positive path uses the existing grounding selector with caller-supplied synthetic candidate rows; it does **not** execute a real vector search, the production `Retriever` positive path, Chroma, or a real model. The fake snapshot's backend status is test data, not proof of persistence. Citation checks establish only structural/source dependencies under the supplied callbacks, not factual entailment, stable source ownership, atomic revocation or independently verified model/index identity.

## Verification

From the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_r21_source_workflow.py tests/backend/test_i91_source_lifecycle.py tests/backend/test_i9_document_foundation.py tests/backend/test_a52_grounding_offline.py tests/backend/test_a51_rag_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_batch2b3e_offline_stdlib.py tests/backend/test_a61_multimodal_offline.py::test_synthetic_pdf_summary_uses_extracted_passage_and_citation tests/backend/test_batch2b2a_content_boundary.py::test_direct_vector_read_apis_do_not_expose_content tests/backend/test_batch2b2a_content_boundary.py::test_index_cleanup_still_removes_synthetic_document
```

**59 passed, 3 warnings in 3.82 s.** With plugin autoload disabled, pytest warned about the unknown `asyncio_mode` option and two unregistered asyncio marks in the collected boundary module; the selected tests passed. An earlier related run passed 58 with three async cases deselected. Tests use temporary synthetic data, fixture callbacks and fake indexes. No timeout occurred, and no Ollama, Chroma, application service or real socket was started. `git diff --check` passed for the changed tracked files.

## Remaining gate and next task

There is no ACTIVE source state or independently current owner/session/clearance authority for source disclosure. Establish a protected storage identity and writer boundary, independently verified revision/index-generation and embedding/model association, crash/restart reconciliation and revocation/rollback resistance, then connect a real current authorization and source-bound citation verifier to the existing retrieval path. Test that boundary in a separately authorized isolated environment before considering any production gate change. Frontend TypeScript, build and mounted browser remain unverified. No protected ingestion, retrieval, artifact publication, launcher, service, download, commit or push was enabled or performed.

## R2.2 — SQLite source transitions and protected cleanup

**R2.2: PARTIAL. R2: PARTIAL. Production RAG: BLOCKED. Production: NO-GO.** The existing SQLite quarantine transitions now detect additional inconsistent metadata and a direct cleanup path is blocked while storage guarantees are unavailable. There is no committed or retrieval-authorized source state, safe replacement transaction, or independently verified writer boundary.

### Ownership and defects

`QuarantinedSources` uses the existing SQLite `BEGIN IMMEDIATE` transaction for reservations, manifest/readback transitions, revocations and restart markers. The gated ingestion path stages a source file with exclusive create, parses/chunks and embeds it, then writes through the vector adapter and records readback; these operations do not share a transaction with SQLite. The adapter also exposes low-level maintenance operations to trusted in-process code. The Windows broker, filesystem identity and durable recovery components inspected are prototype/diagnostic foundations, not an established authorizing production writer boundary. A SQLite lock does not fence direct filesystem or vector-store writers.

Two concrete defects were corrected:

- A syntactically valid index configuration or source digest could be changed after `record_manifest` and still pass `record_readback`. The existing registry now stores an additive `manifest_binding_sha256` in the same SQLite transaction as the one-shot manifest transition. It covers source ID, owner, clearance, revision, source digest, storage reference, manifest, model and exact index configuration (including generation if supplied). Readback recomputes the binding inside its transaction before changing status. It rejects missing/changed binding, stale revision, revocation, repeated callbacks, unknown prior state and restarted work. An unkeyed digest in the same database is only consistency evidence: a writer who can rewrite the database can recompute it, and rollback is not detected. Existing rows without a binding are not backfilled or promoted.
- `DocumentService.delete_document` could revoke a source and then attempt direct index/file cleanup despite unverified storage. It now commits the local revocation first and stops before physical index/file access when the existing persistent-security guarantee is unavailable. The API reports sanitized 503 `DOCUMENT_UNAVAILABLE`. This is an intentional behavior change: revocation may succeed while physical deletion is deferred, so the operator must not assume data was erased. The old direct cleanup code remains unreachable under the current false guarantee; no broker bypass was added.

Registry reads now open an existing database read-only and mutations require an existing database, so a missing store does not silently create a new empty database. Restart marking preserves unknown reconciliation values rather than overwriting them with a normal marker. No unknown file deletion is performed by these changes.

### Commit, recovery and freshness limits

The local sequence remains reservation → manifest pending → non-authorizing readback observation; a restart marks pending work unverified and revocation prevents further callbacks. SQLite atomically commits or rolls back each metadata transition, but no transaction spans content staging, embeddings or Chroma. `readback_verified_*` is an observation label, not a committed or retrievable source. Production `retrieval_eligible()` and `document_content_allowed()` remain false, so a successful SQLite read, metadata binding or matching fake snapshot cannot authorize retrieval. Existing synthetic citation checks still require explicit scope and evidence callbacks; no trustworthy production evidence provider or retriever positive path was introduced.

Revision 1 is the only supported document revision. Replacement under the same ID is **not implemented**; a stale/revised row is rejected by the tested transition. Do not infer that old chunks can be safely retired or a new revision activated. A trustworthy cross-store transaction identity, broker-enforced writer exclusion, source/index generation commitment and independent freshness witness remain outstanding. Native Windows ACL/handle identity, cross-process hostile-writer exclusion, real Chroma integrity, abrupt-process recovery and power-loss durability were not tested. The pre-existing synthetic broker and recovery results do not establish those guarantees here.

### Verification

From the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/backend/test_r22_source_transactions.py tests/backend/test_r21_source_workflow.py tests/backend/test_i91_source_lifecycle.py tests/backend/test_i9_document_foundation.py tests/backend/test_a52_grounding_offline.py tests/backend/test_a51_rag_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_batch2b3e_offline_stdlib.py tests/backend/test_a61_multimodal_offline.py::test_synthetic_pdf_summary_uses_extracted_passage_and_citation tests/backend/test_batch2b2a_content_boundary.py::test_direct_vector_read_apis_do_not_expose_content tests/backend/test_batch2b2a_content_boundary.py::test_index_cleanup_is_blocked_without_verified_storage
```

**74 passed, 3 warnings in 5.38 s.** Warnings: unknown pytest `asyncio_mode` and two unregistered asyncio marks because plugin autoload was disabled. Tests use actual temporary SQLite databases and transactions, a SQLite abort trigger, separate SQLite connections in synchronized threads, an actual SQLite writer lock with explicit lock failure, synthetic data, and fake index/citation fixtures. They verify atomic manifest/binding rollback, one successful competing metadata transition, stale revision, duplicate callback, config changes, restart, revocation, unknown metadata and missing database behavior. The successful readback remains pending and unavailable. They do not test independent processes, OS ACLs, crash/power loss or Chroma. No test timed out. Earlier focused runs passed 15, 66, 72 and 73 tests as changes were added; the initial 15-test run had one pytest warning, and the broader runs had three warnings. `git diff --check` passed with one Windows LF/CRLF warning.

No real service, model, Chroma, launcher, VM, frontend build, TypeScript or browser was started; no dependencies were downloaded and no commit, merge or push was made.

Next implement and independently verify a broker-owned, revision-bound source/index transaction and recovery protocol using protected handles, durable fencing and an independent freshness witness. First establish native Windows writer exclusion and the exact source/index ownership boundary in a separately authorized isolated environment; until then keep ingestion, retrieval and physical cleanup blocked.

A follow-up focused check, `python -m pytest -q tests/backend/test_r22_source_transactions.py` with the same plugin setting, passed **16 tests, 1 warning in 2.76 s**, including a new cross-owner and changed-source-digest readback rejection case. The warning was the same unknown `asyncio_mode` option.

## R2.3 — Durable replacement intent and blocked recovery

**R2.3: PARTIAL. R2: PARTIAL. Production RAG: BLOCKED. Production: NO-GO.** A non-authorizing replacement intent is now persisted in the existing SQLite source registry and participates in restart and revocation handling. The requested staging, cross-store reconciliation and authoritative replacement commit are **not implemented**, because no trusted filesystem/index writer or independent freshness boundary is available. This is not a crash-consistent cross-store replacement protocol.

### Commit sequence and failure windows

The current gated ingestion sequence is SQLite source reservation, exclusive file creation, parsing/chunking, embedding, vector update, then SQLite manifest/readback observation. File, vector and database changes are not atomic together. A crash can leave a reserved source without content, a staged file without a complete index, or partial index data without readback. `readback_verified_*` remains an observation, not a committed source or access grant. Current production ingestion, retrieval and physical cleanup remain blocked. The separately inspected Windows broker and recovery code is not a verified production storage authority.

### Implementation

Additive columns on the existing `quarantined_source_registry` record a single replacement operation: server-issued operation ID, expected/proposed source revision, expected/proposed index-generation IDs, candidate content digest, stage and decision. A unique SQLite index rejects duplicate outstanding operation IDs. `begin_replacement` uses the existing owner and clearance context and a bounded `BEGIN IMMEDIATE` transaction; it requires the exact revision 1, existing pending readback observation, unchanged manifest binding, explicit recorded index generation and no previous replacement. It rejects an absent or stale source, missing generation, conflicting operation or corrupt metadata. It records only the candidate digest, not candidate bytes. The proposed generation is a server-issued identifier, not evidence that an index exists or is trustworthy. The existing production ingestion configuration does not establish an independently trusted index generation, so this is not an operational upload or replacement route.

The only initial stage is `storage_boundary_required` and the only decision is `undecided`. The existing manifest/readback transitions refuse to advance a row with an outstanding intent. Existing startup `mark_restart_unverified` moves a recognized intent to `recovery_required`; existing revocation records `revoked_recovery_required` in the same SQLite update. Owner-scoped `replacement_status` classifies malformed journal fields as `corrupt_operation`, a changed revision as `revision_conflict`, and otherwise reports blocked/recovery or revoked state. Unknown fields are not promoted or silently repaired. No API accepts client-selected stage, commit or operation ID, no arbitrary file is deleted, and there is no operation method to stage, abort, commit or activate. No source revision or source content is replaced. The source registry and global document authorization still return false for retrieval, irrespective of an intent or status.

The journal fields and uniqueness index provide SQLite metadata coordination only. They do not authenticate the database against an independent writer, protect against rollback, prove current account authorization at final use, validate a filesystem handle or establish Chroma writer exclusion. The current unique index covers retained rows; any future operation cleanup must preserve durable replay protection. There is no verified discard decision: even an intent created before staging is not proof that an unknown external writer did nothing.

### Verification

From the repository root with `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'`:

```powershell
python -m pytest -q tests/backend/test_r23_replacement_intents.py tests/backend/test_r22_source_transactions.py tests/backend/test_r21_source_workflow.py tests/backend/test_i91_source_lifecycle.py tests/backend/test_i9_document_foundation.py tests/backend/test_a52_grounding_offline.py tests/backend/test_a51_rag_offline.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_batch2b3e_offline_stdlib.py tests/backend/test_a61_multimodal_offline.py::test_synthetic_pdf_summary_uses_extracted_passage_and_citation tests/backend/test_batch2b2a_content_boundary.py::test_direct_vector_read_apis_do_not_expose_content tests/backend/test_batch2b2a_content_boundary.py::test_index_cleanup_is_blocked_without_verified_storage
python -m pytest -q tests/backend/test_r23_replacement_intents.py
```

The broad run passed **88 tests, 3 warnings in 7.13 s**. The targeted run, after adding a guard against reusing the old generation ID, passed **13 tests, 1 warning in 0.93 s**. Pytest warned about the unknown `asyncio_mode` option; the broad run also reported two unknown asyncio marks because plugin autoload was disabled. A preliminary four-file run passed 29 tests with one warning in 4.76 s. No test timed out. `git diff --check` passed for the changed tracked source file.

Tests use temporary real SQLite databases, an abort trigger, synchronized threads and separate SQLite connections; they cover durable intent recording, rollback, duplicate ID, one winner under concurrent requests, stale revision, absent generation, interrupted intent and restart, corrupted intent, revocation, owner isolation and unchanged blocked RAG gates. Existing synthetic citation and index-generation mismatch tests also passed. These are not cross-process or power-loss tests. Faults *after real candidate staging, during a real index write, or before a real final commit* could not be tested: none of those operations is implemented or authorized. No Chroma, Ollama, application server, Protected profile, VM, socket-sensitive harness or frontend build was run; no dependency was downloaded and no commit/push/merge occurred.

Next, independently verify the existing restricted broker's process identity, protected handles and exclusive writer controls in an authorized isolated Windows environment. Then integrate a broker-issued source/index staging and readback contract, durable transaction identity and independent freshness witness before implementing revision commit or any production retrieval. Treat unknown external objects as recovery blocked, and never delete them merely because an SQLite intent exists.
