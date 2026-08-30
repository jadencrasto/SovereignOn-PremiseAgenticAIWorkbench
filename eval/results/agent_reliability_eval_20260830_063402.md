# Evaluation Report: Agent Reliability & Fault Injection Benchmark
**Timestamp:** 2026-08-30T06:34:02Z UTC  
**Environment:** Air-Gapped In-Memory & SQLite WAL  
**Total Duration:** 0.23s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `7` |
| **Passed** | `5` |
| **Failed** | `2` |
| **Environment Unavailable** | `0` |
| **Passed_Scenarios** | `5` |
| **Total_Scenarios** | `7` |
| **Reliability_Score_Percent** | `71.4000` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `REL-01` | Model Unavailable Resilience | fault_injection | **FAIL** | 14.1 |  |
| `REL-02` | Tool Exception Safety | error_handling | **PASS** | 0.3 | Tool exception caught cleanly and converted to structured ToolResult failure |
| `REL-03` | Malformed Argument Rejection | input_validation | **PASS** | 0.4 | Pydantic validation rejected malformed tool input without crashing |
| `REL-04` | Approval Rejection Handling | human_in_the_loop | **PASS** | 77.3 | Approval rejection safely transitions step to skipped and task to cancelled |
| `REL-05` | Task Cancellation Verification | lifecycle_control | **FAIL** | 25.5 |  |
| `REL-06` | Server Restart Crash Recovery | fault_recovery | **PASS** | 36.4 | In-flight executing task recovered to FAILED_INTERRUPTED on restart |
| `REL-07` | Approval Drift Rejection | security_binding | **PASS** | 42.3 | Approved step rejected when underlying tool was disabled post-approval |
