# Phase 1.5.3F deterministic task report

Date: 2026-11-07. Branch: `phase1-5-recovery-security`. Baseline HEAD: `beeca449e127e01cb53576ea02c05522855766bd`. The working tree already contained extensive uncommitted Phase 1.5.3B–E changes; these were preserved. **PARTIAL; Phase 1.5.4 and Phase 2 NO-GO.**

## Implemented

Added `backend/security/deterministic_task_policy.py`, a standalone, non-authorizing arithmetic primitive. It accepts exactly two bounded Python integers (not booleans, strings or floats), performs integer addition without I/O, and produces a canonical operation record and SHA-256 digest. The immutable request and detached canonical record reduce accidental mutation. The digest is not authentication, verification evidence, or a provenance grant and can be recomputed by anyone who can modify the record.

No route invokes the primitive. There is no verified task creation, verified task read, provenance migration, approval issuance/consumption, durable execution, or recovery. No existing policy or schema was changed, and legacy and unverified tasks remain fail-closed. No artifact behavior was changed. This primitive is preparatory work, not a verified lifecycle.

## Why the lifecycle remains disabled

The existing SQLite `task_security_provenance` schema allows only the quarantine state and lacks a verified evidence model. The general task and approval managers use independent operations and connections. Approval transitions and task execution are not one atomic operation, and conversation ownership is stored under a separate JSON file lock. A trustworthy implementation must define a consistent lock order, bind and revalidate task, owner, session, clearance, plan, arguments and approval inside an appropriate transaction, handle concurrent cancellation and changes, and ensure that all legacy and general-agent paths remain denied. No reviewed additive migration or complete transactional integration has been implemented here. The pure hash does not address these missing guarantees. It would be unsafe to mark a task verified or resume one on this basis.

Proposed next step: design an additive task-provenance schema and a single atomic transaction for a fixed operation and its approval, with cooperative conversation locking held across ownership validation and the transaction. Review lock ordering and recovery on all failure paths before enabling a route. This is a proposal, not an implemented or verified design. Back up the task database, including WAL state, before any future migration; no runtime database migration or backup occurred in this pass.

## Tests

Environment: Windows, Python 3.13, installed pytest; synthetic inputs and temporary pytest stores. No model, browser, or external service was used.

First command, run in the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest tests/backend/test_phase153f_deterministic_policy.py -q -p no:cacheprovider --tb=short
```

**14 passed, 0 failed, 0 skipped; 1 configuration warning; 0.30 s.** These are pure unit tests only, not lifecycle, API, authorization or isolation tests.

Second command used a temporary `sitecustomize.py` that replaced Python `socket.socket.connect`, `connect_ex`, and `socket.create_connection` with functions raising `OSError`; `PYTHONPATH` included that temporary directory and the repository. The same two environment flags were set. No host firewall settings were changed. This guard is not OS-level isolation.

```powershell
python -m pytest -p pytest_asyncio.plugin tests/backend/test_phase153f_deterministic_policy.py tests/backend/test_phase153e_lifecycle.py tests/backend/test_phase153d_provenance.py tests/backend/test_phase153c_task_security.py tests/backend/test_phase153_stabilization.py tests/backend/test_phase15_registry_continuation.py tests/backend/test_approval.py tests/backend/test_tasks.py tests/backend/test_task_recovery.py -q -p no:cacheprovider --tb=short
```

**49 passed, 4 failed, 11 errors, 1 xfailed, 0 skipped; 5.57 s.** The guard blocked the loopback socket pair used to initialize the Windows asyncio event loop. The four failing route tests and eleven async setup errors therefore did not complete their intended assertions. This run does not establish an application defect or a passing task-disclosure guard. The unchanged xfail is the known artifact filename collision. The Phase 1.5.3E report records a prior successful downgraded-user guard; that historical result is not a fresh pass here. The previously rejected full suite was not retried. No compatibility tests were rerun in this pass; the earlier 4-pass/2-fail result remains historical.

The same targeted command was subsequently run with a different temporary `sitecustomize.py`: `socket.socket.connect` and `connect_ex` allow only literal loopback IP addresses and raise `OSError` for other destinations. This permits the local socket pair used by Windows asyncio. The rest of the command and environment flags were unchanged. **64 passed, 0 failed, 0 skipped, 1 xfailed; 4.38 s.** This includes the original L3-to-L1 task-detail guard, unchanged artifact-collision xfail, and existing approval and unverified-task guards. The Python-level guard does not establish OS-level isolation and does not demonstrate live services or a verified lifecycle.

## Acceptance and remaining limitations

- **NOT IMPLEMENTED:** server-authorized creation, server-generated verified provenance, migration, authorized task detail, single-use lifecycle approval, durable execution, restart/recovery, mutation rejection at the task boundary and all corresponding positive end-to-end tests.
- **PASS in the loopback-enabled targeted run:** the existing L3-to-L1 route guard and applicable async approval/unverified-resume guards. The original assertions were not weakened. The known artifact collision remains expected-failing.
- **BLOCKED:** the previously rejected full suite and formal air-gap verification; no OS-level network isolation was demonstrated. Live-model and browser behavior were not tested.

Files added in this pass: `backend/security/deterministic_task_policy.py`, `tests/backend/test_phase153f_deterministic_policy.py`, and this report. No commit, push, merge or deployment was performed.
