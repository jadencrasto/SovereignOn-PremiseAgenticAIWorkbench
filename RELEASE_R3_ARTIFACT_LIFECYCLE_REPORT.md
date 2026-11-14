# R3.1 Artifact lifecycle and verification

**R3.1: PARTIAL. R3: PARTIAL. Production artifact writing/publication: BLOCKED. Production: NO-GO.** This milestone corrects an isolated verification defect and a tool-availability contract defect; it does not establish an execution-owned artifact receipt or activate staging.

## Production path and constraints

The planner and plan validator lead to agent dispatch, which refreshes request identity and checks task/conversation ownership. The registry enforces role, registration, approval and argument checks, then blocks `docx_create`, `xlsx_report` and `file_write` before generator execution or approval consumption. The engine independently rejects successful claims from those names and `artifact_verifier` as dependency evidence, even with a permissive registered verifier. Artifact access is separately blocked by task/source provenance revalidation. The TaskStore has artifact IDs and task/owner metadata, but these records do not bind a verified output to an invocation, current authorization, protected staging object, approval or independent source freshness.

The A7.2 `generate_verified_report` wrapper uses the existing isolated generators and reopens temporary synthetic output. Its generator-created opaque ID, digest, source map, bytes and `verified_unpublished` state are not an execution-owned production identity, proof of authorization, durable record or publication authority. It has no trusted association to an agent task, invocation or approval. This work does not manufacture a trusted receipt from those fields. The required production boundary must allocate a server-owned operation and staging object under a verified restricted writer, bind it to current owner/conversation/task/invocation and approval/source dependencies, independently verify exact immutable bytes, and revalidate those bindings before any receipt or publication. No such protected storage primitive is presently available; no permissive callback, alternate writer, artifact database or privileged staging path was added.

## Concrete changes

- The isolated XLSX verifier previously checked expected cells only. An additional formula outside the expected range could survive even when the generator returned a matching digest. It now inspects bounded worksheet XML independently of the declared worksheet dimensions, rejects formulas and out-of-range cells, checks the one supported worksheet relationship, rejects duplicate ZIP names and bounds worksheet/expanded archive sizes. A forged-dimension fixture with an extra formula and a matching self-reported digest is rejected. Literal formula-looking strings in expected cells remain supported by existing tests. This is a limited structural check, not a complete Office active-content, archive-bomb, semantic or layout security assessment.
- `VerifiedReport.state` can no longer be supplied through its constructor as `published`. It remains an isolated `verified_unpublished` result and is not a production receipt. This does not make arbitrary in-process Python objects trustworthy.
- `GET /api/tools` now reports server-derived `blocked` availability for the existing hard-blocked artifact writers and for the artifact verifier while task provenance is unavailable. The registry and inventory share the writer-name set. The existing configured `enabled` field is preserved, while the tools view displays unavailable and the reason. `unknown` is used where configuration alone does not prove actual availability. Frontend changes were statically inspected only.

## Verification and limits

From the repository root with `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'`:

```powershell
python -m pytest -q tests/backend/test_r31_artifact_contract.py tests/backend/test_i10_artifact_integrity.py tests/backend/test_i101_artifact_boundary.py tests/backend/test_a71_artifacts_offline.py tests/backend/test_a72_verified_report_offline.py tests/backend/test_phase153h_public_artifact.py tests/backend/test_i81_authorization_freshness.py tests/backend/test_i8_execution_integrity.py tests/backend/test_i112_numeric_receipts.py tests/backend/test_a33_production_tools_offline.py
python -m pytest -q tests/backend/test_a91_admin_offline.py tests/backend/test_r31_artifact_contract.py tests/backend/test_i1_document_gate_offline.py tests/backend/test_r23_replacement_intents.py
```

**77 passed, 1 warning in 14.07 s** and **32 passed, 1 warning in 3.52 s**. Warnings are pytest's unknown `asyncio_mode` option with plugin autoload disabled. These tests use synthetic inputs, actual isolated DOCX/XLSX/PDF generation and reopening, production registry/engine modules, in-process routes, temporary storage and fake providers. Coverage includes wrong/corrupt formats, changed digest, formula injection, fabricated artifact claims, no approval consumption on blocked dispatch, source-grounding denial, existing authorization freshness, preview/access withholding, numeric receipts and calculator/conversion workflows. A PDF text check is not visual-layout validation. No real service or socket was started and no test timed out.

An earlier narrower run returned **35 passed, 1 failed, 1 warning in 4.43 s**: the new XML check correctly rejected a formula in the title, but an existing test expected the previous title-specific error text. The test was updated to accept the formula rejection; the later 77-test run passed. `git diff --check` passed with Windows LF/CRLF warnings.

No frontend execution tests were run. **TypeScript, production build, mounted React/browser and live Ollama: NOT VERIFIED.** The prior automatic approval review rejected frontend build continuation because of possible registry access, and the existing frontend harness invokes the restricted TypeScript transpiler; neither it nor an alternate path was run. Static inspection shows artifact preview/download controls remain dormant and account-keyed remounting exists, but does not prove browser behavior.

## Remaining gate and next task

No real execution-bound owner/conversation/task/invocation identity, duplicate-operation protection, independent verification-to-task receipt, protected staging, publication, process-restart recovery or source-revocation-aware artifact lifecycle has been implemented. Thus wrong-task/cross-owner evidence and interrupted/restarted production operations cannot be validated as successful workflows; they remain blocked rather than tested through a fabricated positive path. Unknown temporary files remain quarantined, and cancellation does not prove a side effect was undone.

Next independently verify the existing restricted broker's writer/handle identity and durable recovery boundary in an authorized isolated environment, then bind staging and verification to fresh task, approval and source authority before integrating a production receipt. Keep artifact writing, publication, protected RAG, generated code and approval resume blocked until their own gates are satisfied. No application, Ollama, Chroma, launcher, Protected service or VM was started; no dependencies were downloaded and no commit, merge or push was made.

A final targeted rerun after rejecting duplicate workbook relationship IDs used `python -m pytest -q tests/backend/test_r31_artifact_contract.py tests/backend/test_i10_artifact_integrity.py tests/backend/test_a72_verified_report_offline.py` with the same plugin setting: **23 passed, 1 warning in 2.83 s**. The warning was the same unknown `asyncio_mode` option.
