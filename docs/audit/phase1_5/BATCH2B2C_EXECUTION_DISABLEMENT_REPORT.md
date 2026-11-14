# Batch 2B.2C — code execution disabled / release NO-GO

Date: 2026-11-08. Branch `phase1-5-recovery-security`, HEAD `beeca449e127e01cb53576ea02c05522855766bd`; all pre-existing uncommitted changes preserved. ACTIVE, authorized RAG, task approval/resume and artifact reads remain restricted. Phase 1.5.4 and Phase 2 remain NO-GO.

## Entrypoints and policy

`backend/security/execution_policy.py` defines one unconditional deny decision; there is no environment or configuration switch to enable generated code. Current enforcement:

| Boundary | Enforcement |
| --- | --- |
| Registration and exposure | `ToolRegistry.register` marks `code_execution` disabled and `enable` refuses it; enabled-tool listings exclude it. `_register_tools` now passes the active custom settings into its factory and no longer describes subprocess execution as a verified sandbox. |
| Registry dispatch | `ToolRegistry.execute` rejects `code_execution` before approval verification, role resolution or tool invocation, even if a caller mutates the enabled flag. |
| Agent normal/multimodal/planned/resumed dispatch | All five existing engine registry calls route through `_dispatch_tool`, which denies that tool before even an injected registry is called. Existing unverified task resume remains separately denied. |
| Direct tool factory and internal workers | `execute_code_execution`, `_run_subprocess_worker` and `_run_docker_worker` call the central guard before parsing or launching. Denial raises `PermissionError`. No Docker/subprocess fallback is enabled. |
| Startup and config validation | Startup marks the tool disabled without Docker daemon/image probes; validation reports code execution disabled in development and production, regardless of configured backend. This does not assert that an isolation backend exists. Other authentication and binding validation remains. |

This protects the discovered named code tool and workers in the cooperative application. It is not OS isolation: privileged callers, arbitrary in-process plugins that invoke Python/subprocess directly, alternate execution entrypoints not identified in the review, or a caller that alters application code can evade these Python controls. Other subprocess uses (such as Docker diagnostics if invoked directly and external dependencies) are not blanket-disabled. Do not claim protection against a compromised trusted application process.

Configuration behavior: both legacy `subprocess` and `docker`, global or custom Settings, are denied. A configured Docker daemon/image or an approval never grants eligibility. The legacy backend code remains for a future independently reviewed integration; no future activation switch was introduced. This pass does not establish security of that backend or make Docker/WSL2 available. `ConfigValidator` now accepts disabled execution rather than requiring Docker merely to start production; other configuration checks continue. A future code-enable design requires a separate reviewed change and independent host isolation evidence.

## Synthetic tests and compatibility

Before the fix, the new test file produced 7 failures: development subprocess reached its mocked worker, registry approval allowed a mocked execution function, direct workers attempted their mocked subprocess APIs, and registration remained enabled. Production subprocess and Docker cases raised existing `RuntimeError` instead of the new denial. **The initial pre-fix test unintentionally called the existing Docker availability probe for the Docker cases** (it reported Docker unavailable); the execution workers were mocked and no generated code was run. No image pull was requested. This local probe was not an OS-level isolation check; its network activity was not independently observed. Subsequent tests deny before probes.

An additional regression-first test initially failed because the new agent dispatch boundary did not exist. Added tests cover direct tool calls in both environments/backend modes, workers with mocked `Popen`/`run`, registry flag mutation, approval non-consumption, replay-shaped dispatch, agent dispatch with an injected registry, custom active settings, startup validator behavior, and calculator compatibility. They use synthetic code strings only and do not execute those strings.

Final Windows/Python targeted run with temporary pytest storage:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:PYTHONDONTWRITEBYTECODE='1'; python -c "import socket,pytest; original=socket.socket.connect; socket.socket.connect=lambda self,address: original(self,address) if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(OSError('Network disabled for test')); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests/backend/test_batch2b2c_execution_disablement.py','tests/backend/test_batch2b2a_content_boundary.py','tests/backend/test_batch2b2_retrieval_boundary.py','tests/backend/test_phase153g_registry.py','tests/backend/test_phase153g_stream_revocation.py','tests/backend/test_phase153c_task_security.py','tests/backend/test_phase153_stabilization.py','tests/backend/test_phase153h_artifact_integrity.py','tests/backend/test_approval.py','tests/backend/test_docx_and_execution.py::TestDocxCreate','tests/backend/test_phase153h7_presentation.py','-q','-p','no:cacheprovider','--tb=short']))"
# 78 passed, 2 skipped in 7.43s
```

Two skips require Windows symlink privileges. Selected regression guards include RAG/workspace denial, conversation revocation, artifact collision, L3-to-L1 task isolation, approval denial, calculator and DOCX creation. The wrapper is not OS-level egress isolation.

One pre-existing, mocked compatibility test was run separately without changing it:

```powershell
# Same Python socket wrapper and pytest flags as above, selecting:
tests/backend/test_code_isolation_step8.py::TestExitCodeClassification::test_control_c_treated_as_failure_with_description
# 1 failed: PermissionError because direct execution is intentionally disabled.
```

Other legacy tests expecting real code execution, successful task replay or enabled code-tool schemas were not run and are expected to need a security-aware redesign; no claim is made that they pass. No real model, browser, OS sandbox, production service, privileged setup or full-suite retry was run. Earlier full-suite retry remains blocked by automatic security review after unidentified outbound telemetry.

## Changes and operational decision

Changed `backend/security/execution_policy.py`, `backend/tools/registry.py`, `backend/tools/code_execution.py`, `backend/agent/engine.py`, `backend/main.py`, `backend/utils/config_validation.py`, `tests/backend/test_batch2b2c_execution_disablement.py` and this report. Existing arbitrary-code functionality is intentionally unavailable; calculator and tested DOCX creation remain available under their separate policies. No stage, commit, push, merge or deploy.

**Operational release NO-GO.** The tested code path is disabled, but document lifecycle, provenance and retrieval, safe parser isolation, legacy compatibility, Windows reparse behavior, external-egress/telemetry evidence and real integration remain unresolved. Batch 1 is PARTIAL; do not enable document ACTIVE, authorized retrieval, task approval/resume, artifact access or verifier dispatch based on these tests.
