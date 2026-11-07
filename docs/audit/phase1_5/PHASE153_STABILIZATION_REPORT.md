# Phase 1.5.3 stabilization report

Date: 2026-11-07. Branch `phase1-5-recovery-security`, HEAD `88df0d1`; initial working tree clean. Decision for **Phase 1.5.4: NO-GO**. Phase 1.5.3 stabilization remains incomplete. The prior rejected full suite was not retried. No live model, browser, real Chroma or OS-level isolation verification was performed.

## Findings and immediate change

1. **Confirmed synthetic task disclosure:** `backend/api/tasks.py:get_task` checks role and ownership but returns task result, plan, step arguments, results and errors without current source-provenance checks. A synthetic task owned by an operator with stored L3 clearance returned its result when the same owner had L1 clearance. Task list exposes user request summaries; approval history exposes reasons; resume can consume prior task state. The latter paths are supported by code inspection and were not demonstrated as complete exploits here. Source reclassification/deletion cannot be enforced from task records because the needed lineage is absent.
2. **Confirmed synthetic artifact registry collision:** `backend/agent/task_store.py:save_artifact` updates `user_id` on filename conflict. The isolated test shows a second registration can reassign the record owner. Whether an external caller can exploit a particular production creation path was not established. File content overwrite may occur before registration, so a registry-only check would not close the lifecycle issue.
3. **Confirmed continuity regression:** `_build_messages` excludes all prior assistant messages, including a verified deterministic calculation. Task reasoning uses a similar exclusion. This avoids some provenance laundering but prevents requested multi-turn continuity.
4. **Confirmed empty-source ambiguity:** `_retrieve_context` returns an empty list for no service, no documents, skipped search, timeout, failures and no results. Previously `source_provenance([])` tagged such a response as plain chat with no dependencies and `may_read_assistant` treated that as readable. This phase changes both paths to fail closed: new empty-source results receive no provenance, and earlier `plain_chat` records with empty sources are withheld. Deterministic calculation provenance remains distinct. Compatibility impact: ordinary no-source plain-chat answers are withheld on reload until trustworthy explicit provenance exists. Data is preserved.
5. **Attachment boundary remains incomplete:** descriptors are stored within an owner-bound conversation but no approved metadata/content recovery endpoint is implemented. Legacy files, missing files, deletion lifecycle, classification and reference binding remain unverified. No new endpoint was exposed.

## Why implementation stopped

The requested end-to-end security and continuity needs a shared provenance and resource-ownership model across model turns, tools, tasks, plans, approvals, outputs, artifact creation and attachments. Adding a task-only clearance comparison, reusing an empty source list, or restoring context without transitive lineage would give false confidence and can either leak classified data or break approved workflows. In accordance with the request, no rushed broad workaround or Phase 1.5.4 implementation was attempted. Existing known disclosure paths therefore remain open; this is not a security sign-off.

## Proposed architecture for review

- Produce a server-owned execution provenance record with an explicit dependency state: independently verified, exact source/chunk and version dependencies, inherited assistant/task/tool dependencies, or unknown. Distinguish retrieval skipped, empty, failure and timeout. Bound size and reject missing or malformed dependencies rather than accepting empty lists as proof.
- Carry transitive lineage through approved tool inputs/outputs, planner steps, resumed tasks and generated artifacts. Persist it atomically with the corresponding output and status, with restart-safe idempotency. Preserve existing legacy data as unknown, without auto-classification.
- Centralize current-user, role, clearance, ownership, source existence, exact chunk version/classification and dependency checks. Apply them consistently at history, model-context assembly, task list/detail/approvals/resume, artifact list/preview/download and attachment metadata/content endpoints. Deny or withhold on uncertainty; explicitly decide whether administrators can ever bypass source clearance.
- Revalidate persisted task inputs and approvals before side effects, bind approval to the exact current task, arguments and provenance, and never automatically execute on hydration. Define a safe interruption/retry protocol before implementing resumability.
- Replace filename ownership as the artifact identity with collision-resistant IDs and atomic exclusive creation/registration, enforce task/session owner binding and controlled migration of legacy unowned records. Ensure registration failure cannot occur only after overwriting another user's file.
- Gate attachment access through a stable server record bound to conversation/task owner and current classification; cover guessed IDs, traversal, missing content, deletion, orphan cleanup and no raw bytes in conversation records. Keep existing attachments quarantined until binding is established.
- Add isolated actual-route, concurrent, restart, downgrade, reclassification, deletion, replay and browser tests, then validate genuine local model/RAG workflows under separately approved OS-enforced egress isolation.

## Tests and evidence

Targeted Python tests executed in a disposable Git archive of HEAD with the changed provenance module and test files overlaid; temporary stores, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`:

```text
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153_stabilization.py tests/backend/test_phase153_security.py tests/backend/test_phase15_app_integration.py tests/backend/test_phase15_registry_continuation.py tests/backend/test_phase15_memory_continuation.py -q -p no:cacheprovider --tb=short
# 13 passed, 0 failed, 0 skipped, 3 xfailed; 15.87s
```

The three strict xfails are open acceptance guards, not passes. To confirm they fail on the intended assertions, the same isolated setup ran:

```text
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153_stabilization.py -q -p no:cacheprovider --runxfail --tb=short
# 0 passed, 3 failed, 0 skipped; 1.41s; pytest exit 1
```

Those failures demonstrate the synthetic task disclosure, missing safe-context continuity and artifact-record reassignment described above. The route integration tests substitute model/vector services and use Python-level socket guards; that is not OS-level egress enforcement.

Frontend `node --experimental-strip-types --test tests/historyRequests.test.mjs`: **4 passed, 0 failed, 0 skipped**, helper tests only. `npm run lint`: exit 0 with warnings. App and node `tsc.cmd --noEmit --incremental false -p ...`: exit 0. Vite build to a fresh temporary directory: exit 0 with chunk-size warning. No real-browser test was run.

## Changed files

`backend/security/conversation_access.py` fail-closes empty-source records; `tests/backend/test_phase153_security.py` updates the security regression expectation; `tests/backend/test_phase153_stabilization.py` adds three strict expected-failure acceptance guards. This report and the appended audit addenda document findings. No runtime data was read or modified intentionally.
