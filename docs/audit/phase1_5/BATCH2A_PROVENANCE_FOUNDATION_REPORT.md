# Batch 2A provenance foundation — PARTIAL / NO-GO

Date: 2026-11-08. Branch `phase1-5-recovery-security`, baseline HEAD `beeca449e127e01cb53576ea02c05522855766bd`; substantial pre-existing uncommitted work preserved. Batch 1 remains PARTIAL; Phase 1.5.4 and Phase 2 remain NO-GO. This pass did not start Batch 2B.

## Trust-boundary findings

| Stage | Existing authority and observed limitation |
| --- | --- |
| Authenticated user | `backend/auth/dependencies.py` sets `effective_owner` and `effective_clearance` from a server-resolved user. Context is request-scoped, not a continuous reload after a role or clearance change. |
| Conversation | `memory.stream_identity()` checks existing JSON ownership and observed revocation under cooperative locks. Its generation can originate from JSON; `conversation_registry.classify()` intentionally returns unknown even for an active row, because rollback and independent writers undermine freshness. `has_active_binding` explicitly is not authorization. |
| Ingested document | `rag/service.py` writes an uploaded file, embeds and indexes chunks independently; it records clearance but not authoritative owner. The `DocumentParser` identifier hashes filename and only the first 1024 content bytes (`rag/ingest.py`), so it must not be treated as globally unique proof of source ownership. No transactional binding exists among upload file, vector chunks, source classification and owner. |
| Retrieval | `rag/retriever.py` filters using vector metadata clearance and yields source/chunk fields; result dictionaries in `tools/document_search.py` are not an authoritative source record. Forged, stale, deleted or reclassified data cannot be revalidated against a trustworthy owner registry. |
| Agent/model/client | `engine._retrieve_context` and `document_search` feed several plain, streaming, tool, planner, multimodal and synthesis paths; source metadata and text may enter prompts and responses. Existing conversation stream guards recheck identity, but cannot establish continuous current clearance or cross-store atomicity. |
| Task/artifact | `task_security_provenance` records unverified/unknown dependencies. `task_content_revalidation_available()` returns false; approval/resume and artifact access remain denied. |

These are code-inspection findings; no real user documents, live models or vector services were used. The current application does **not** enforce the new pending registry or policy and existing retrieval behavior was not changed. Unknown and legacy documents therefore are not newly quarantined in existing retrieval; do not mistake this foundation for a completed fail-closed vertical slice.

## Implemented foundation and schema

`backend/security/provenance_foundation.py` provides an unintegrated `SecurityPolicy.may_disclose` interface that denies conversation, document, tool-result, task-result, artifact and unknown requests regardless of caller-supplied claims. It is a conservative placeholder, not a replacement for production authorization and not invoked by current application paths.

`QuarantinedSources` creates an additive `quarantined_source_registry` table **only when explicitly instantiated**. It stores a generated UUID source ID, untrusted document reference, owner from `effective_owner`, schema version 1, creation/revocation timestamps and `pending`/`revoked` state. It stores no document content; only the bound owner can read or revoke a record. Writes use the existing bounded SQLite `BEGIN IMMEDIATE` helper. No transition to trusted/active exists. A row is not evidence that the owner owns the underlying document, that content matches, or that classification is current. Records are not automatically created on application ingestion and are not connected to existing storage, tasks or policy grants. The table can be removed only through a separately reviewed migration; this pass does not modify any real application database.

The requested authoritative provenance schema and authorized vertical slice are **BLOCKED / NOT IMPLEMENTED**. A safe implementation must first define a server-issued content identity and owner/classification lifecycle; transaction or quarantine protocol spanning indexing, deletion and reclassification; a reliable authority for current user/session and conversation generation; and enforceable revalidation across model and client release. SQLite alone cannot make JSON, vector and filesystem writes atomic, and a process-local lock cannot prevent independent writers or rollback. Promoting a pending row based solely on a matching vector ID, JSON field, caller dictionary, or empty dependency list would grant unsupported trust. No existing records were migrated or assigned an owner.

## Targeted tests

Regression-first: before implementation, the new test file failed collection because the foundation module did not exist. After implementation the following synthetic Windows/Python run used temporary pytest databases, disabled plugin autoload and bytecode, and a Python socket wrapper permitting literal loopback only. The wrapper is not OS-level isolation.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_batch2a_provenance_foundation.py','tests/backend/test_phase153h7_presentation.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_approval.py','tests/backend/test_docx_and_execution.py::TestDocxCreate','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153g_registry.py','-q','-p','no:cacheprovider','--tb=short']))"
# 48 passed, 1 skipped in 6.02s
```

The skipped Windows directory-symlink test requires privilege. New tests cover deny-default decisions, server-context-derived pending owner, cross-owner read/revocation denial, revocation and restart. Existing selected tests include artifact collision, task downgrade, approval restrictions, DOCX and conversation revocation. **Authorized document retrieval, cross-user document isolation, stale-generation and clearance-downgrade rejection at an integrated retrieval boundary, SSE containment, source forgery, legacy quarantine and retrieval concurrency were not verified** because the required vertical slice does not exist. Existing retrieval must not be described as safe on this evidence.

## Compatibility and next scope

Existing runtime behavior and records remain unchanged; the new component is not initialized by the application. Batch 1's unrestricted known-tool/model output, unverified source provenance and incomplete public error contract remain dependencies. Before Batch 2B grants any access, design and test a unified authority with server-derived owner, revisioned source IDs and classification, content/chunk integrity binding, durable deletion/reclassification revocation, current identity/clearance revalidation, and defined cross-store crash/recovery and rollback assumptions. Start with a synthetic isolated vertical slice and verify one authorized and multiple denied flows before wiring production. Keep task approval/resume and artifact access disabled.

Files introduced: `backend/security/provenance_foundation.py`, `tests/backend/test_batch2a_provenance_foundation.py`, this report. No staging, commit, push, merge or deployment. Previously rejected full suite was not retried; no OS-level network isolation is claimed.
