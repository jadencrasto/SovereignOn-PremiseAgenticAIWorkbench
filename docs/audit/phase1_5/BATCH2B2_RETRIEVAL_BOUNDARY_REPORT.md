# Batch 2B.2 retrieval boundary — PARTIAL / ACTIVE WITHHELD

Date: 2026-11-08. Branch `phase1-5-recovery-security`; existing uncommitted work preserved. Batch 1 remains PARTIAL; Phase 1.5.4 and Phase 2 remain NO-GO.

## Decision and deliberate compatibility impact

`backend/security/provenance_foundation.py::document_content_allowed` is a shared **deny-all** decision. It cannot establish trusted source ID, revision, current owner/clearance, conversation binding, revocation freshness or ACTIVE state. No input, including a client ID or vector metadata, can grant access. It is deliberately not a complete positive authorization policy; no authoritative source is activated and no legacy record is migrated.

Document RAG search returns no results, indexed-document listing is empty, document detail returns not found, source-dependent historical assistant responses are withheld, and live knowledge-graph service/tool output is unavailable. Uploads and persisted data are not removed. This is an intentional security restriction and means ordinary RAG functionality is unavailable. The static knowledge-graph fallback can still show its existing demonstration data; the dynamic document attachment is disabled. File tools are **not covered** by this RAG boundary.

## Consumer inventory and boundary

| Consumer | Control / limitation |
| --- | --- |
| `Retriever.retrieve` | Checks deny-all before embedding or vector query, including direct invocations and missing context. |
| `DocumentService.retrieve`, `list_documents`, `get_document_details` | Checks deny-all before delegating or reading vector metadata. List/detail API routes independently deny even if a service is substituted. |
| `document_search` | Checks deny-all before calling even an injected retriever. |
| `AgentEngine._retrieve_context_inner` | Checks deny-all before invoking a document service. `document_search` direct answer, observation and prior-step result formatting also withhold supplied content. Existing model-generated free text and other input paths are not a provenance proof. |
| Conversation source checks | `may_read_assistant` denies any source-dependent historical message before trusting a service; deterministic source-independent history continues under existing policy. |
| Knowledge-graph API/tool | Live graph service is withheld before calls because it can mix document snippets with other nodes; graph tool returns unavailable. Static fallback is still possible, with dynamic indexed-document enumeration disabled. Direct internal graph-service calls are outside these guards. |
| `VectorStore.query` / `get_document_chunks` | Remain internal low-level interfaces without a gate. Ingestion/deletion need raw access; direct non-cooperating internal calls can bypass the boundary and must not be exposed to clients or model consumers. `delete_document` uses internal chunk metadata for cleanup. |
| Workspace `file_read` / `file_list` | Separate tools operating on uploads/workspace. They can expose the same underlying documents and filenames and are **not gated here**. Their ownership, classification and source binding remain a critical open disclosure path. No claim is made that all document-derived content in the application is blocked. |
| Other model, tool, graph and extension paths | Not exhaustively proved. In-flight already-sent bytes cannot be recalled. Direct stores, non-cooperating writers, rollback and current-clearance freshness remain unresolved. |

This shared decision currently denies without consulting a source registry, because the existing registry has no trustworthy ACTIVE state. It does not implement current user, owner, revision, clearance or freshness checks for any future grant. A future positive policy must implement and test all of those checks rather than changing the return value.

## Regression evidence

The Batch 2B.1 synthetic tests originally demonstrated that pending/unregistered content reached both the retriever and `document_search`. After implementing the boundary, those same tests were changed to assert denial. New tests initially showed seven failures covering direct retriever, injected service/tool, listing and historical source paths (one initial tool fixture failed early on an incomplete synthetic chunk; it is now prevented from reaching that fixture). The final tests cover actual pending and revoked registry rows, missing/legacy/unregistered labels, forged or injected service outputs, route denial, tool-output formatting, agent retrieval, graph service denial and conversation source denial. They do not establish a positive authorized retrieval or full application-wide containment.

Windows/Python targeted run, synthetic fixtures and temporary databases, no live models or Chroma:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_batch2b2_retrieval_boundary.py','tests/backend/test_batch2b1_retrieval_preconditions.py','tests/backend/test_batch2b_document_lifecycle.py','tests/backend/test_batch2a_provenance_foundation.py','tests/backend/test_phase153g_registry.py','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_approval.py','tests/backend/test_docx_and_execution.py::TestDocxCreate','tests/backend/test_phase153h7_presentation.py','-q','-p','no:cacheprovider','--tb=short']))"
# 67 passed, 1 skipped in 6.64s
```

The skip is the Windows symlink privilege limitation. Selected tests cover artifact collision, task L3-to-L1 denial, approvals, artifact access, conversation revocation, calculator and DOCX. The Python socket guard is not OS-level isolation. The previously rejected full suite was not retried; browser and real-model tests were not run. Existing RAG compatibility suites expecting retrieval may now fail due to the deliberate restriction and were not represented as passing.

Changed: `backend/security/provenance_foundation.py`, `backend/rag/retriever.py`, `backend/rag/service.py`, `backend/tools/document_search.py`, `backend/security/conversation_access.py`, `backend/agent/engine.py`, `backend/api/documents.py`, `backend/api/knowledge_graph.py`, `backend/tools/knowledge_graph.py`, `tests/backend/test_batch2b1_retrieval_preconditions.py`, `tests/backend/test_batch2b2_retrieval_boundary.py`, this report. No stage, commit, push, merge or deployment.

## Batch 2B.3 readiness

**NO-GO for ACTIVE or authorized retrieval.** The tested RAG consumers now fail closed, but workspace tools and raw internal stores can still expose document-derived data. Build a complete shared access model including those paths, authenticated current user and clearance, source owner and revision, trustworthy lifecycle and file/index integrity, concurrency and revocation before enabling ingestion activation. Task approval/resume, artifact viewing/download and verifier dispatch remain restricted.
