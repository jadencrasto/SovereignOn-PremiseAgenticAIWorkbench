# Offline egress monitoring foundation verification

**Partial implementation; native coverage UNKNOWN; application execution remains blocked; production NO-GO.**

Changed: `tools/offline_verification/guest_wfp_monitor.ps1`, `wfp_event_parser.ps1` (new), `test_wfp_parser.ps1` (new), `egress_monitor_core.py`, `test_egress_monitor_core.py`, `network_sensitive_test_runner.ps1` (new), `OFFLINE_EGRESS_MONITOR_IMPLEMENTATION_REPORT.md`, `NETWORK_SENSITIVE_TEST_GATE_MATRIX.md`, and this report.

The actual guest collector calls the pure 5157 parser and prints bounded sanitized JSON. Invalid/duplicate/missing fields fail the capture; 5157 is categorized WFP-blocked, never successful. Source/destination, process IDs and paths are redacted. The parser and injected restoration decision were exercised offline. The synthetic Python collector builds canonical in-memory evidence and hash marked SIMULATED with UNKNOWN coverage; it does not prove a real native event. The new runner denies unconditionally and provides no application-test dispatch. Direct execution of other test commands is outside that runner's control.

| Check | Result |
| --- | --- |
| `python -I -B tools/offline_verification/test_egress_monitor_core.py` | 7 passed, 0 failed, 0 skipped; synthetic parser, collection, bad inputs, approval/gate denial, calibration limitation and export refusal. |
| `pwsh -NoProfile -File tools/offline_verification/test_wfp_parser.ps1` | 4 passed, 0 failed; actual shared parser and injected restoration success, conflict and failure. |
| PowerShell parser for guest, parser and tests | 3 passed, 0 failed. |
| `pwsh -NoProfile -File tools/offline_verification/network_sensitive_test_runner.ps1` | Expected denial, exit 1; no tests launched. |

No real guest collector, Windows Security log, `auditpol`, network operation, host security setting, application, VM or service was executed. Offline mocks do not prove native restoration, absence of event loss, process identity, OS isolation, process termination handling or endpoint coverage. Existing guest audit original state is held only in memory; abrupt termination may prevent restoration and requires approved snapshot rollback.

Safe durable export is deliberately unsupported: no Windows ACL or reparse-point guarantee can be established from these unit tests and there is no atomic writer or partial-write recovery. Path-attack and write-failure tests assert unconditional refusal rather than successful filesystem semantics. Process-level independent identity, observer health, native calibration, no-route-before-WFP visibility and a trusted protected test-runner integration remain unresolved. The five monitored conditions cannot be proven by caller-provided booleans. Before real tests: independent VM isolation and default-deny evidence; reviewed guest audit and snapshot changes; verified native collection/restoration and safe path/ACL output; path-specific real calibration with no external communication; approval for exact test, monitor and retention; and separately reviewed test-runner enforcement. Preserve logs before rollback and never claim absence of events proves absence of attempts.
