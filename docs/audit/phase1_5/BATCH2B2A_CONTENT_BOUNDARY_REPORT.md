# Batch 2B.2A content boundary — PARTIAL / NO-GO

Date: 2026-11-08. Branch `phase1-5-recovery-security`; existing uncommitted work preserved. Batch 1 remains PARTIAL; Phase 1.5.4 and Phase 2 remain NO-GO. No source activation or authorized RAG retrieval was enabled.

## Reproduced bypasses and controls

Before changes, synthetic tests demonstrated that `create_file_read` returned a protected upload's text, `create_file_list` could enumerate its filename, direct `VectorStore.query` returned content and metadata, and `AgentEngine` file-result formatting replayed content. A traversal test initially received the existing path-validation `ValueError` instead of the new expected denial; this was a test expectation mismatch, not a demonstrated traversal bypass. Windows symlink creation was not permitted in this environment.

The existing shared `document_content_allowed()` decision remains unconditional denial. Both workspace tool factories now check it **before** path resolution or filesystem enumeration: file reads raise `PermissionError` and listings return empty. `ToolRegistry.execute` independently denies both registered tool names. Engine direct-answer, observation and prior-step result formatters withhold `file_read` and `file_list` output, including a synthetic injected result. These controls do not delete files and do not claim any source is authorized.

`VectorStore.query`, `get_document_chunks`, `list_documents` and `document_chunk_count` now check the same decision and return empty results while it denies. `DocumentService.delete_document` uses explicitly named internal maintenance methods for its preexisting cleanup; a synthetic test confirms index removal works. The maintenance methods and `_collection` remain directly accessible to Python code and are **not a security boundary**. They must remain restricted to trusted maintenance code; a malicious or non-cooperating in-process caller can bypass the public methods. Aggregate `count` also remains available internally. Existing RAG/document/service/graph guards from Batch 2B.2 remain in effect.

## Consumer inventory

| Surface | Current disposition / remaining issue |
| --- | --- |
| Workspace `file_read` / `file_list` | Denied before touching upload paths, including direct factory calls and registered dispatch. Entire configured upload root is restricted; no trusted distinction for non-document files exists. |
| Agent file-tool summaries and prior-step formatting | Tested direct answers, observations and prior-step result projections withhold `file_read` content. Other raw internal state and arbitrary free-form model/user text are not globally filtered. |
| Public vector `query`, chunks, list, per-document count | Denied via shared decision. The earlier retriever, service and document API guards remain. |
| Internal `_collection`, maintenance methods, index update/delete/reconciliation | Raw access remains for operational maintenance. Python naming does not isolate it; no supported client route should forward it to a model or client. Independent internal code and plugins have not been exhaustively audited. |
| Knowledge graph | Batch 2B.2 blocks the live API/tool service because it can contain source snippets. Internal graph-service calls remain outside the guard. |
| Source-derived conversation history | Existing source check denies unverified sources; deterministic independent history retains prior behavior. |
| Code execution, alternative filesystem access, background jobs, other extensions | Not fully proven contained. Code execution needs separate filesystem isolation and authorization; restricting `file_read` is not a sandbox. Current approved execution and artifact policies remain as before. |

The invariant is therefore **not proven for every possible application path**. Existing source lifecycle remains pending/revoked only, and no decision validates live source ownership, classification or revision. Count/metadata leakage from other internal APIs, races, non-cooperating writers, rollback, filesystem tampering, and Windows reparse behavior remain open. Do not enable ACTIVE, task approval/resume, artifact preview/download or registry verifier dispatch.

## Regression verification

Windows/Python, synthetic files, in-memory fake vector collection and temporary pytest data. Exact targeted command:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_batch2b2a_content_boundary.py','tests/backend/test_batch2b2_retrieval_boundary.py','tests/backend/test_batch2b1_retrieval_preconditions.py','tests/backend/test_batch2b_document_lifecycle.py','tests/backend/test_batch2a_provenance_foundation.py','tests/backend/test_phase153g_registry.py','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_approval.py','tests/backend/test_docx_and_execution.py::TestDocxCreate','tests/backend/test_phase153h7_presentation.py','-q','-p','no:cacheprovider','--tb=short']))"
# 72 passed, 2 skipped in 6.95s
```

Both skips concern Windows symlink creation privileges. Selected guards cover pending/unregistered RAG, synthetic workspace/vector denial and cleanup, calculator, DOCX, artifact collision, task downgrade, conversation revocation and approval restrictions. No isolated non-document operation in the uploads root is claimed available. Real browser, live model, actual OS egress isolation and the previously rejected full suite were not run; Python socket guards do not prove OS-level isolation. Other legacy file/RAG compatibility suites were not run and may fail because of the intentional restriction.

Files changed: `backend/tools/file_read.py`, `backend/tools/file_list.py`, `backend/tools/registry.py`, `backend/rag/store.py`, `backend/rag/service.py`, `backend/agent/engine.py`, `tests/backend/test_batch2b2a_content_boundary.py`, and this report. No staging, commit, push, merge or deployment.

**Batch 2B.3 NO-GO for activation or authorized retrieval.** The next review must address all supported source-derived output paths, operational isolation and a trustworthy current source/owner/revision authority before any deny-all condition is replaced.
