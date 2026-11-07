# Fix register

## Security implementation continuation, 2026-11-07

- Conversation ownership: request-bound server identity is persisted for newly created sessions. Authenticated users cannot list or access another owner's session. Legacy list-format records remain unassigned in authenticated mode; the local synthetic principal can claim an individual legacy conversation in local mode. Atomic JSON updates are serialized across cooperating processes via a one-byte Windows lock (`msvcrt.locking`) or POSIX `flock`, plus the existing in-process lock. The lock only coordinates versions/processes that honor the same sidecar lock; network filesystems and mixed old/new versions have not been verified. A failed write may leave in-memory state changed until the next reload; the on-disk file remains intact.
- Retrieval: server-derived clearance is carried in request context and caps agent and document-search tool retrieval; plain chat retains its explicit effective clearance. Direct API and task-resume paths require broader integration validation, particularly where administrator approval of another user's task intersects conversation ownership. Local tests use mock model/retrieval providers.
- Tools: dispatcher checks an explicit flag and request context, role, exact approval binding, and consumes approved actions once using an atomic SQLite update before execution. Failed or interrupted actions need a new approval; cancellation/consume races and full workflow recovery require additional testing.
- Multipart uploads: ASGI middleware bounds `/api/documents` and `/api/chat/multimodal` multipart bodies before the framework parser. It permits configured file size plus 1 MiB multipart overhead and spools beyond 1 MiB to a temporary file. Direct arbitrary ASGI deployments or reverse proxies still require independent ingress, concurrency and timeout limits. The new `python -m backend` launcher uses validated configured host and port; direct `uvicorn` invocations can still override binding and must be controlled by deployment configuration.

These are targeted implementations with synthetic regression coverage, not verified production or air-gap guarantees. Earlier entries describe prior checkpoints.

## Telemetry investigation, 2026-11-07

Egress finding OPEN: exact rejected destination and module are unavailable. `backend/rag/store.py` now explicitly requests disabled Chroma product and OTLP telemetry before its import and disables default collection embedding functions; 3 mocked configuration tests passed. Installed Chroma native behavior and real-client startup remain unverified. Full suite is blocked pending demonstrated OS isolation; see TELEMETRY_AND_EGRESS_AUDIT.md.

## Continuation, 2026-11-07

Checkpoint is branch `phase1-5-recovery-security`, commit `5dc673f55a02e1c689af62c1bc4c7fa6625b9383`; subsequent changes are uncommitted. Earlier table records the original checkpoint.

- ISS-011 PARTIAL: JSON persistence uses same-directory temporary files, fsync and atomic replacement; an RLock serializes operations in one process and invalid JSON fails closed. Legacy file format is preserved. Restart, thread concurrency and injected replace failure tested. No cross-process serialization, ownership, migration, or rollback of already-mutated in-memory state after a failed save is implemented. Existing legacy conversations are not assigned to anyone.
- ISS-007 PARTIAL: authenticated server-derived effective clearance is now passed to plain and streaming text chat; regression tests check role and clearance combinations. Planned, multimodal and tool-based paths still use roles in places and remain open.
- ISS-005 PARTIAL: registry now rejects missing role and requires a verified task/step/argument-bound approval for tools marked as requiring approval; approved task resume supplies approval ID. Synthetic tests include a real SQLite approval manager. Registry-level per-request tools-disabled capability, replay protection, task/session ownership and complete workflow integration still need validation. Existing direct tool consumers must pass a verified role.
- Auth-disabled local mode no longer honors role simulation headers unless the existing explicit development simulation setting is enabled. This is not a multi-user security boundary and does not replace authentication or session ownership.

| ID | Change | Verification | Remaining |
|---|---|---|---|
| ISS-002 | `backend/config.py`, `.env.example`: loopback default; validator requires auth for non-loopback, rejects production auth override. | Synchronous validator tests passed. | Actual server command can independently bind a host; production deployment, reverse proxy and ingress configuration remain unverified. |
| ISS-003 | Removed Google font references in HTML/CSS; system fallbacks retained. | Source regression passed; Vite build passed. | Browser request capture and full dependency egress audit not performed. |
| ISS-004 | Report FAIL/INCOMPLETE/BLOCKED, count expected suites, avoid unconditional isolation claims, nonzero runner exit for non-PASS. | PASS/FAIL/INCOMPLETE/BLOCKED and missing suite synchronous tests passed. | Live benchmark acceptance and per-case mandatory/optional policy remain to be defined. |
| ISS-005 | Propagate tools flag into multimodal engine and guard registry dispatch in that path. | Regression tests added but async execution blocked. | Engine dispatch guard is not a registry-level capability enforcement; enabled-path approvals and text path need independent verification. |
| ISS-006 | Document handler reads bounded chunks and rejects > configured size with 413; closes upload. | Async regression added but blocked. | Framework multipart parsing may spool request before handler; request-body/ingress limits, exact boundary, malformed and interruption tests outstanding. |
| ISS-012 | Replaced misleading verified/live claims for static graph display. | Source inspection, lint/build. | Audit all synthetic surfaces and visually verify browser. |
| ISS-001/011 | No change | Not verified. | Owner-aware migration, atomic persistence, concurrency and ownership enforcement. |
| ISS-007 | No change | Not verified. | Propagate authenticated clearance across all paths. |

Do not assume partial fixes close the associated issues until the remaining verification and integration tests pass.
