# A8.1 Workbench UI integration report

**A8.1: PARTIAL. A8: PARTIAL. Production: NO-GO.** Mounted React, real browser, live application and production build verification remain incomplete.

## Inspection and changes

Reviewed the chat view and composer, streaming API client, history sidebar and requests, authentication unmount boundary, capability handling, model scanner, message/source presentation, artifact viewer and relevant backend capability/route tests. Consulted A1, A2 and A7 reports. This was a targeted review, not a complete audit of every component or API contract.

- Fixed `chatDisabled`: an installed alternate model could previously override a blocked or unavailable chat capability. A focused test covers blocked, unavailable, installed and missing alternatives. The composer now identifies a selected alternate model missing from a checked inventory.
- Removed an unconditional security/local-inference claim from the empty chat screen. Added accessible names to the composer and history search/title inputs, announced attachment errors, and constrained history drawer width on narrow screens. These changes have not been tested with assistive technology or a browser.
- When an action invalidates an in-flight history load, clear the obsolete loading indicator. History records remain server-authoritative. No ownership, provenance, retrieval, execution, artifact or approval gate was changed.

## Evidence and limits

The existing frontend client sends JSON or multipart chat requests and handles typed SSE events. Its tests exercise the actual stream parser with mocked fetch, including cancellation and terminal events. Simulated-hook tests exercise chat submission, progressive updates, cancellation, error and stale callback guards, session creation, workflow and approval states. Sidebar tests exercise server-confirmed rename/deletion and stale history requests in a simulated harness. These are not mounted React tests, a browser, or a full frontend-to-backend integration test. Cross-user safety relies in part on the existing authenticated `ChatView` unmount and server checks; a mounted account-switch test was not performed.

The existing message UI distinguishes planning, execution, approval, completed, failed, cancelled and interrupted states using events; blocked and uncertain outcomes are not comprehensively mapped or mounted-tested. Source cards suppress unverified source details, and document/artifact access is capability-gated in the application layout. The underlying artifact viewer has download controls and was not separately mounted or verified for a future change to capability states; publication remains blocked by existing backend controls. The image composer validates PNG/JPEG/WebP and size, removes invalid replacements, and is keyed by conversation; upload failure and switching were not mounted-tested. Example prompts still suggest workflows whose protected operations may be blocked. Keyboard selection of history rows, focus management, modal behavior, long messages and responsive layout need real interaction testing.

## Verification

From `frontend`:

`node --test tests/a61-chat-attachment.test.cjs tests/a52-source-render.test.cjs tests/a51-documents-render.test.cjs tests/a41-history-render.test.cjs tests/a41-history-sidebar.test.cjs tests/a22-chatview-handlers.test.cjs tests/a2-stream-client.test.cjs tests/a1-capability-render.test.cjs tests/historyRequests.test.mjs` — **42 passed, 0 failed**. After the selected-model feedback change: `node --test tests/a22-chatview-handlers.test.cjs tests/a1-capability-render.test.cjs` — **17 passed, 0 failed**. These tests use TypeScript transpilation, simulated hooks or server rendering, not a type check.

From repository root, PowerShell: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q tests/backend/test_a1_capabilities_offline.py tests/backend/test_a22_async_offline.py tests/backend/test_a41_history_offline.py` — **24 passed, 1 warning** (unknown `asyncio_mode` with plugin autoload disabled). These in-process route/contract tests are not connected to the React client in a single end-to-end test.

Local React and React DOM are installed, but no jsdom, happy-dom, React Testing Library, Playwright or react-test-renderer was found in the top-level local dependencies. No explicitly authorized network-isolated build environment was established. No packages were installed or downloaded. The previous automatic approval review rejected the npm build because registry access was outside authorized offline scope; no retry or alternative build/type-check command was attempted. **Type checking and production build remain unverified.**

## Next task

Provide an explicitly authorized network-isolated frontend verification environment with a locally provisioned DOM test runner, then mount real ChatView, ChatInput and history components with API-boundary mocks. Cover account and conversation switches, cancellations, approval and artifact restrictions, keyboard/focus behavior, and connect the client to an in-process synthetic backend where feasible. Separately authorize an offline TypeScript/Vite build. Until then these acceptance criteria are unmet; no browser, model, Chroma, launcher or production service was started, and nothing was committed or pushed.

# A8.2 update — offline verification and interaction reliability

**A8.2: PARTIAL. Overall A8: PARTIAL. Production: NO-GO.** No mounted React or browser integration evidence was obtained.

## Environment and authorization

Read the A8.1 report, frontend manifest and lockfile, locally installed dependency names, capability derivation, relevant chat events and artifact routes, and existing isolation documents. React and React DOM are both 19.2.8. The checked top-level `node_modules` and lockfile did not list jsdom, happy-dom, React Testing Library, Playwright, Puppeteer or react-test-renderer. No default local npm cache index or Playwright browser cache was found. Chrome and Edge executables exist; their presence does not establish an approved isolated testing environment. No packages were downloaded or installed and no browser was launched. No explicitly authorized network-isolated frontend test/build environment was identified. Prior automatic review rejected the npm build continuation due to potential registry access outside the authorized offline scope; it was not retried through another command. **TypeScript: NOT VERIFIED. Production build: NOT VERIFIED.**

A practical next step is an independently provisioned, network-isolated environment with reviewed, version-locked offline React-compatible DOM test dependencies and their transitive dependencies, an approved exact command, synthetic data and API-boundary mocks. Then mount ChatView, ChatInput, history, model selector and workflow UI; verify actual state transitions, account and session changes, keyboard behavior and restrictions. An isolated browser/application integration and independently authorized build remain separate work. No alternate executable is an approval substitute.

## Changes and observations

- ArtifactViewer previously trusted its parent gate and offered preview and direct download controls for any returned item; the current backend list contract has no verified publication state. The viewer now independently renders a blocked notice and does not start its polling effect while publication support is absent, even if the outer capability later changes. The old viewer remains dormant; it must not be re-enabled until a reviewed per-artifact authorization, verification and publication contract exists. Server-rendered output verifies that no preview/download buttons or artifact URLs appear in the blocked view. The backend remains authoritative; a client change does not secure a direct URL. Existing backend capability declares `artifact_access` blocked and backend routes independently deny unverified provenance.
- `task_completed` previously immediately displayed workflow completion before the SSE terminal event. The view now records that event and only displays completed after a terminal `done`, provided there is no pending approval; failure/cancellation supersedes the pending completion. This does not prove artifact publication or protect against an incorrect backend event. The existing simulated-hook harness checks the intermediate state and terminal transition; actual React mounting remains unverified.
- Attachment object URLs used in sent-message previews are now revoked when messages are removed or the view unmounts. Added keyboard focus and Enter/Space activation to history rows. The effect cleanup and keyboard behavior have not been exercised in a mounted DOM or with assistive technology; native confirmation focus restoration and full keyboard usability still need testing.
- Inspected server-issued session IDs, chat SSE dispatch, capability values and artifact authorization paths. Existing frontend mock-boundary tests and separate backend tests do not establish a connected client-to-FastAPI integration. Chat, model, approval and history behavior remains covered only by previous simulated hooks and API client tests; no unverified assistant content or production artifact access was enabled. Blocked/uncertain workflow presentation, account switching, responsive rendering, upload failures and stale attachment behavior need mounted verification.

## Exact verification

From `frontend`: `node --test tests/a82-artifact-presentation.test.cjs tests/a41-history-sidebar.test.cjs tests/a22-chatview-handlers.test.cjs tests/a1-capability-render.test.cjs tests/a2-stream-client.test.cjs tests/a61-chat-attachment.test.cjs` — **32 passed, 0 failed** after the final changes. The new artifact test server-renders the real component; other tests use the existing simulated hooks and mocked stream boundary. None is mounted, a browser test, or a type check.

From repository root: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q tests/backend/test_a1_capabilities_offline.py tests/backend/test_a41_history_offline.py tests/backend/test_offline_package_artifact_errors.py tests/backend/test_phase153h_public_artifact.py` — **interrupted, no final result** after 20 progress dots and an extended period without output; no test count or passing suite is claimed. The cause of the stall was not established. A8.1's separate 24-test backend result remains historical evidence, not a result of this run.

No launcher, Ollama, Chroma, VM, production document service, package installation, build, commit, merge or push was performed. Approval resume, document retrieval and artifact publication remain blocked. The recommended next action is to provision and approve the isolated test environment and exercise the real mounted components and permitted synthetic route adapter before reassessing A8.2.
