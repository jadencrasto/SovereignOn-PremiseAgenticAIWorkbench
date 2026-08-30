# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T06:47:44Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 13.71s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0010` |
| **Health_Ready_Latency_Mean_ms** | `60.3810` |
| **Task_Creation_Latency_Mean_ms** | `5.5930` |
| **Tool_Execution_Latency_Mean_ms** | `0.0190` |
| **Model_Inference_Latency_Mean_ms** | `3567.3600` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.001ms, P95=0.003ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 60.4 | Mean=60.381ms, P95=603.781ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 5.6 | Mean=5.593ms, P95=5.88ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.019ms, P95=0.081ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 3567.4 | Mean=3567.36ms, P95=3645.613ms |
