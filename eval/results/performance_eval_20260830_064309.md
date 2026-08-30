# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T06:43:09Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 13.46s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0010` |
| **Health_Ready_Latency_Mean_ms** | `58.3660` |
| **Task_Creation_Latency_Mean_ms** | `6.2530` |
| **Tool_Execution_Latency_Mean_ms** | `0.0180` |
| **Model_Inference_Latency_Mean_ms** | `3490.4080` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.001ms, P95=0.003ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 58.4 | Mean=58.366ms, P95=583.635ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 6.3 | Mean=6.253ms, P95=6.797ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.018ms, P95=0.069ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 3490.4 | Mean=3490.408ms, P95=3568.224ms |
