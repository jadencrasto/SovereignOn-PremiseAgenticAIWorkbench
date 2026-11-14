# Batch 2B.3 document lifecycle — BLOCKED / ACTIVE WITHHELD

Date: 2026-11-08. Branch `phase1-5-recovery-security`; all prior uncommitted work preserved. No application source, real document, database, index or runtime configuration was changed in this pass. Batch 1 remains PARTIAL; Phase 1.5.4 and Phase 2 remain NO-GO.

## Actual upload path and newly reproduced defect

`POST /api/documents` derives clearance from its server user and calls `DocumentService.ingest_document`. The service sanitizes the name, `DocumentParser.parse` computes legacy `doc_` identity from filename and only the first 1,024 bytes, writes `upload_dir / (document_id + filename)` using `write_bytes`, chunks/embeds and calls vector upsert. It does not call `QuarantinedSources`, bind a server owner to the document, use exclusive creation or verify a persisted file/index manifest. File, index and SQLite are separate stores. The existing `document_content_allowed()` deny-all remains enabled in the tested RAG, document API, workspace and vector read paths; raw maintenance/internal paths and independent writers remain outside that protection.

Two synthetic tests in `tests/backend/test_batch2b3_ingestion_preconditions.py` execute the actual upload route/service using temporary files, an in-memory vector collection and mocked embeddings. The first confirms that the upload creates legacy indexed data but **no provenance row**, while tested document retrieval/detail remain denied. The second confirms that two different synthetic files with identical names and first 1,024 bytes receive the same legacy ID and the second overwrites the first synthetic file. This is a confirmed write-integrity defect; no real document was read or overwritten. Existing uploads are **not** automatically PENDING or safe merely because RAG is denied.

## Lifecycle status and blocker

**PENDING → VERIFIED → ACTIVE is not implemented or integrated. ACTIVE remains withheld.** The current `quarantined_source_registry` is pending/revoked only and not initialized by application ingestion; its SHA-256 is an assertion about input bytes, not a verified disk/index binding. No schema, activation, deletion, index manifest or recovery migration was added. We did not attach a misleading pending record to the legacy path, nor promote records using vector metadata.

Safe integration needs server-issued IDs and immutable server-context ownership/classification, exclusive path creation with Windows handle/reparse validation, persisted-byte digest verification, revision-bound chunks and exact trusted manifest verification, SQLite compare-and-swap against revocation, and fault/restart recovery across independent file/vector/SQLite operations. Existing Windows symlink/reparse tests remain skipped; non-cooperating writers, rollback and file tampering are not resolved. Chroma can fall back to an in-memory store and upsert is not a complete-index durability proof. Without implementing and validating these pieces together, a positive ACTIVE transition would claim guarantees not demonstrated here. Existing upload remains operational and retains the confirmed collision risk; an operator must not interpret this report as production safety.

## Execution compatibility test

The previously failing `TestExitCodeClassification.test_control_c_treated_as_failure_with_description` expected a mocked code worker to run. That expectation conflicts with the deliberate execution deny policy. It now verifies `PermissionError`, asserts the mocked worker was not called, and retains the separate `classify_exit_code` assertion. No code was executed, Docker was not probed, and no test was removed, skipped or made permissive.

## Targeted verification

Windows/Python, temporary synthetic storage, mocked embeddings, no live model/vector service. First run:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_batch2b3_ingestion_preconditions.py','tests/backend/test_code_isolation_step8.py::TestExitCodeClassification','tests/backend/test_batch2b2c_execution_disablement.py','tests/backend/test_batch2b2a_content_boundary.py','tests/backend/test_batch2b2_retrieval_boundary.py','tests/backend/test_batch2b_document_lifecycle.py','tests/backend/test_batch2a_provenance_foundation.py','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_approval.py','tests/backend/test_docx_and_execution.py::TestDocxCreate','tests/backend/test_phase153h7_presentation.py','-q','-p','no:cacheprovider','--tb=short']))"
# 82 passed, 2 skipped in 5.19s
```

That run preceded the addition of the collision test. The same wrapper/flags were then used with only `tests/backend/test_batch2b3_ingestion_preconditions.py`: **2 passed in 0.92s**, including the collision reproduction. The two skipped tests in the broader run require Windows symlink privilege. These are not a combined 83-test result. No authorized positive lifecycle, activation/revocation race, incomplete-index denial, restart recovery or manifest test was performed because that lifecycle does not exist. Python socket guards are not OS-level network isolation. The previously rejected full suite was not retried.

Files changed: `tests/backend/test_code_isolation_step8.py`, `tests/backend/test_batch2b3_ingestion_preconditions.py`, and this report. No stage, commit, push, merge or deploy.

## Batch 2C readiness

**NO-GO.** First replace the legacy ingestion write/index sequence with a reviewed, isolated server-ID and exclusive-storage protocol, and verify all failure and recovery cases before granting ACTIVE. Address the reproduced ID/file collision and preserve existing documents without automatic migration. Keep retrieval denied and do not enable task approval/resume, artifact access or verifier dispatch. The execution denial remains in force; OS isolation and outbound telemetry remain unverified.
