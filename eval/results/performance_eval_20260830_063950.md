# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T06:39:50Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 13.74s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0020` |
| **Health_Ready_Latency_Mean_ms** | `230.5780` |
| **Task_Creation_Latency_Mean_ms** | `5.7110` |
| **Tool_Execution_Latency_Mean_ms** | `0.0450` |
| **Model_Inference_Latency_Mean_ms** | `3560.0950` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.002ms, P95=0.003ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 230.6 | Mean=230.578ms, P95=691.72ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 5.7 | Mean=5.711ms, P95=5.852ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.045ms, P95=0.087ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 3560.1 | Mean=3560.095ms, P95=3638.949ms |
