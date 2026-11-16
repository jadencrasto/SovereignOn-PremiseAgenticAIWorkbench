# R8.10 — Repository recovery checkpoint preparation

**Preparation PASS. Checkpoint awaits explicit approval. Production NO-GO.**

## Checkout and scope

Current branch `main`, HEAD `43d72b8036b27b7926a74d08af260f865259669b`. The local remote-tracking ref `origin/main` is at the same SHA. No fetch was performed, so this records local ref parity only. The initial index had zero changes. The worktree contained 47 tracked modifications with 2,816 insertions and 598 deletions, no tracked additions or deletions, and 22 nonignored untracked files. This report adds one untracked document. No application or configuration file was edited in R8.10.

| Classification | Initial count or location | Checkpoint disposition |
|---|---|---|
| A · Application source | 30 paths, including three new Python modules | Group 1 allowlist |
| B · Tests and fixtures | 23 paths, including five new tests | Group 1 allowlist |
| C · Documentation | 14 release reports, plus this report | Group 2 after documentation privacy review |
| D · Configuration and templates | `.env.example`, `frontend/vite.config.ts` | Group 1 allowlist |
| E · Generated outputs | Ignored frontend build, .NET `bin`/`obj`, native VM ISOs and local packaging outputs | Exclude |
| F · Runtime data and diagnostic output | Ignored memory, logs, uploads, generated reports, databases, indexes, sandbox files and current evaluation output | Exclude |
| G · Credentials or secrets | No real secret identified in the candidate scan. Runtime stores and private local config remain excluded. | Exclude any future credential material |
| H · Models, caches and environments | No model payload or virtual environment in the candidate set. Existing dependency directories and caches are ignored. | Exclude, including external model storage |
| I · Manual review | Machine paths in 13 candidate reports. Historical evaluation artifacts already tracked, described below. | Review before remote publication |

`git status --ignored=matching` returned 1,422 entries, including directories listed as single entries. This is an ignore inventory, with no need to inspect upload names or payload contents as part of checkpoint review.

## Security and ignore review

- The redacted static scan covered all 69 original candidate files. It looked for key and token signatures, private-key headers, JWT and long bearer/cookie shapes, credentials in URLs, and credential-like literals. The two flagged test values are synthetic inputs to temporary in-process auth stores. Provider-key examples in `.env.example` are commented placeholders and predate this checkpoint. The template's only current change is a comment. No deployable credential, real user record, raw conversation or document payload was identified in the candidate set. Pattern and source review have limited coverage, so repeat the review on the actual staged contents after approval.
- Keep `data/memory.json`, `data/memory.json.lock`, `data/{tasks,chromadb,chromadb_eval,uploads,uploads_eval,logs,sandbox}/`, `eval/results/`, environment files, local tooling, build outputs, model payloads and dependencies out of the allowlist. The memory JSON, SQLite and vector stores, uploads, sandbox outputs and logs were inspected by path and tracking status only. Do not back them up as part of this checkpoint. Existing tracked `.gitkeep` files remain untouched.
- `git ls-files -ci --exclude-standard` returned **208 already-tracked files, all under `eval/results/`**. They are historical generated benchmark and evaluation reports at HEAD, with zero candidate changes. A limited signature scan of their current contents found no strong credential hits. The ignore rules exclude matching new output and leave tracked history visible to recipients. Include this directory in the separate remote-publication privacy review. If that review confirms sensitive data, identify the exact path and material type privately, have the owner rotate or revoke credentials where applicable, and obtain approval for any index or history remediation.
- Thirteen candidate reports contain machine-specific paths, including operator workspace or executable locations and traceback paths. Review them and recorded process observations before including the documentation group in the approved destination. Retain gate status, commands, scope and evidence counts. Substitute repository-relative or parameterized paths where privacy requires it. This inspection made no edits to the existing reports.
- `.gitignore` already excludes `.env`, the runtime families above, model-adjacent outputs in the reviewed local directories, dependency environments, caches, build output and VM images. `frontend/.gitignore` covers `*.local`. Read-only probes found a gap for root `.env.local`, root `.env.production` and `frontend/.env.production`. No matching local env file was present in the checked app roots. Proposed follow-up, if approved, is scoped `/.env.*` and `/frontend/.env.*` rules with `!/.env.example` and `!/frontend/.env.example` exceptions. No ignore edit was required for this explicit allowlist.

## Consistency checks

Changed Python files parsed without import execution. Static Python and frontend relative-path checks found no missing repository modules. The new `context.py`, `csv_file.py` and `text_file.py` must travel with chat, memory, multipart limits and their tests. The frontend capability, multipart and streaming contracts depend on the backend changes and share regression fixtures. The logout test uses the existing offline test harness. Manifests and lockfiles have no candidate changes. No debugger hook or checkpoint-specific authorization bypass was identified in the reviewed candidates. The existing `README.md` Quick Start predates the gated runtime handoff. The disabled launcher and R8.9 decision control the supported acceptance path. Historical proposed commands, mock evidence, and the Antigravity diagnostic refusal do not establish native or live success.

## Proposed commit allowlists

**Group 1 — `Consolidate gated local workbench workflows and offline regressions`**. One 55-file implementation checkpoint keeps shared route, planner, provider, readiness, frontend and regression changes together. Splitting individual features into separate versions of overlapping files would lose the currently verified combination. Paths below are repository-relative and exhaustive.

Application source and safe configuration:

```text
.env.example
backend/agent/engine.py
backend/agent/memory.py
backend/agent/numeric_receipts.py
backend/api/chat.py
backend/auth/dependencies.py
backend/auth/routes.py
backend/health/routes.py
backend/main.py
backend/models/ollama_provider.py
backend/multimodal/context.py
backend/multimodal/csv_file.py
backend/multimodal/grounding.py
backend/multimodal/image_processor.py
backend/multimodal/service.py
backend/multimodal/spreadsheet.py
backend/multimodal/text_file.py
backend/security/request_limits.py
frontend/src/api/capabilities.ts
frontend/src/api/chat.ts
frontend/src/api/client.ts
frontend/src/api/health.ts
frontend/src/components/auth/RolePortalView.tsx
frontend/src/components/chat/ChatHistorySidebar.tsx
frontend/src/components/chat/ChatInput.tsx
frontend/src/components/chat/ChatView.tsx
frontend/src/components/chat/MessageItem.tsx
frontend/src/components/models/ModelScanner.tsx
frontend/src/components/settings/SettingsView.tsx
frontend/src/context/WorkbenchContext.tsx
frontend/src/types/index.ts
frontend/vite.config.ts
```

Regression tests and fixtures in the same commit:

```text
frontend/tests/a1-capability-render.test.cjs
frontend/tests/a2-stream-client.test.cjs
frontend/tests/a22-chatview-handlers.test.cjs
frontend/tests/a41-history-render.test.cjs
frontend/tests/a41-history-sidebar.test.cjs
frontend/tests/a61-chat-attachment.test.cjs
frontend/tests/r74-dev-proxy.test.cjs
tests/backend/test_a22_async_offline.py
tests/backend/test_a2_provider_offline.py
tests/backend/test_a33_production_tools_offline.py
tests/backend/test_a61_multimodal_offline.py
tests/backend/test_i111_followup_boundary.py
tests/backend/test_i112_numeric_receipts.py
tests/backend/test_i6_workflow_offline.py
tests/backend/test_i7_model_reliability.py
tests/backend/test_multimodal.py
tests/backend/test_r12_readiness_offline.py
tests/backend/test_r51_core_slice.py
tests/backend/test_r83_text_file_offline.py
tests/backend/test_r86_csv_preview_offline.py
tests/backend/test_r87_attachment_transitions_offline.py
tests/backend/test_r88_session_logout_offline.py
tests/fixtures/i7/model_outputs.json
```

**Group 2 — `Record offline verification and outstanding release gates`**. These 15 reports are a distinct documentation checkpoint. Obtain the report privacy decision before approving this group. Group 1 can proceed on its own if the user approves it first.

```text
RELEASE_R7_2_FRONTEND_VERIFICATION_REPORT.md
RELEASE_R7_3_OLLAMA_INTEGRATION_REPORT.md
RELEASE_R7_4_WORKBENCH_INTEGRATION_REPORT.md
RELEASE_R7_5_CHAT_FIRST_ACCEPTANCE_REPORT.md
RELEASE_R7_6_CORE_WORKBENCH_COMPLETION_REPORT.md
RELEASE_R8_1_MULTIMODAL_VISION_REPORT.md
RELEASE_R8_2_MULTIMODAL_LIFECYCLE_REPORT.md
RELEASE_R8_3_TEXT_PREVIEW_REPORT.md
RELEASE_R8_4_DOCUMENT_PREVIEW_REPORT.md
RELEASE_R8_5_LOCAL_RUNTIME_ACCEPTANCE_REPORT.md
RELEASE_R8_6_CSV_PREVIEW_REPORT.md
RELEASE_R8_7_CROSS_FEATURE_INTEGRATION_REPORT.md
RELEASE_R8_8_LOCAL_RELEASE_CANDIDATE_REPORT.md
RELEASE_R8_9_WINDOWS_CONTAINMENT_GATE_REPORT.md
RELEASE_R8_10_REPOSITORY_CHECKPOINT_REPORT.md
```

## Approval and recovery procedure

1. Confirm Codex and Antigravity are taking turns. Recheck `git branch --show-current`, `git rev-parse HEAD`, `git --no-optional-locks status --short --branch`, and `git diff --cached --stat`. Compare against this report. Stop on unexpected changes or staged content and reconcile with the owner before changing the index.
2. Obtain explicit user approval to stage Group 1 on the current branch, and separately resolve any privacy redactions and obtain approval for Group 2. Stage only literal allowlisted paths for the approved group. Compare `git diff --cached --name-status` and `git diff --cached --stat` against that allowlist and run `git diff --cached --check`. Review the staged diff privately, including content and ignore status. Never stage excluded runtime paths as a recovery shortcut.
3. Compare staged code against the verified baseline. Run relevant approved offline checks before committing if code content differs or staged-only verification is needed. Use the R8.8 suite for application changes and the R8.9 suite only for lifecycle changes. Record commands and exit status. Obtain user approval to commit the reviewed index. Verify each resulting SHA with `git show --stat <sha>` and confirm that remaining local changes are the expected ones.
4. If validation or commit fails, preserve the index and worktree. Inspect `git status` and staged checks, then review a targeted correction with the owner. If a local checkpoint already exists, record its SHA and use an approved forward correction. For recovery later, first confirm the SHA is available and inspect it read-only. Reconstruct it in a separately approved, access-controlled recovery checkout while preserving the current checkout and keeping runtime state outside the Git recovery. A verified local SHA provides local recovery only while its repository remains available.
5. Remote publication requires **separate user approval** for the exact remote, destination branch, commit SHAs, report privacy and visibility. Check remote ancestry only after approval for any required remote access. Review the reachable tracked content, including historical evaluation artifacts. Verify local/remote parity after an authorized push. No checkpoint, backup archive or remote publication was created in R8.10.

## Verification evidence and remaining gates

| Scope | Evidence |
|---|---|
| R8.8 independently verified offline | Antigravity recorded both TypeScript checks passing, 99 frontend and 289 broad backend passes. [R8.8 appendix](RELEASE_R8_8_LOCAL_RELEASE_CANDIDATE_REPORT.md#antigravity-independent-verification--r88) |
| R8.9 independently verified refusal and offline lifecycle | Antigravity recorded 88 offline passes and the fixed entry-point refusal with exit 3. No controller, native workload or live process test ran. [R8.9 appendix](RELEASE_R8_9_WINDOWS_CONTAINMENT_GATE_REPORT.md#antigravity-independent-verification--r89) |
| R8.10 checked | Inventory, redacted static scan, import reference and ignore checks and `git diff --check`. No runtime tests rerun because product code did not change. |

R5.5 authenticated observer handoff remains unimplemented pending independent design review. Native Windows containment and live browser acceptance remain blocked. Real text and vision inference through the workbench remain unverified. R6.4 durable provenance-key protection remains unresolved. Protected document ingestion, RAG and artifact publication stay gated. Production remains NO-GO.

**Next action** — Review and explicitly approve the Group 1 local checkpoint allowlist, and record the separate report privacy decision before including Group 2.
