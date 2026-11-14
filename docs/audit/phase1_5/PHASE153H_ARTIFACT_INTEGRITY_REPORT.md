# Phase 1.5.3H artifact integrity report

Date: 2026-11-07. Branch `phase1-5-recovery-security`; HEAD `beeca449e127e01cb53576ea02c05522855766bd`. Pre-existing uncommitted work preserved. **PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** Artifact read, preview, download and verifier restrictions remain in force. No staging, commit, push, merge or deployment occurred.

## Root cause and write paths

`TaskStore.save_artifact` previously used `ON CONFLICT(filename) DO UPDATE`, including `user_id` and `path`, so another registration could replace ownership metadata. `file_write` accepted `overwrite=True` and used atomic replace; DOCX and XLSX generated predictable temporary and destination names followed by `os.replace`. XLSX did not reject existing destination names. The tool registry registered metadata only after file creation and swallowed registration errors as warnings. A check before a write could race another writer. Separate code execution can also write files; this pass does not establish its sandbox or constrain all files a program may create.

Before changes, synthetic tests `test_existing_filename_cannot_transfer_owner` and `test_overwrite_flag_cannot_replace_existing_file` both **failed**: metadata transferred and pre-existing content was overwritten. These were isolated temporary fixtures, not real user files. The test did not reproduce all possible DOCX/XLSX race and registration-failure scenarios before the fix; their old code paths were inspected as above.

## Implementation

- `backend/tools/artifact_storage.py` reserves a random UUID-derived physical filename using `O_CREAT|O_EXCL`, optional `O_NOFOLLOW`, and restrictive requested creation mode. It rejects a symlink storage root and restricts the suffix. It assumes the configured root and its ancestors are trusted against non-cooperating OS users; Windows junctions, ancestor replacement and platform-specific reparse behavior are not fully verified.
- `file_write`, `docx_create`, and `xlsx_report` use that exclusive file rather than overwriting a requested name or predictable temporary name. Tool output includes `artifact_id`, opaque physical `filename`, and separate sanitized `display_name`. `overwrite` no longer replaces an existing artifact. The physical file is written directly and flushed; **partial bytes may exist after interruption**. This is exclusive creation, not atomic complete-file publication.
- Artifact metadata registration uses insert-only semantics: duplicate filenames or duplicate non-null artifact IDs raise a conflict and preserve original ownership and path. A migration adds nullable `artifact_id` and `display_name` columns and a partial unique index; old records stay intact with NULL fields and are not silently assigned ownership or IDs. `ToolRegistry` requires server-context owner matching and a store for these three tools, propagates registration failure as a failed tool result and records ID/display name where successful.
- Registry metadata is still recorded after file creation. A failed or interrupted write or registration can leave a uniquely named orphan; this pass never automatically deletes it. Missing registered files and orphan reconciliation are not implemented. Direct invocation of tool functions bypasses registry ownership recording; arbitrary code execution and other file paths are not brought into this metadata protocol. Artifact access remains fail-closed.

Migration runs when `TaskStore` initializes. Before future deployment, stop writers and create a consistent SQLite backup including WAL state. No real database was migrated in these tests. Existing filename-keyed legacy records remain restricted; no automatic reassignment or data deletion occurs. Registry code still builds paths using the global sandbox settings, which needs review for custom-settings deployments before relying on these metadata for access. The returned filename is now opaque; callers must use `display_name` for presentation and the returned storage reference for downstream processing.

## Verification

Windows/Python 3.13, temporary synthetic files/databases, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`, and a Python socket guard permitting literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153h_artifact_integrity.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py tests/backend/test_approval.py -q -p no:cacheprovider --tb=short
# 25 passed, 1 skipped, 1 xfailed; 3.05 seconds
```

New tests cover duplicate metadata ownership, same-name file creation/content integrity, multiple concurrent exclusive writes, competing metadata registrations, DOCX/XLSX same-name output, traversal input containment, registration and creation failures, an injected write interruption, and metadata restart. The symlink-root test was **skipped** because this Windows environment could not create a directory symlink without local privilege; symlink behavior remains unverified here. Existing L3-to-L1 and approval/resume denial guards passed. The original strict artifact collision guard remains **xfailed**: it expects the second registration to return normally and then checks the original owner; registration now raises a conflict before that assertion. Its assertion was not weakened or represented as passing. An earlier expanded run reported 21 passed, 1 failed, 1 skipped, 1 xfailed because the synthetic tool-registry fixture lacked an authenticated role; the fixture was corrected before the final run.

An unchanged compatibility check, `python -m pytest -p pytest_asyncio.plugin tests/backend/test_docx_and_execution.py::TestDocxCreate -q -p no:cacheprovider --tb=short`, returned **1 passed, 1 failed; 0.86 seconds**. The failing test expects the requested display filename as the physical filename and path, whereas the new storage filename is opaque. Consumers and downstream artifact-verification flows need review and integration updates; tests were not weakened. No live models, external services, full suite or OS-level network isolation were verified. The Python socket guard is not OS isolation; the previously rejected full suite was not retried.

## Remaining security and compatibility blockers

Unique IDs and exclusive file creation substantially reduce the directly reproduced overwrite and metadata-transfer paths, but complete cross-tool authorization, task/conversation ownership binding, lifecycle reconciliation, atomic metadata/file publication, privileged/non-cooperating filesystem modification and symlink/reparse resistance remain unproven. Direct write access and arbitrary code execution are not covered by this protocol. DOCX filename compatibility is confirmed broken, and downstream XLSX, UI and verifier compatibility have not been comprehensively tested. Keep artifact access disabled until these gaps and trusted provenance are resolved.

Files changed in this pass: `backend/tools/artifact_storage.py`, `backend/tools/file_write.py`, `backend/tools/docx_create.py`, `backend/tools/xlsx_report.py`, `backend/tools/registry.py`, `backend/agent/task_store.py`, `tests/backend/test_phase153h_artifact_integrity.py`, and this report.

## Phase 1.5.3H.1 identity compatibility stabilization

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; prior uncommitted work preserved. Artifact ID is the generated UUID, display name is sanitized requested presentation metadata, and the current opaque physical filename derives from the ID plus file suffix. Metadata retains these separately; legacy metadata remains without an automatically assigned ID. `created_path` and `filename` are still returned by the tool for internal downstream compatibility. They are storage references, not authorization, and their presence in ordinary tool results remains a path-disclosure/contract gap. Artifact API list/preview/download and registry-dispatched artifact verifier remain fail-closed.

The existing DOCX creation test asserted the requested display name as the actual file path. It now checks the requested `display_name`, the ID-derived physical filename, returned path relationship, document content, table contents, size and hash. The direct verifier unit test now verifies the returned opaque path rather than guessing the requested filename; this does not enable the guarded verifier tool or prove an ID-authorized verifier flow. The original filename-collision xfail was converted to a normal test with explicit duplicate-rejection, unchanged owner, unchanged recorded path and unchanged synthetic file-content assertions. Its security goal now passes under the intentional rejection contract.

`identify_orphan_candidates` is a read-only helper that reports opaque-shaped regular filenames absent from a supplied metadata snapshot. It never deletes; legacy names and symlinks are excluded. A concurrent write, stale snapshot or manually named file may be falsely identified, and no automatic action should rely on the result. It is not wired to an API or scheduled cleanup. A synthetic test verifies reporting and legacy preservation. A further test opens an old artifact schema and verifies migration leaves old owner/path intact and ID/display-name NULL.

The frontend viewer can display `display_name` when supplied and falls back to its existing filename field. Artifact API access remains denied. The viewer's existing preview/download routes still use the filename field; no authorized ID-based API exists yet, and the frontend cannot be described as fully migrated or browser-verified. The registry-dispatched verifier and direct verifier still consume physical references, not owner-authorized artifact IDs. Safe ID resolution, path-free ordinary responses, missing-file states, task/source provenance, and actual frontend-to-API integration remain outstanding; enabling access would be unsafe.

Targeted Windows/Python 3.13, synthetic temp stores, plugin autoload and bytecode disabled, Python socket guard permitting literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153h_artifact_integrity.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py tests/backend/test_approval.py tests/backend/test_docx_and_execution.py::TestDocxCreate tests/backend/test_docx_and_execution.py::TestArtifactVerifierDocx -q -p no:cacheprovider --tb=short
# 31 passed, 1 skipped; 3.28 seconds
```

The skip remains the Windows directory-symlink privilege limitation. The artifact collision guard and L3-to-L1 and approval denial guards passed. After adding the legacy-schema test, `python -m pytest tests/backend/test_phase153h_artifact_integrity.py::test_legacy_artifact_metadata_is_preserved_without_assigning_id tests/backend/test_phase153_stabilization.py::test_artifact_registration_does_not_reassign_existing_owner -q -p no:cacheprovider --tb=short` with plugin autoload and bytecode disabled returned **2 passed, 10 warnings; 1.23 seconds** (warnings arise from disabled pytest-asyncio plugin/config during collection). The broad run preceded this test-only addition.

Frontend `npm run lint` exited 0 with existing warnings; local `tsc --noEmit -p tsconfig.app.json` and `tsconfig.node.json` both exited 0; local Vite production build to a unique temporary output directory exited 0 with chunk-size warnings. No frontend component/browser test was run. No real model, approved network-isolation verification, or rejected full-suite retry occurred. Python socket guards do not establish OS-level isolation.

Changed files in this continuation: `tests/backend/test_phase153_stabilization.py`, `tests/backend/test_docx_and_execution.py`, `backend/tools/artifact_storage.py`, `tests/backend/test_phase153h_artifact_integrity.py`, `frontend/src/components/artifacts/ArtifactViewer.tsx`, and this report. **PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No staging, commit, push, merge or deployment occurred.

## Phase 1.5.3H.2 intended public contract

The target public artifact descriptor contains only a server-issued `artifact_id`, a display name, an artifact type and appropriate status metadata. Filesystem paths and physical filenames are internal references, not IDs and not authority. An ID is not a bearer token: any future read or verification must independently revalidate current owner, linked task/conversation, clearance and source provenance. Legacy records without trustworthy identity remain restricted. Artifact list, preview, download and registry-dispatched verification stay disabled until those checks exist. Existing internal creation results carry `created_path` and physical `filename`; planner/tool observations, result summaries, logs and task data may propagate them, so safe public migration requires a dedicated serialization boundary rather than global free-text redaction. This is the desired contract, not a claim that every path has been migrated.

### Partial containment and exposure map

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; pre-existing uncommitted work preserved. The tool-result presentation path `AgentEngine._format_tool_result_summary` previously emitted `File: <opaque physical filename>` for `file_write`, `docx_create`, and `xlsx_report`. A synthetic test confirmed this. That one presentation path now emits a validated display name, or a generic success/failure message when the name is unsafe or an operation failed; arbitrary non-artifact result formatting is unchanged. This is not general path redaction and is not a complete artifact-ID boundary.

| Surface | Actual state |
| --- | --- |
| Artifact HTTP list/preview/download | Denied by existing fail-closed policy, including owners/admins. Legacy filename-based route shapes remain in code. |
| SSE tool-result summaries | The specific `ToolResult` artifact-summary formatter now avoids physical filenames; output test includes a nested path that is not traversed or serialized. This does not prove arbitrary nested SSE events are safe. |
| Planner/task SSE | Planning and approval event dictionaries may contain raw arguments/results; no exhaustive schema migration was made. |
| Agent model observations/direct answers | Internal raw tool result and filesystem paths can still reach model prompts or other formatting paths and may be echoed. Not fully contained. |
| Tool results, task state, logs | Internal `created_path` and physical `filename` still exist in tool results and can be propagated by consumers; logs and task persistence were not exhaustively remediated. |
| Frontend | Existing viewer still uses a filename for routes, although access is disabled; no ID-only UI migration occurred. |
| Verifier | Direct internal verifier accepts a storage path; registry-dispatched verifier remains disabled. It was not switched to an ID resolver. |

**No artifact-ID resolver was enabled or implemented in this pass.** Current records lack proven task/source provenance and current-authorization revalidation. Treating a matching owner or ID as enough would create a reading bypass; an authorized positive-resolution case therefore cannot honestly be verified while the current policy denies access. A safe future design must combine opaque ID lookup, stable server-side owner and current permission checks, task/conversation binding, source/clearance revalidation, strict storage-root/path checks and a race-safe open. No new public path-based access was added, and existing artifact access remains disabled. No tests claim cross-owner resolver denial or legitimate authorized ID resolution.

The original synthetic summary test failed before the change (`File: <physical name>`). Targeted Windows/Python 3.13 run with synthetic temporary data, plugin autoload and bytecode disabled, Python socket wrapper permitting literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153h_public_artifact.py tests/backend/test_phase153h_artifact_integrity.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py tests/backend/test_approval.py tests/backend/test_docx_and_execution.py::TestDocxCreate tests/backend/test_docx_and_execution.py::TestArtifactVerifierDocx -q -p no:cacheprovider --tb=short
# 35 passed, 1 skipped; 3.16 seconds
```

The skip is the Windows directory-symlink privilege limitation. Original collision, L3-to-L1 and approval/resume denial guards passed. The new tests cover one artifact-summary representation, unsafe display name, failure-message containment and a calculator compatibility control. They do not demonstrate path-free nested public responses, ID lookup, browser rendering, complete verifier integration or current-clearance checks. No full suite, real model or OS-level isolation was verified; the previously rejected full suite was not retried. Python socket wrapping is not OS isolation.

Files changed in this continuation: `backend/agent/engine.py`, `tests/backend/test_phase153h_public_artifact.py`, and this report. **PARTIAL / Phase 1.5.4 and Phase 2 NO-GO.** No staging, commit, push, merge or deployment occurred.

## Phase 1.5.3H.3 internal result separation

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; all prior uncommitted changes preserved. Existing artifact APIs and verifier dispatch remain denied, and unverified approval/resume remains disabled. No artifact-ID resolver was added.

`backend/security/artifact_presentation.py` defines an explicit scalar allowlist (`artifact_id`, sanitized `display_name`, fixed `artifact_type`) for the three creation tools and verifier. Raw tool results remain available to trusted internal execution for existing workflows, while `AgentEngine` uses the allowlist for these tools' model observations, direct answers and formatted prior-step results. Artifact arguments and failure details are omitted from the structured task-reasoning log; artifact failures from the tool registry use generic errors. A recovered synthetic task result was passed through the same formatter to test replay without rewriting stored data. Artifact tool-start SSE arguments and tool-result summaries are withheld/generic; planner SSE event dictionaries are projected to a bounded structural schema, excluding arbitrary nested result/argument fields. SSE runtime errors are generic. Non-artifact user content is not globally redacted.

The projection deliberately replaces free-form plan objective and step descriptions with generic labels, and does not expose raw approval reasons or task errors. This is a **planning UI compatibility regression** requiring a reviewed safe, provenance-aware description schema; no claim is made that all planner interactivity is unchanged. The direct verifier unit test remains available to trusted tests via an internal physical path, but registry-dispatched verification remains disabled. Existing opaque-creation and DOCX tests pass in the targeted run.

Before changes, four synthetic tests failed: all three creation tools' model observations included nested physical storage references, and a generated error included a private path. A separate synthetic planner-SSE test failed because a nested `created_path` was serialized. A prior-step reasoning test also failed because raw artifact arguments and error text were inserted into the model prompt. These tests now pass. Calculator summary remains a passing control; no real model was used.

Final targeted run, Windows/Python 3.13, isolated synthetic storage, pytest plugin autoload and bytecode disabled, Python socket wrapper permitting literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153h_tool_result_separation.py tests/backend/test_phase153h_public_artifact.py tests/backend/test_phase153h_artifact_integrity.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py tests/backend/test_approval.py tests/backend/test_docx_and_execution.py::TestDocxCreate tests/backend/test_docx_and_execution.py::TestArtifactVerifierDocx tests/backend/test_phase153g_response_boundaries.py tests/backend/test_phase153g_stream_revocation.py tests/backend/test_phase153g_new_session_binding.py -q -p no:cacheprovider --tb=short
# 60 passed, 1 skipped; 4.27 seconds
```

The skip is Windows symlink privilege. The artifact collision regression, L3-to-L1 guard, approval/resume denial and artifact-access restriction tests passed. The broad run is synthetic and does not establish OS-level network isolation, real-model behavior, frontend/browser integration or full-suite success. `git diff --check` returned 0. The rejected full-suite command was not retried.

**Remaining vulnerabilities and limits:** this is targeted containment, not proof that all storage references are confined. Other tools, arbitrary code output, user text, LLM-generated text, legacy task fields, tool descriptions, audit/error logs, and result objects that are mislabeled as non-artifact can still carry paths. Some model synthesis paths include free-form step descriptions; generic SSE deltas and model text are not globally scrubbed. The classifier cannot prove source provenance; task replay and artifact routes remain fail-closed rather than being made safe for disclosure. Direct tool invocation, filesystem tampering, independent registry writers, rollback, in-flight clearance changes and Windows reparse points remain unresolved. No resolver was implemented. Next highest priority: define a single typed, provenance-aware public event/result boundary and inventory all remaining emitters and replay paths; keep raw internal values out of model/public projections without treating tool-provided names as proof of authorization, then restore useful planner descriptions with targeted route and concurrency tests.

Files changed in this continuation: `backend/security/artifact_presentation.py`, `backend/agent/engine.py`, `backend/api/chat.py`, `backend/tools/registry.py`, `tests/backend/test_phase153h_tool_result_separation.py`, and this report. **Phase 1.5.3H PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No staging, commit, push, merge or deployment occurred.

## Phase 1.5.3H.4 typed public-event boundary

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; prior uncommitted work preserved. `PublicPlannerEvent`, `PublicPlan`, `PublicPlanStep` and `PublicArtifact` are explicit Pydantic presentation schemas with forbidden extra fields. The projection uses bounded structural fields and never recursively serializes arbitrary nested objects. Unknown planner event types return a generic error. Tool SSE names and agent statuses are restricted to server lists; arguments are empty except for a bounded arithmetic-only calculator expression, and summaries are generic except for a restricted calculator result. This changes informational detail but does not authorize tool execution. Existing task/artifact fail-closed policy remains unchanged.

Planner descriptions no longer repeat arbitrary LLM-supplied text. A fixed server-owned mapping of recognizable tool names produces labels such as `Search documents`, `Calculate`, or `Create Word document`; unknown tools fall back to `Task step`. The plan objective summarizes up to three fixed labels. A model-proposed tool name selects a label only; it is not proof that a tool was authorized, approved or executed. No document filename is treated as verified-safe in this pass. Before this change, a synthetic usability test expecting fixed labels failed because all steps read `Task step`; a separate test confirmed unknown tool events forwarded nested untrusted arguments. Both pass after the projection changes.

### Output-boundary inventory

| Producer / event | Internal or persisted source | Model-visible | Client-visible / authorization | Provenance and remaining risk |
| --- | --- | --- | --- | --- |
| Agent planner / `plan_created`, `plan_step`, task/approval events | Planner output, task metadata; tasks persisted separately | Internal planner and execution retain their own plan | SSE receives typed projection, structural IDs/status and fixed labels; conversation event guard applies | Labels are non-authoritative. Direct SQLite writers and in-flight revocation races remain. |
| Normal, tracked, multimodal tool start/result | Tool args, `ToolResult`; tracked task may persist internal plan/results | Artifact observations use allowlisted projection; other tools may supply substantive output | Existing `StreamChunk` plus allowlisted tool name/status, restricted args and summaries | Other model/tool outputs and arbitrary deltas remain unverified; raw internal fields are not globally erased. |
| Direct answers | Tool result or model output | Artifact direct formatter uses safe presentation; non-artifact workflows use their own content | Text may be streamed or returned after conversation identity checks | LLM output, user documents and non-artifact tools can still contain paths; cannot assert path-free free-form text. |
| Planner reasoning/replay | Internal step results, args, errors; legacy tasks can contain raw values | Artifact step result projection omits nested storage; artifact args/errors withheld in reasoning path | Unverified task detail and resume remain fail-closed | Some synthesis prompts and legacy fields still contain free-form descriptions; lineage is unverified. |
| Approval intercept and task endpoints | Task/approval records | Resume disabled for unverified tasks | Intercept and task responses remain guarded/redacted; user-facing task approvals denied | Current clearance is not continuously refreshed; no verified task provenance. |
| Error events | Exceptions and tool failure details | Artifact errors are generic in targeted paths | SSE runtime errors and projected task failures are generic | Other logs, errors and producers may still carry raw strings; no exhaustive proof. |
| Artifact API/verifier | Filename-keyed metadata and filesystem | Direct internal verifier still accepts paths | Artifact list/preview/download and registry verifier remain denied | No ID resolver or path-free public artifact API exists. |

The frontend dispatcher already consumes the preserved `plan_created`, `plan_step`, approval and task event names. Static type checks pass, but no component/browser test was performed and this is not proof of end-to-end event compatibility. Generic tool summaries/arguments and fixed rather than model-written planner descriptions reduce detail in the UI. `StreamChunk` retains its existing typed fields; no frontend API was enabled. Unknown events outside the planner projection, arbitrary text deltas, raw internal task records and independent writers remain outside this small boundary. Known and unknown provenance never grants artifact or task access.

Final targeted Windows/Python 3.13 run, synthetic isolated paths, pytest plugin autoload and bytecode disabled, Python socket wrapper permitting literal loopback only:

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153h_typed_public_events.py tests/backend/test_phase153h_tool_result_separation.py tests/backend/test_phase153h_public_artifact.py tests/backend/test_phase153h_artifact_integrity.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase153c_task_security.py tests/backend/test_approval.py tests/backend/test_docx_and_execution.py::TestDocxCreate tests/backend/test_docx_and_execution.py::TestArtifactVerifierDocx tests/backend/test_phase153g_response_boundaries.py tests/backend/test_phase153g_stream_revocation.py tests/backend/test_phase153g_new_session_binding.py -q -p no:cacheprovider --tb=short
# 63 passed, 1 skipped; 4.44 seconds
```

The skip is Windows symlink privilege. Tests include DOCX creation, calculator summary, artifact collision, L3-to-L1 task denial and approval/resume restrictions. Frontend `npm run lint` exited 0 with existing warnings; local `tsc --noEmit -p tsconfig.app.json` and `tsconfig.node.json` both exited 0. Local Vite production build to a unique temporary output directory exited 0 with chunk-size warnings. The rejected full suite was not retried; Python socket wrapping is not OS-level isolation.

Changed files in this continuation: `backend/security/artifact_presentation.py`, `backend/api/chat.py`, `tests/backend/test_phase153h_typed_public_events.py`, and this report. Registry freshness, cross-process coordination, current-clearance revalidation, unverified task provenance, artifact ID authorization, filesystem/reparse safety, direct internal paths and live-model/browser validation remain blockers. **Phase 1.5.3H PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.** No staging, commit, push, merge or deployment occurred.

## Follow-up: non-streaming error containment (2026-11-08)

The non-streaming chat route returned `str(RuntimeError)` in its 503 response and logged the same text. A synthetic exception containing a Windows artifact path reproduced the disclosure: the new regression failed before the fix. The route now returns `Chat generation unavailable` and logs a generic failure; it does not expose the exception text. This deliberately removes error detail from this log entry and is not evidence that other exception paths or logs are safe.

Changes: `backend/api/chat.py`, `tests/backend/test_phase153g_response_boundaries.py`, and this report. Existing uncommitted changes were preserved. The first attempt used an overly restrictive Python socket wrapper and failed in Windows asyncio setup because socketpair uses loopback; this was a test harness error, not an application result. The corrected wrapper permits literal loopback and blocks other `socket.connect` calls. It is not OS-level network isolation.

The following command used synthetic fixtures and isolated pytest temporary directories. The previously rejected full suite was not run:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_phase153h_typed_public_events.py','tests/backend/test_phase153h_tool_result_separation.py','tests/backend/test_phase153h_public_artifact.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_approval.py','tests/backend/test_docx_and_execution.py::TestDocxCreate','tests/backend/test_docx_and_execution.py::TestArtifactVerifierDocx','tests/backend/test_phase153g_response_boundaries.py','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153g_new_session_binding.py','-q','-p','no:cacheprovider','--tb=short']))"
# 64 passed, 1 skipped in 6.07s
```

The skipped Windows symlink test still requires privilege. Remaining risks include other unreviewed error and event emitters, free-form model/tool output, registry freshness and cross-process coordination, current-clearance revalidation, missing trusted task provenance, artifact authorization, and unverified Windows reparse behavior. Artifact access and unverified task approval/resume remain restricted. Live-model/browser tests, formal network isolation and the full suite remain unverified or blocked. **Phase 1.5.3H remains PARTIAL; Phase 1.5.4 and Phase 2 remain NO-GO.**

## Phase 1.5.3H.5 — bounded API and streaming error review (2026-11-08)

Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; pre-existing uncommitted work preserved. Synthetic tests first reproduced exception disclosure in document upload (ValueError and RuntimeError), document deletion, disabled-fallback knowledge graph, model preload, vision resolution and vision analysis, development unhandled HTTP errors, planner validation and exceptions, and a non-artifact tool error passed to model context. Changes replace those public/model-visible strings with server-owned messages. Document upload 400/503 and delete 404 statuses remain unchanged; absent filenames retain an explicit validation error. Planner task failure events and text are generic while internal task error data remains preserved. No automatic task or tool recovery was enabled.

`backend/security/public_errors.py` supplies fixed document errors; existing HTTP `detail`, SSE `type`/`content` and preload `status`/`error` contracts remain. Preload additionally returns `MODEL_PRELOAD_FAILED`, and the global 500 handler retains `INTERNAL_SERVER_ERROR`. **Stable error codes are not yet consistently supplied on other HTTP or SSE errors**, so the proposed shared contract is only partial. Caller-provided request IDs are echoed by existing tracing middleware and are not server-proven safe correlation identifiers. Existing diagnostic logs may contain raw exception text or traces and require controlled access; this pass did not establish log confidentiality. Persisted task errors are not rewritten and must remain access-restricted.

### Error-emitter inventory

| Producer | Source and client-visible fields | Model / persistence | Authorization and assessment |
| --- | --- | --- | --- |
| Document upload/delete | Service ValueError/RuntimeError formerly returned verbatim; fixed 400/503/404 `detail` | Service may log internal details; no task record | Permission dependency; synthetic direct-route tests reproduced and fixed. Detailed validation is reduced to a useful generic message. |
| Development middleware/global handler | Unhandled exception formerly appeared in 500 `detail`; now fixed message/code | Internal log retains traceback | Application middleware; reproduced through in-process ASGI. Both handler branches changed; only middleware path exercised. Existing request ID is client-supplied. |
| Knowledge graph / preload | Disabled fallback and preload formerly exposed exception; now generic errors | Internal warning/audit may retain exception | Permission-protected routes; synthetic tests reproduce/fix. Production KG path was already generic. |
| Vision resolution/analysis | Exception formerly emitted as string delta and tool summary; now generic | Internal logger may retain exception; no raw vision error sent in these paths | Conversation and streaming guards; engine tests use mocks, not live model. |
| Planner generation / validation | Exception/validation detail formerly emitted as text and structured error; now fixed messages | Original error may remain in persisted task state and logs | Planner SSE projects structured events; synthetic engine tests demonstrate text containment. Stored content remains restricted. |
| Tool failure and reasoning replay | Non-artifact error formerly passed to model and persisted-step reasoning; now generic for these paths | Internal ToolResult/error and task data remain; tool arguments and successful outputs have separate exposure risks | Synthetic formatter tests; no proof of comprehensive event or task lineage containment. |
| SSE runtime / planner projection | Stream exception handlers use fixed content; projected planner `task_failed` generic | Internal exceptions logged; free-form deltas remain | Conversation guard and tested projection; other emitters not exhaustively verified. |
| Approval intercept / task resume | Intercept projects dict errors, but string deltas are forwarded; task resume route also forwards raw data after provenance gate | Existing unverified task content/resume denied | Currently withheld by `task_content_revalidation_available()`; do not enable until its release boundary is secured and tested. |
| Artifact preview/download | Legacy route code includes exception details | Verifier and storage hold internal paths | Current access policy denies artifact reads before these branches; latent risk if denial is lifted, not demonstrated as reachable. |
| Other routes, background work, logs | Some task cancellation/image-validation paths and other service outputs contain variable strings | Raw errors may be persisted or logged | Not exhaustively audited; unknown and potential risks remain. |

### Batch 1 acceptance

| Area | Status | Evidence / limitation |
| --- | --- | --- |
| Public-event structural safety | PARTIAL | Planner and tool projections have targeted tests; free-form deltas, intercept paths and other emitters are not exhaustively bounded. |
| Model-visible observation containment | PARTIAL | Tested artifact and tool-error projections; successful non-artifact outputs, arguments and other synthesis paths remain unverified. |
| Planner description usability | PARTIAL | Fixed tool-category labels tested; browser/live planner behavior and richer descriptions remain unverified. |
| Error-boundary safety | PARTIAL | Reproduced cases fixed; stable codes are incomplete, and legacy/inactive emitters and logs remain. |
| Legacy replay restrictions | PARTIAL | Existing task content/resume fail closed; synthetic formatter tests do not prove all replay paths safe. |
| Remaining unaudited emitters | BLOCKED | Complete coverage and actual egress isolation are not demonstrated; rejected full-suite retry remains prohibited. |

### Targeted verification

Windows/Python 3.13, synthetic fixtures, temporary pytest storage, plugin autoload and bytecode disabled. The Python socket wrapper denies non-loopback `connect`, but is not OS-level isolation. Exact final command:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_phase153h5_error_boundaries.py','tests/backend/test_phase153h_typed_public_events.py','tests/backend/test_phase153h_tool_result_separation.py','tests/backend/test_phase153h_public_artifact.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_approval.py','tests/backend/test_docx_and_execution.py::TestDocxCreate','tests/backend/test_docx_and_execution.py::TestArtifactVerifierDocx','tests/backend/test_phase153g_response_boundaries.py','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153g_new_session_binding.py','-q','-p','no:cacheprovider','--tb=short']))"
# 76 passed, 1 skipped in 5.49s
```

The skip is the Windows directory-symlink privilege limitation. Targeted tests include artifact collision, L3-to-L1 task denial, approval restrictions, artifact-access restrictions, DOCX and calculator controls. `git diff --check` passed with line-ending warnings. No live model, real browser, full-suite retry, OS-isolation proof or Windows reparse-point verification was performed.

Files changed in this pass: `backend/security/public_errors.py`, `backend/api/documents.py`, `backend/api/knowledge_graph.py`, `backend/api/models.py`, `backend/agent/engine.py`, `backend/main.py`, `tests/backend/test_phase153h5_error_boundaries.py`, and this report. No staging, commit, push, merge or deployment.

Recommended next batch, subject to a separate instruction: address trustworthy task/source provenance and authorization before restoring task approval/resume or artifact access; first finish Batch 1's remaining error/output coverage and agree on a compatible stable-code contract. Independent SQLite writers, rollback, current-clearance changes, artifact identity authorization, reparse safety and runtime isolation remain release blockers. **Batch 1 PARTIAL; Phase 1.5.3H PARTIAL; Phase 1.5.4 NO-GO.**

## Phase 1.5.3H.6 — bounded reachable output audit (2026-11-08)

Branch `phase1-5-recovery-security`; existing uncommitted changes preserved. Tests reproduced raw exception disclosure from task cancellation, local model scan, image validation and direct non-artifact tool failures; unknown successful tool dictionaries also reached direct answers and model observations. Tests additionally showed that middleware accepted a caller-supplied correlation ID and ordinary SSE error chunks lacked stable codes. The SSE test initially had an incomplete mock and was corrected before the missing-code failure was reproduced. The unknown-dictionary test initially compared a backslash-containing path to JSON-escaped output and was strengthened before reproducing the filename leak.

The affected paths now return fixed messages. Unknown successful tool dictionaries are withheld from direct answers and generic model observations, which can reduce usefulness for unrecognized tools; known calculator, DOCX and artifact paths retain their existing targeted behavior. This does not sanitize arbitrary user content or successful output for known tools. Image validation now returns a generic format/size explanation; specific processor messages are not trusted. Cancellation remains 400, image validation 422, model scan retains its existing status/error fields. A server-generated request ID replaces client-supplied request IDs for middleware and error reporting. Selected HTTP error codes are sent through `X-Error-Code`; modeled SSE errors include `error_code` while preserving `type` and `content`; model scan returns `error_code`. Existing `public_errors.py` remains a fixed-message catalog, but **the code/message contract is not yet universal across every route or SSE emitter**. Existing client-supplied request-ID compatibility changes and frontend SSE consumers have not been browser-verified.

### Bounded emitter inventory

| Emitter / source | Reachability classification and evidence |
| --- | --- |
| Document upload/delete; KG fallback disabled; model preload; non-stream chat | Reachable, previously reproduced disclosures; fixed in H.5 tests. Current HTTP code consistency remains partial. |
| Task cancel, model scan, image validation | Reachable; synthetic exception disclosure reproduced and fixed in H.6 tests. Auth dependencies still apply. |
| Plain SSE runtime error and conversation revocation | Reachable; synthetic tests show generic error content and code; existing revocation tests pass. Other stream handlers have code additions but each variant was not independently exercised. |
| Planner failure, vision failure, tool error, unknown tool result | Reachable via agent stream/model formatting; targeted mocked tests show fixed public/model presentations. Planner descriptions and free text remain unverified end to end. |
| Approval intercept | Reachable dispatch, but real `resume_agent_task` immediately yields the existing unverified-provenance denial. Route still forwards string deltas if a different resume implementation is substituted; code paths behind the denial are latent, not demonstrated accessible under the current policy. Existing tests mock resume for revocation checks. |
| Task approval, resume and legacy task replay | Currently blocked by `task_content_revalidation_available()` returning false; task detail withholds result/plan/error. Persisted raw errors remain intact. Do not enable without provenance and boundary tests. |
| Artifact preview/download and registry-dispatched verifier | Currently blocked by existing artifact policy; latent exception formatting in disabled artifact branches remains unverified, not a confirmed reachable exposure. |
| Successful known non-artifact output and diagnostic tools | Reachable but unverified: file contents, document snippets and model deltas may be legitimate content; `hardware_status`, `model_scan`, `security_diagnostics` and reasoning paths serialize tool-provided fields, potentially including internal metadata. No provenance guarantee or global text redaction was added. |
| Background tasks, unexpected framework errors, other HTTP routes | Reachable but not exhaustively traced; production/global error paths use generic 500 responses, with only the development middleware exercised in H.5. Input validation and less common errors need further audit. |
| Internal logs and persisted raw task records | Internal-only by intended boundary, not proven confidential operationally. `backend/main.py` logs exception tracebacks; engine, chat, document and registry logs may record exception text, tool arguments or task details. Restrict log read access, configure bounded retention and protect backups; no logs were deleted or rewritten. |

### Acceptance and blockers

| Criterion | Status | Evidence |
| --- | --- | --- |
| Tested reachable error disclosures | PARTIAL | Listed synthetic cases fixed; unreviewed emitters remain. |
| Error contract and correlation | PARTIAL | Server-generated ID, selected HTTP headers and SSE codes implemented; not all error variants use the catalog or code field. |
| Free-form and model observations | PARTIAL | Generic unknown dictionaries withheld; known tool outputs, diagnostics and arbitrary generated text lack complete provenance. |
| Approval, replay and artifact restrictions | PASS for targeted guards | Denial tests pass; this is not proof that latent paths are safe if enabled. |
| Browser, live model and network isolation | BLOCKED / NOT VERIFIED | No browser/live service or OS-level isolation run; the rejected full suite remains blocked. |

Final synthetic Windows/Python targeted run, temporary pytest storage, plugin autoload and bytecode disabled; the Python wrapper permits literal loopback and denies other `connect` calls, not OS-level egress isolation:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_phase153h6_error_boundaries.py','tests/backend/test_phase153h5_error_boundaries.py','tests/backend/test_phase153h_typed_public_events.py','tests/backend/test_phase153h_tool_result_separation.py','tests/backend/test_phase153h_public_artifact.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_approval.py','tests/backend/test_docx_and_execution.py::TestDocxCreate','tests/backend/test_docx_and_execution.py::TestArtifactVerifierDocx','tests/backend/test_phase153g_response_boundaries.py','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153g_new_session_binding.py','-q','-p','no:cacheprovider','--tb=short']))"
# 84 passed, 1 skipped in 5.68s
```

Skipped: Windows directory-symlink creation needs privilege. Tests include collision, L3-to-L1, approval/resume, artifact access, DOCX and calculator guards; no real model or browser. Files changed in this pass: `backend/security/public_errors.py`, `backend/api/tasks.py`, `backend/api/models.py`, `backend/api/chat.py`, `backend/api/documents.py`, `backend/schemas/chat.py`, `backend/main.py`, `backend/agent/engine.py`, `tests/backend/test_phase153h6_error_boundaries.py`, and this report. No staging, commit, push, merge or deploy.

**Batch 1 NO-GO / PARTIAL; Phase 1.5.4 NO-GO.** Before any next batch, resolve or explicitly scope the remaining known-tool/model output and uniform error boundary. Task/source provenance, independent registry writers, clearance changes, rollback, artifact authorization, OS isolation and Windows reparse safety remain later security gates. Do not lift fail-closed restrictions or automatically begin Batch 2.

## Phase 1.5.3H.7 — tool presentation review (2026-11-08)

Branch `phase1-5-recovery-security`; pre-existing uncommitted work preserved. Five synthetic tests first failed: nested diagnostic metadata reached observations and synthesis; unverified code stdout reached presentation; and unknown prior-step dictionaries were serialized. `backend/security/tool_presentation.py` now provides bounded numeric CPU/RAM percentages, model counts, a restricted security-diagnostics message, and a code-output-unavailable message. The engine uses these projections for direct answers, model observations, prior-step formatting and summaries; raw results remain internal. Unknown prior-step dictionaries are withheld. Calculator numeric results are projected separately and nonnumeric values are withheld. These projections make no authorization or source-provenance decision. Free-form step descriptions and several other model synthesis inputs remain outside this policy.

### Registered tool inventory

The following inventory reflects `_register_tools` in `backend/main.py`, not a live-service test. Tool dispatch role/approval checks apply independently; no name or returned field proves authorization. Current columns describe presentation after this pass, and limitations apply to both model and client paths unless otherwise stated.

| Registered tool | Category / internal fields and sensitivity | Current presentation and authorization/provenance limitation |
| --- | --- | --- |
| `calculator` | Arithmetic `result`, `expression` | Numeric scalar only through reviewed engine formatters; no authorization inferred. |
| `document_search` | Retrieved text, filename, page, chunk/document IDs, score, relative path and classification-sensitive content | Existing retrieval uses server-derived clearance; current formatters expose text and selected metadata. **No trustworthy per-result current-access revalidation at presentation/replay was established.** |
| `file_list` | Workspace filenames, sizes and extensions; can reveal organizational metadata | Existing formatter; no demonstrated document-level authorization for every file or current-access revalidation. |
| `file_read` | Filename and file content; can contain classified or private text | Existing formatter; current owner/classification provenance cannot be demonstrated for every file. |
| `file_write` | Opaque storage path, physical filename, artifact ID, display name | Existing artifact projection exposes limited ID/display/type. Artifact reading remains denied and ID is not authorization. |
| `xlsx_report` | Artifact path, filename, ID, display name, workbook metadata | Same artifact projection and restrictions. |
| `docx_create` | Artifact path, filename, ID, display name, hash | Same artifact projection and restrictions; DOCX creation tested synthetically. |
| `artifact_verifier` | File path and inspection details | Registry-dispatched verification remains disabled; artifact presentation does not authorize reads. |
| `code_execution` | Arbitrary stdout/stderr, exit code, paths and possible secrets | Output withheld in reviewed presentation functions because provenance is not established; functionality is intentionally reduced. This does not prove every synthesis or tool path is contained. |
| `knowledge_graph_query` | Graph entities, relationships, citations, classification-sensitive data | Unknown/generic step result is withheld by the updated fallback, but other routes/output paths are not fully audited. Graph access and classification must be checked by service. |
| `hardware_status` | CPU/RAM/GPU telemetry and internal diagnostics | Only bounded numeric CPU/RAM percentages through reviewed formatters; no raw nested metadata. |
| `model_scan` | Installed/running models, runtime metadata and potential local paths | Counts only when expected lists exist; otherwise generic. |
| `security_diagnostics` | Security posture, network, storage and audit configuration | Generic completion only in reviewed formatters; `VIEW_SECURITY` checks remain separate. |
| Unknown/unregistered | Arbitrary nested results and tool-provided metadata | Direct generic dictionaries, generic observations and generic prior-step fallback withheld in reviewed functions; no claim about all successful strings or other pathways. |

### Acceptance and limitations

**PARTIAL / Batch 1 NO-GO.** This pass does not satisfy the requested complete tool-specific contract or prove that document retrieval exposes only currently authorized content: document/file outputs lack trustworthy presentation-time provenance, several formatter and synthesis paths forward descriptions or successful known-tool content, and authorization cannot safely be inferred from tool names or raw result fields. Retrieval was not disabled or relabeled as verified; the existing role/clearance controls remain unchanged. Code output is now deliberately unavailable in reviewed presenters, and diagnostics/model details are reduced. Real-model, browser, planner end-to-end, live vector, clearance-change and Windows reparse-point behavior are unverified. Existing unverified task approval/resume, artifact viewing/download and registry verification remain denied. The previously rejected full suite was not retried, and Python socket guarding is not OS-level isolation.

Exact targeted synthetic Windows/Python command; pytest temporary storage, plugin autoload and bytecode disabled:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_phase153h7_presentation.py','tests/backend/test_phase153h6_error_boundaries.py','tests/backend/test_phase153h5_error_boundaries.py','tests/backend/test_phase153h_typed_public_events.py','tests/backend/test_phase153h_tool_result_separation.py','tests/backend/test_phase153h_public_artifact.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_approval.py','tests/backend/test_docx_and_execution.py::TestDocxCreate','tests/backend/test_docx_and_execution.py::TestArtifactVerifierDocx','tests/backend/test_phase153g_response_boundaries.py','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153g_new_session_binding.py','-q','-p','no:cacheprovider','--tb=short']))"
# 91 passed, 1 skipped in 5.88s
```

The skipped Windows symlink test requires privilege. The run includes collision, L3-to-L1, conversation revocation, approval and artifact denial, calculator and DOCX guards, but is not real-model or browser proof. Files changed in this pass: `backend/security/tool_presentation.py`, `backend/agent/engine.py`, `tests/backend/test_phase153h7_presentation.py`, and this report. No staging, commit, push, merge or deployment. **Phase 1.5.4 NO-GO; no H.8 or Batch 2 started.**
