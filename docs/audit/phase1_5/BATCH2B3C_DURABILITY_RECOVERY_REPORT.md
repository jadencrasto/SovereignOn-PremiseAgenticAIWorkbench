# Batch 2B.3C — durability and recovery: PARTIAL / ACTIVE WITHHELD

Date: 2026-11-08. Branch `phase1-5-recovery-security`; all earlier uncommitted changes preserved. ACTIVE, authorized document retrieval and existing task/artifact/execution restrictions remain unchanged. Phase 1.5.4 and Phase 2 remain NO-GO.

## Actual backend behavior and evidence

`VectorStore.__init__` attempts a Chroma `PersistentClient` using the configured directory and collection; it falls back to `_InMemoryFallbackCollection` on most initialization errors. Telemetry-configuration errors are re-raised. A configured path or a non-None client does **not** demonstrate persistence, correct storage binding or close/reopen durability. Existing `readback_verified_durability_unproven` and `readback_verified_nondurable` labels remain non-authorizing observations, not durable verification.

No real Chroma client was started or reopened in this pass. Real persistent-backend testing is **BLOCKED**: the earlier automatic security review rejected a full-suite attempt after an unidentified outbound telemetry request, and no OS-level network isolation has been demonstrated. A fake or socket wrapper cannot establish native/dependency egress denial or durable Chroma behavior. No model was pulled or run.

## Configuration evidence and restart handling

The existing registry gains additive nullable `index_configuration_json` and `reconciliation_status` columns. During pending ingestion, the saved manifest now includes a separate configuration record containing the configured embedding model name, `model_digest: null`, observed embedding dimensions, collection name, source revision, chunk size/overlap, and either `in_memory_nondurable` or `persistent_backend_unverified`; `storage_configuration_verified` is always false. These are recorded assertions/observations, **not authoritative proof** that the selected model is immutable, that the collection/path is correct, or that a persistent backend was actually reopened. Inconsistent or missing embedding dimensions now fail before indexing. Previously stored records remain unchanged and untrusted.

At application startup the registry's `mark_restart_unverified()` sets PENDING records to `restart_unverified` and REVOKED records to `revoked_cleanup_review` in a short SQLite transaction. It preserves the prior manifest and verification fields, never changes lifecycle state, never reads or deletes files, and never grants access. Repeating it is state-idempotent. These labels mean **verification has not happened**, not that a file, index, or cleanup candidate was validated. Startup may run while another application process is working; this metadata marking is not a cross-process reconciliation protocol. Existing `document_content_allowed()` remains deny-all.

The requested restart procedure that validates source bytes, reopens a persistent backend and compares exact manifests is **NOT IMPLEMENTED**. Secure Windows source-handle/reparse behavior, a proven storage/collection binding, a safe Chroma close/reopen mechanism and independent egress control have not been established. No unknown resource is adopted or deleted. No authoritative durable evidence or verified immutable model digest is created. A later implementation must revalidate those resources under a reviewed concurrency/OS trust model rather than interpreting these fields as approval.

## Failure and recovery matrix

| Stage / scenario | Evidence in this pass | Outcome / limitation |
| --- | --- | --- |
| After PENDING, file create/source mismatch, incomplete indexing | Prior 2B.3B synthetic tests re-run; restart marking is metadata-only | Remains PENDING; no automatic file/index inspection or cleanup. Actual process kill not tested. |
| Source or index altered/missing after restart | Not revalidated in this pass | `restart_unverified`; no claim of detection or durability. Prior mismatch tests exercise ingestion-time fake readback only. |
| After index upsert but before evidence write | Not separately injected here | No verified reopen evidence; remain PENDING, no activation. |
| Revoked and repeated restart | Synthetic registry test | Revoked state retained and marked cleanup review; no resource is identified as safely owned or deleted. |
| Invalid embedding dimensions | Synthetic fake embedding test | Runtime error before index write, PENDING retained. No actual model used. |
| Cross-user, downgrade and denial guards | Existing targeted synthetic tests re-run | Existing restrictions tested; not proof of current clearance refresh or cross-process authorization. |

## Targeted verification

Before implementation two new synthetic tests failed due to missing configuration and restart-status support. Exact Windows/Python command, temporary SQLite/files and fake vector backend:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_batch2b3c_reconciliation.py','tests/backend/test_batch2b3b_pending_ingestion.py','tests/backend/test_batch2b3a_upload_integrity.py','tests/backend/test_batch2b3_ingestion_preconditions.py','tests/backend/test_batch2b_document_lifecycle.py','tests/backend/test_batch2a_provenance_foundation.py','tests/backend/test_batch2b2c_execution_disablement.py','tests/backend/test_batch2b2a_content_boundary.py','tests/backend/test_batch2b2_retrieval_boundary.py','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_approval.py','tests/backend/test_docx_and_execution.py::TestDocxCreate','tests/backend/test_phase153h7_presentation.py','-q','-p','no:cacheprovider','--tb=short']))"
# 101 passed, 2 skipped in 5.83s
```

Both skips require Windows symlink privileges. This is not a real persistent-index reopen, actual crash, browser, live-model or OS-isolation test. The rejected full suite was not retried. Changed files: `backend/security/provenance_foundation.py`, `backend/rag/service.py`, `backend/main.py`, `tests/backend/test_batch2b3c_reconciliation.py`, and this report. No stage, commit, push, merge or deployment.

**Conditional ACTIVE remains NO-GO.** Before any grant, independently verify persistent storage and model digest, exact manifest after real restart, safe source opens, concurrent revocation and recovery, rollback policy, current user/clearance and all consumer boundaries. Do not restore task approval/resume, artifact reads, arbitrary code or RAG on this evidence.
