# Phase 2 handoff (proposed, not implemented)

1. Obtain the authoritative SIH26117 statement and map every explicit requirement to code and acceptance evidence. Decide retention of the provisional items.
2. Fix session ownership and migration (ISS-001/011); acceptance: two authenticated users cannot list, use, alter or delete each other's sessions, including restart, guessed IDs and concurrent writes. Define administrator exceptions explicitly.
3. Harden deployment defaults and sandbox (ISS-002); acceptance: externally bound configurations cannot silently run unauthenticated, production execution uses verified isolation, and negative/config tests run offline. User decision: target deployment topology and approved production threat model.
4. Enforce multimodal tool disable (ISS-005); acceptance: injected tool calls never dispatch with flag off and work only when authorized and approval-gated when on.
5. Bundle fonts and verify browser network behavior (ISS-003), correct benchmark incomplete/air-gap statuses (ISS-004), guard upload size while streaming (ISS-006), propagate verified clearance (ISS-007).
6. Re-establish an isolated, pinned Windows baseline with complete pytest, typecheck/lint/build, safe evaluations, live local models and an offline test harness. Add regression tests for all above, plus restart/approval/ownership and citation correctness. Do not infer quality from historical scores.

Future phases: RAG ground-truth dataset/metrics and retrieval calibration, multimodal/OCR, model benchmarking and customization (with data and hardware decisions), then enterprise operations and deployment. Obtain approval for external downloads, installing dependencies, model pulls, changes to user data, or any deployment. Actual phase boundaries should be reconciled with the project roadmap.
