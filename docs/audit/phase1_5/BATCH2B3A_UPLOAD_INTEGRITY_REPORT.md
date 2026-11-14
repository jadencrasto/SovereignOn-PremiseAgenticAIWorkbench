# Batch 2B.3A — upload write integrity

Date: 2026-11-08. Branch `phase1-5-recovery-security`; existing uncommitted changes preserved. ACTIVE and authorized document retrieval remain disabled. This is a write-integrity repair, **not** an ownership or lifecycle authorization implementation. Phase 1.5.4 and Phase 2 remain NO-GO.

## Root cause and change

The existing parser generated a document ID from the display filename and only the first 1,024 bytes. `DocumentService.ingest_document` used that ID and filename with `Path.write_bytes`, which overwrote the same path; chunk IDs and Chroma upsert also shared that identity. The original synthetic collision reproduction passed before the change and the updated preservation assertion then failed before the fix.

For each new ingestion, the service now issues `doc_v1_` plus 32 UUID4 hexadecimal characters and exclusively creates `<document_id>.bin` under the configured upload root using `O_CREAT|O_EXCL` and `O_NOFOLLOW` where available. It retries collisions at most eight times and checks the index for an already-existing identity before file creation. The display filename remains in document metadata. A short write, creation failure, reopened byte-count mismatch or SHA-256 mismatch rejects ingestion; writes flush and fsync before re-read. The helper does not delete failures. It rejects a symlink or Windows reparse-point flag on the storage root, but ancestor replacement, handle-level race resistance and Windows reparse behavior have **not** been established; the storage directory and its ancestors must be trusted against non-cooperating writers. These checks are not an OS sandbox.

`DocumentParser.parse` retains its legacy deterministic behavior for standalone callers; the service replaces the parsed ID with the new server ID before chunking and graph extraction. Chunks consequently use the new document ID plus chunk index, with `source_revision=1` in metadata. The graph document-node ID uses the complete new ID rather than its first eight characters; old graph IDs retain their prior format. The deletion path recognizes the new ID format and its opaque `.bin` name while retaining legacy filename-based cleanup for legacy records. No existing file or index entry was migrated or renamed. New IDs and stored digests do not establish owner, classification or access eligibility.

Index lookup and file creation are **not** one atomic transaction. An independent writer, restored database, index corruption or malicious filesystem actor remains outside the tested collision guarantee. On embedding/index failure, a newly created file remains as an orphan; a partially successful index upsert can also remain. No automatic cleanup, idempotency guarantee, index manifest verification or ACTIVE state was added. Operators need a future owner-aware reconciliation process and must not blindly delete unknown files. Current in-memory vector fallback is not a durable-index guarantee.

## Regression evidence

The retained same-name/same-prefix/different-tail test now asserts distinct IDs and exact preserved bytes. New synthetic tests cover identical uploads, concurrent exclusive creation, forced physical-ID collision without overwrite, retry after an index ID collision, exclusive-create failure, partial write, digest mismatch, indexing failure with preserved legacy data, source revision and chunk IDs, and deletion of one new upload without altering another or a legacy file. Tests use temporary files and a fake in-memory vector collection; graph-node behavior is code-inspected, not live-graph verified.

Exact Windows/Python targeted command:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_batch2b3a_upload_integrity.py','tests/backend/test_batch2b3_ingestion_preconditions.py','tests/backend/test_code_isolation_step8.py::TestExitCodeClassification','tests/backend/test_batch2b2c_execution_disablement.py','tests/backend/test_batch2b2a_content_boundary.py','tests/backend/test_batch2b2_retrieval_boundary.py','tests/backend/test_batch2b_document_lifecycle.py','tests/backend/test_batch2a_provenance_foundation.py','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_approval.py','tests/backend/test_docx_and_execution.py::TestDocxCreate','tests/backend/test_phase153h7_presentation.py','-q','-p','no:cacheprovider','--tb=short']))"
# 92 passed, 2 skipped in 5.29s
```

The two skips concern Windows symlink privileges. Selected prior guards include RAG/workspace denial, execution denial, conversation revocation, task L3-to-L1, artifact collision/authorization, approval, calculator and DOCX. Legacy RAG compatibility tests and full integration suites were not run; Chroma, browser and real models were not used. Python socket wrapping is not OS-level isolation. The previously rejected full suite was not retried and Docker was not probed.

Changed: `backend/rag/service.py`, `backend/graph/extractor.py`, `tests/backend/test_batch2b3_ingestion_preconditions.py`, `tests/backend/test_batch2b3a_upload_integrity.py` and this report. No staging, commit, push, merge or deployment.

## Batch 2B.3B readiness

**NO-GO for activation or authorized retrieval.** A trusted owner/classification registry, revocation-before-cleanup protocol, source and exact-index manifest verification, crash recovery, OS-level storage isolation and concurrency/rollback assumptions still need implementation and verification. The deny-all document boundary, code-execution denial, task approval/resume and artifact-access restrictions remain unchanged.
