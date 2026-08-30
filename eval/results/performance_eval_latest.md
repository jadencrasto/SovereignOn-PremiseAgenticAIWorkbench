# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T17:20:32Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 15.20s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0010` |
| **Health_Ready_Latency_Mean_ms** | `83.1710` |
| **Task_Creation_Latency_Mean_ms** | `126.6100` |
| **Tool_Execution_Latency_Mean_ms** | `0.0170` |
| **Model_Inference_Latency_Mean_ms** | `3440.0380` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.001ms, P95=0.004ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 83.2 | Mean=83.171ms, P95=831.687ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 126.6 | Mean=126.61ms, P95=210.919ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.017ms, P95=0.066ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 3440.0 | Mean=3440.038ms, P95=3496.388ms |
