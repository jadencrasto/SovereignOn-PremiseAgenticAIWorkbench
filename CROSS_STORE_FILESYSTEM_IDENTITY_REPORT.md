# Cross-store coordination and filesystem identity

**Status: synthetic foundation only; production cross-store isolation BLOCKED. Production NO-GO.** The prototype does not guard production writers and does not grant or enable any workflow. The application cannot safely enforce a shared cross-store lock until storage roots, writer identities, lock identity and all independent writers are protected by an independently verified OS boundary. Existing ingestion, retrieval, task resume, protected artifact access and generated-code gates remain unchanged.

## Files changed

- `prototype/cross_store_coordination.py`: standalone non-authorizing fixture. Existing-database-only SQLite `BEGIN IMMEDIATE` provides a bounded, global cooperative lock; nested acquisitions fail, cleanup always rolls back and closes the connection, and no lockfile is deleted or recreated. A read-only file observation compares `lstat` and `fstat` identities, rejects final-component symbolic/reparse flags, non-regular files, ambiguous device/inode and multi-link files; it returns only `OBSERVED_UNVERIFIED`. `production_coordination_available()` is always false. It is not connected to FastAPI, storage writers, authentication or recovery.
- `tests/backend/test_cross_store_coordination_offline.py`: synthetic fixtures and real temporary SQLite/local subprocess tests.
- This report. No production source file, VM, snapshot, network or security setting was changed.

## Actual writer inventory

| Store and writers | Existing protection | Shared cross-store protection |
| --- | --- | --- |
| Task, approval, artifact metadata SQLite: `TaskStore`, approval and task managers | SQLite transactions, conditional approval consume, process-local mutex and prior insert-only approval protection. | **No**. SQLite coordinates only writers to that live DB; another path/database or uncooperative file writer is outside its authority. |
| Conversation JSON plus conversation registry SQLite: `ConversationMemory`, `ConversationRegistry` | Memory lifecycle uses a cooperative file lock and process-local lock; registry uses SQLite immediate transactions. | **No**. Not all registry/direct writers share one lock or file identity boundary. |
| Provenance registry: `QuarantinedSources` | SQLite immediate transactions; pending/revoked records and restart-unverified state. | **No**. No independent rollback witness. |
| Chroma collections: `VectorStore` initialization, add/delete/backfill | Backend classification and document ingestion fail-closed gate; underlying internal writes and startup remain separate. | **No**. No Chroma initialization was performed in this batch. |
| Upload files: `_persist_new_upload` | Exclusive creation and content checks in a currently denied ingestion path. | **No**. Parent traversal, Windows reparse and replacement protection not established. |
| Artifact bytes: `backend/tools/artifact_storage.py` | Opaque generated names and exclusive file creation. | **No**. It does not establish protected ancestors or cross-store atomicity. |
| Audit and recovery writers: task events, audit logger, prototype synthetic journal | SQLite or fixture-specific mechanisms. | **No**. Synthetic recovery is not production freshness or isolation. |

## Protocol scope and limits

The fixture uses one global lock acquired before any synthetic observation and released by closing the SQLite connection. Timeout is explicit and capped at one second; SQLite busy errors propagate. Reentrant acquisition is rejected, so the fixture has no lower-ranked locks or path that waits while holding one. OS process termination releases that database connection's lock; this says nothing about interrupted changes to other stores. There is no stale-lock removal. Missing databases fail rather than being intentionally created, but its `is_file`/URI check can race a replacement and is therefore unsuitable for production. Malicious or uncoordinated writers can bypass it, and the code has no independently protected store locator or ACL admission.

`os.fstat()` and `os.lstat()` obtain OS-backed metadata, but this is **an observation, not a Windows secure filesystem identity layer**. The opened handle is closed before the result returns; there is no handle-relative directory traversal, verified protected root, reparse-free ancestry, authenticated volume binding, safe namespace opening, protection from preexisting handles, or guarantee that later use reads the observed object. Inodes/file IDs can be reused and do not prove freshness after restart. Parent junction traversal and TOCTOU remain unresolved. A mocked final-component reparse flag is not a native reparse test. No source is authorized or recovery promoted by these observations. The production requirement is blocked until a reviewed Windows handle-based broker, independently provisioned ACLs and native adversarial verification exist.

## Offline tests

External pytest plugin autoload disabled; direct stdlib runners with Python `-I -B`. Tests create only generated temporary synthetic files/databases and local child Python processes. No real Chroma, Ollama, FastAPI, Docker, SCM or VM was started. No OS-level network monitoring was asserted.

| Command | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| `python -I -B tests/backend/test_cross_store_coordination_offline.py` | 5 | 0 | 0 |
| `python -I -B tests/backend/test_storage_integrity_offline.py` | 8 | 0 | 0 |

The new tests exercise real two-process SQLite contention and a killed lock holder, bounded busy failure, a real ordinary-file replacement, missing/changed identity, nested lock refusal and simulated symlink/reparse results. They do not test native reparse/junction traversal, malicious independent writers, Windows security descriptors or power loss.

## Remaining requirements

An independently provisioned restricted broker must own every relevant root and writer, verify existing handle identities and ancestry without path reopening, deny uncoordinated access via ACLs, fence operations and revocation across stores, and use an independent anti-rollback witness. Native tests in a separately approved isolated VM must cover real junctions/reparse points, hard links, parent replacement, existing handles, cross-account access and interruption/rollback. Chroma and egress monitoring remain separately blocked. Only after those guarantees and full integration exist can a shared production coordinator be claimed; no tested fixture or SQLite lock alone provides them.
