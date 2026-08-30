# Evaluation Report: Agent Reliability & Fault Injection Benchmark
**Timestamp:** 2026-08-30T06:41:15Z UTC  
**Environment:** Air-Gapped In-Memory & SQLite WAL  
**Total Duration:** 0.22s  

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
| `REL-01` | Model Unavailable Resilience | fault_injection | **PASS** | 20.4 | Task transitioned safely to FAILED with descriptive connection error |
| `REL-02` | Tool Exception Safety | error_handling | **PASS** | 0.3 | Tool exception caught cleanly and converted to structured ToolResult failure |
| `REL-03` | Malformed Argument Rejection | input_validation | **PASS** | 0.1 | Pydantic validation rejected malformed tool input without crashing |
| `REL-04` | Approval Rejection Handling | human_in_the_loop | **PASS** | 75.8 | Approval rejection safely transitions step to skipped and task to cancelled |
| `REL-05` | Task Cancellation Verification | lifecycle_control | **PASS** | 28.4 | Running task cancelled immediately and flagged as cancelled |
| `REL-06` | Server Restart Crash Recovery | fault_recovery | **PASS** | 27.9 | In-flight executing task recovered to FAILED_INTERRUPTED on restart |
| `REL-07` | Approval Drift Rejection | security_binding | **PASS** | 40.1 | Approved step rejected when underlying tool was disabled post-approval |
