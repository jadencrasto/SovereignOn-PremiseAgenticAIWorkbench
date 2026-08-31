# Evaluation Report: Tool Execution, Authorization & Latency Benchmark
**Timestamp:** 2026-08-31T15:55:31Z UTC  
**Environment:** Air-Gapped Sandbox Environment  
**Total Duration:** 0.09s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `7` |
| **Passed** | `7` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Tools_Evaluated** | `5` |
| **Passed_Tool_Operations** | `7` |
| **Average_Tool_Latency_ms** | `8.8600` |
| **Success_Rate_Percent** | `100.0000` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `TOOL-01` | Calculator: Complex Arithmetic | execution | **PASS** | 0.0 | Result=585.0 |
| `TOOL-02` | Calculator: Division by Zero Safety | error_handling | **PASS** | 0.0 | Clean error returned: Division by zero. |
| `TOOL-03` | File List: Sandbox Listing | execution | **PASS** | 15.0 | Listed 1 items in sandbox root |
| `TOOL-04` | File Read: Sandbox File Access | execution | **PASS** | 16.0 | Read 32 bytes |
| `TOOL-05` | File Write: Atomic Sandbox Write | execution | **PASS** | 16.0 | Atomic write committed in sandbox workspace |
| `TOOL-06` | Document Search: Semantic Search | execution | **PASS** | 15.0 | Retrieved 1 chunks |
| `TOOL-07` | RBAC: Viewer Write Rejection | authorization | **PASS** | 0.0 | Viewer role blocked at ToolRegistry boundary |
