# Batch 2B.3E.2 — Workflow integrity and test compatibility

Date: 2026-11-12. Branch `phase1-5-recovery-security`. **Release decision: NO-GO.** Production document ingestion, retrieval, task approval/resume, artifact reads, protected workspace/graph reads and generated-code execution remain disabled. No production authorization or persistence gate was modified. Existing tracked and untracked work was preserved; no commit, push, merge, deployment or VM modification occurred.

## Files changed

- `scripts/seed_demo_data.py`: replaces the unsafe, misleading staging/indexing workflow with an explicit blocked result and nonzero exit. It does not import or initialize Chroma/Ollama or write files.
- `eval/rag_eval.py`: returns a machine-readable unavailable result with no retrieval metrics while the existing document authorization gate denies access. Index preparation tracks failed attempts and returned IDs; scoring is blocked unless every attempted ID is distinct and visible as eligible. Errors are generic.
- `tests/backend/test_batch2b3e2_offline_stdlib.py`: offline workflow, fail-closed and explicitly test-only dormant manifest/readback fixtures.
- This report. The four historical test files remain unchanged, so they retain their original evidence and their incompatible expectations remain visible.

## Workflow behavior

Previously, demo seeding copied source documents directly to uploads, attempted ingestion without a registry, swallowed errors and printed a success banner. It also staged a CSV, generated an image and performed database/sandbox probes. The current script **does none of these operations**: it reports `attempted=0 ingested=0 failed=0 skipped=3` for the three required documents and exits 1. All required seeding is explicitly incomplete. This deliberately suspends the entire demo seeder until a reviewed authenticated operator ingestion path exists; no file staging substitutes for authorized ingestion. It does not currently attempt individual documents, so the distinction between failed and skipped is reflected by the actual counters.

Previously, the evaluator ignored ingestion failures and could report zero retrieval quality as a measurement. It now checks the existing `document_content_allowed()` before any model probe or vector-store initialization and records each QA case as `ENVIRONMENT_UNAVAILABLE`, includes a blocked reason in `errors`, and emits an empty `summary_metrics` object. The direct CLI exits nonzero for an unavailable result. If a future separately authorized code path reaches index preparation, any failed document, empty set, duplicate IDs or missing eligible IDs blocks scoring. A configured model, raw vector count or successful pending ingestion is not a retrieval authorization. Normal result persistence still writes the evaluation JSON/Markdown when the evaluator is invoked; this batch did not invoke the real evaluator or alter historic outputs. The existing consolidated evaluation runner also has other probes and was not run.

## Classification of the 23 historical cases

Names below refer to the four files named in the previous audit. “Dormant fixture” means the historical scenario requires a separately isolated internal fixture before it can be exercised; it does not authorize passing the production gate. The cases were reviewed, not executed or rewritten.

| File | Case | Classification |
| --- | --- | --- |
| `test_batch2b3_ingestion_preconditions.py` | `test_current_upload_remains_pending_and_retrieval_denied` | Update to denial expectation; prior pending upload no longer occurs. |
| same | `test_same_filename_and_prefix_preserve_distinct_uploads` | Dormant fixture for post-gate allocation. |
| `test_batch2b3a_upload_integrity.py` | `test_forced_collision_does_not_overwrite` | Still valid direct synthetic upload-helper test. |
| same | `test_concurrent_identical_uploads_get_distinct_files` | Still valid direct helper test, subject to its filesystem assumptions. |
| same | `test_digest_mismatch_is_rejected_and_file_preserved` | Still valid direct helper test. |
| same | `test_exclusive_creation_failure_never_falls_back` | Still valid direct helper test. |
| same | `test_partial_write_rejected_without_removing_orphan` | Still valid direct helper test. |
| same | `test_index_id_collision_retries_without_touching_old_index` | Still valid direct helper test. |
| same | `test_index_failure_preserves_new_file_and_legacy_file` | Dormant fixture; current denial creates no new upload. |
| same | `test_index_uses_server_identity_and_revision` | Dormant fixture for post-gate index binding. |
| same | `test_identical_uploads_and_deletion_preserve_other_files` | Dormant fixture for post-gate lifecycle. |
| `test_batch2b3b_pending_ingestion.py` | `test_pending_record_precedes_file_and_retains_readback_evidence` | Dormant fixture; new ingestion cannot reserve. |
| same | `test_revocation_commits_before_failed_index_cleanup` | Dormant fixture using pre-existing synthetic PENDING record. |
| same | `test_incomplete_index_or_revocation_never_records_verified[missing]` | Dormant fixture; missing readback validation exercised in new isolated fixture. |
| same | `test_incomplete_index_or_revocation_never_records_verified[extra]` | Dormant fixture; extra readback exercised in new isolated fixture. |
| same | `test_incomplete_index_or_revocation_never_records_verified[mismatch]` | Dormant fixture; changed readback exercised in new isolated fixture. |
| same | `test_incomplete_index_or_revocation_never_records_verified[revoked]` | Dormant fixture for registry conditional update and concurrent revocation; not established by the mock readback test. |
| same | `test_mismatched_clearance_rejected_before_reservation` | Update denial expectation: persistence gate runs before clearance check. |
| same | `test_index_failure_remains_pending_after_restart_and_cross_owner_denied` | Dormant fixture for post-gate failure and owner isolation. |
| same | `test_source_mismatch_keeps_pending_and_does_not_index` | Dormant fixture for post-gate source validation. |
| `test_batch2b3c_reconciliation.py` | `test_index_configuration_records_unknown_model_and_fallback` | Obsolete expectation that fallback ingestion succeeds; future evidence capture needs an isolated fixture. |
| same | `test_restart_never_repromotes_and_preserves_evidence` | Still valid registry-only test. |
| same | `test_inconsistent_embedding_dimensions_remain_pending` | Dormant fixture for post-gate dimensionality check. |

## Coverage and limitations

The new tests execute the actual ingestion method body with the real unchanged deny condition and a synthetic SQLite registry containing existing PENDING and REVOKED rows. They check the registry rows are identical afterward, no new row or upload directory exists, no embedder or index insertion is invoked, and eligibility and content authorization remain false. Prior 2B.3E.1 tests also exercise denial for multiple backend states and a forged status.

For dormant coverage only, a clearly named fixture extracts the manifest-to-readback statement block from the actual source AST and executes it with fake stores, synthetic chunks and fake registry calls. It checks matching readback and missing, extra, altered and wrong-revision readback. It does **not** call ingestion, patch the production gate, test a real database transaction, establish durable indexing, or prove authorization. The fake registry does not test revocation races or conditional updates. Windows reparse points, independent writers, real persistence, atomicity and rollback remain unverified.

The evaluator tests exercise extracted production function bodies with fake persistence and service dependencies; they verify blocked status, empty metrics, eligibility conditions and sanitized ingestion errors. They are not full CLI/framework integration tests. No new positive authorization path or policy setting exists.

## Tests and cleanup

Commands on Windows PowerShell:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -I tests/backend/test_batch2b3e2_offline_stdlib.py -v
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -I tests/backend/test_batch2b3e1_offline_stdlib.py -v
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -I tests/backend/test_batch2b3e_offline_stdlib.py -v
python -I scripts/seed_demo_data.py
```

Final results: **20 passed, 0 failed, 0 skipped** (6 new, 7 previous compatibility, 7 previous snapshot tests). The seeder command separately produced the expected blocked output and exit code **1**. The **23 reviewed historical cases were not run**; this count does not represent the full uncollected suite. A first development run of the new file had **3 passed and 2 errors**: a fake backend identity was not JSON-serializable and a synthetic SQLite read left a Windows file handle open. The fixture was corrected, a sixth test added, and the final 6/6 run passed. The development attempt may have left its synthetic temporary database in the system temporary area; unrelated temporary files were not removed.

All tests used standard-library imports, AST-extracted source and fakes. No real Chroma, Ollama, Docker, VM or previously rejected full suite was started; external pytest plugin autoload was disabled. No independently enforced network boundary is claimed. `git diff --check` for the two tracked workflow files exited 0 with line-ending warnings; it does not cover untracked files.

The previously test-created `unused` directory was inspected and appeared empty, but Windows reports it as a read-only reparse point. A deletion attempt was interrupted; it was left untouched, with no recursive deletion or change to VM evidence.

## Next batch

Design an authenticated operator seeding flow and explicit test-only seams for the remaining dormant lifecycle and real concurrent revocation tests, without overriding production gates. Independently establish filesystem handle identity, writer exclusion and cross-store freshness before considering recovery verification. Review the consolidated evaluation runner's aggregate semantics and independently verify operational network-attempt monitoring before any release consideration.
