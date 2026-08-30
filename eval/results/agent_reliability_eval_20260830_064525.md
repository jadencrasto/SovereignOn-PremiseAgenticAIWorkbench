# Evaluation Report: Agent Reliability & Fault Injection Benchmark
**Timestamp:** 2026-08-30T06:45:25Z UTC  
**Environment:** Air-Gapped In-Memory & SQLite WAL  
**Total Duration:** 0.26s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `7` |
| **Passed** | `7` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Passed_Scenarios** | `7` |
| **Total_Scenarios** | `7` |
| **Reliability_Score_Percent** | `100.0000` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `REL-01` | Model Unavailable Resilience | fault_injection | **PASS** | 22.2 | Task transitioned safely to FAILED with descriptive connection error |
| `REL-02` | Tool Exception Safety | error_handling | **PASS** | 0.4 | Tool exception caught cleanly and converted to structured ToolResult failure |
| `REL-03` | Malformed Argument Rejection | input_validation | **PASS** | 2.1 | Pydantic validation rejected malformed tool input without crashing |
| `REL-04` | Approval Rejection Handling | human_in_the_loop | **PASS** | 85.1 | Approval rejection safely transitions step to skipped and task to cancelled |
| `REL-05` | Task Cancellation Verification | lifecycle_control | **PASS** | 27.9 | Running task cancelled immediately and flagged as cancelled |
| `REL-06` | Server Restart Crash Recovery | fault_recovery | **PASS** | 30.4 | In-flight executing task recovered to FAILED_INTERRUPTED on restart |
| `REL-07` | Approval Drift Rejection | security_binding | **PASS** | 43.9 | Approved step rejected when underlying tool was disabled post-approval |
