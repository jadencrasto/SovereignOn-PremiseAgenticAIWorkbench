# Batch 2B.1 ingestion integration — BLOCKED / ACTIVE WITHHELD

Date: 2026-11-08. Branch `phase1-5-recovery-security`; existing uncommitted changes preserved. The requested precondition requires stopping activation when a failed upload could become retrievable through an unguarded path. That precondition fails. No production ingestion, retrieval, registry, authorization or data was changed. Phase 1.5.4 and Phase 2 remain NO-GO.

## Actual call graph and trust

1. `POST /api/documents` in `backend/api/documents.py` requires the write-tools permission, reads a bounded upload, derives clearance with `resolve_user_clearance(current_user)`, then calls `DocumentService.ingest_document(filename, content, clearance=...)`. `get_current_user` binds `effective_owner` and `effective_clearance` from the server-resolved user. The ingestion service does not currently create or consult an authoritative owner record.
2. `backend/rag/service.py` sanitizes and parses the document, writes bytes under `upload_dir`, chunks, obtains embeddings and calls `VectorStore.add_chunks`. The parser's legacy ID is based on filename and a partial content hash; `add_chunks` uses Chroma upsert. These are separate file, model and index operations with no SQLite lifecycle transaction or verified completeness record. Graph ingestion follows separately.
3. `DocumentService.retrieve` calls `Retriever.retrieve` (`backend/rag/retriever.py`), which trusts vector metadata for clearance filtering and does not consult the quarantine registry. `AgentEngine._retrieve_context_inner` calls the service; `create_document_search` calls the same retriever directly. Both paths can feed agent/model output.
4. Document list/detail routes call `DocumentService.list_documents` and `get_document_details`, which read vector metadata and chunks separately from any authoritative source registry. Conversation source checks use document details. Knowledge-graph code can list indexed documents. `file_read` and `file_list` are separate workspace consumers and require their own authorization review. Search over direct store query calls identified these paths; this is not a proof that every possible extension or direct internal call is covered.

The current `QuarantinedSources` is intentionally not initialized in app startup or invoked by ingestion. Its owner, clearance and digest fields are pending assertions and `retrieval_eligible` always returns false. The conversation registry intentionally does not establish freshness against rollback or independent writers. Existing application retrieval behavior remains unchanged and is **not** made fail-closed by the quarantine registry.

## Reproduced retrieval bypass

`tests/backend/test_batch2b1_retrieval_preconditions.py` uses an isolated SQLite registry, fake embedder and fake vector store. In both a registered `pending` case and an unregistered case, `retrieval_eligible` returns false, yet the actual `Retriever.retrieve` and `create_document_search` return the synthetic indexed text. This demonstrates the missing central eligibility boundary. It does not require or modify Chroma, an application database, real documents or a live model.

The tests intentionally document existing behavior; their passing result is **not** a security pass or a claim that pending content is blocked. A failure after vector upsert is therefore capable of leaving index entries that these current paths do not gate on SQLite state. We did not deliberately write to real indexes or simulate real process termination.

## Lifecycle and recovery decision

**ACTIVE is withheld.** No route was wired to reserve pending rows, and no server-controlled source storage, re-open/hash check, revision-bound Chroma indexing, index-completeness verification, active transition, or revocation-before-cleanup protocol was implemented. Connecting only reservation to the existing write/upsert path would not prevent unverified content from reaching the model. A partial pseudo-activation would contradict the requested precondition.

Required before activation: a source authority with authenticated current identity/clearance and stable revision; server-controlled, exclusively created storage with verified bytes; complete index manifest verified against the source; a common authorization gate on *all* retrieval, document detail and other applicable consumers; revocation before cleanup; and crash-safe recovery and concurrency rules. SQLite, Chroma and filesystem writes remain separate transactions. Independent writers, database rollback, file tampering and Windows reparse behavior need a stated operational trust boundary and targeted validation. Legacy data must not be silently assigned ownership or promoted.

## Tests and limitations

Windows/Python, synthetic temporary storage and fake services. Exact targeted command:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_batch2b1_retrieval_preconditions.py','tests/backend/test_batch2b_document_lifecycle.py','tests/backend/test_batch2a_provenance_foundation.py','tests/backend/test_phase153g_registry.py','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_approval.py','-q','-p','no:cacheprovider','--tb=short']))"
# 46 passed, 1 skipped in 5.91s
```

The skip is the Windows symlink privilege limitation. Selected tests include collision, L3-to-L1 task isolation, conversation revocation, approval and artifact restrictions; the two new cases **demonstrate a bypass**. Successful verified ingestion, hash mismatch, incomplete-index denial, concurrent activation/revocation, source-revision enforcement and cleanup recovery are NOT RUN/BLOCKED because no activation protocol or retrieval gate exists. Python socket guards do not establish OS-level isolation. The previously rejected full suite was not retried.

Files added: `tests/backend/test_batch2b1_retrieval_preconditions.py` and this report. No staging, commit, push, merge or deployment. Batch 2C readiness: **NO-GO for enabling ACTIVE or retrieval**; first implement and verify a central fail-closed eligibility gate and the source lifecycle, including legacy handling, without weakening existing restrictions.
