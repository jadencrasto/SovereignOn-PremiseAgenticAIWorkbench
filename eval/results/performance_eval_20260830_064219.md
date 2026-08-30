# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T06:42:19Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 13.42s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0010` |
| **Health_Ready_Latency_Mean_ms** | `59.6290` |
| **Task_Creation_Latency_Mean_ms** | `6.2670` |
| **Tool_Execution_Latency_Mean_ms** | `0.0190` |
| **Model_Inference_Latency_Mean_ms** | `3475.6920` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.001ms, P95=0.006ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 59.6 | Mean=59.629ms, P95=596.266ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 6.3 | Mean=6.267ms, P95=6.769ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.019ms, P95=0.075ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 3475.7 | Mean=3475.692ms, P95=3660.714ms |
