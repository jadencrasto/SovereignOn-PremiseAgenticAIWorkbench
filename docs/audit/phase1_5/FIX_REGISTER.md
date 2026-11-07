# Fix register

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
