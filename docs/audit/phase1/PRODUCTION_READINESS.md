# Production readiness

Not production-ready on available evidence. Deployment currently relies on local setup scripts and unpinned Python requirements; no tracked CI or deployment manifest was found. `README.md` and historical architecture descriptions require reconciliation with code. Release and rollback, signed offline dependency/model bundles, license review, backups, restore drills, database migrations and documented operator procedures remain unverified.

Security priorities: session ownership, safe binding/auth defaults, true isolation, role/document policy, local assets, verified egress controls, secrets and TLS. Actual firewall isolation, model telemetry and dependencies' outbound behavior have not been verified. SQLite and file-based JSON have concurrency/recovery limits that need load and interruption testing; logs and backups need access control and retention.

Performance: no reliable fresh startup, model initialization, first-token, tokens/sec, ingest throughput, retrieval, tool, concurrent request or GPU/memory metrics obtained. Node frontend type check succeeded, but production bundle measurement was not executed. No meaningful load test was run. Instrument p50/p95/p99 per workflow, trace IDs, resource use, timeouts, queue and cancellation behavior, and error/approval rates before sizing hardware. Local model availability was blocked by loopback restrictions; do not infer hardware capacity from model configuration.

Operational readiness requires safe health/readiness checks, monitoring, alerts, retention, documented incident response, training and multi-user acceptance testing. Static synthetic graph data should be labeled as such.
