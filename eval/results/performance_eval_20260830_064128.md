# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T06:41:28Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 12.95s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0010` |
| **Health_Ready_Latency_Mean_ms** | `0.0050` |
| **Task_Creation_Latency_Mean_ms** | `5.6580` |
| **Tool_Execution_Latency_Mean_ms** | `0.0350` |
| **Model_Inference_Latency_Mean_ms** | `3538.9240` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.001ms, P95=0.002ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.005ms, P95=0.011ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 5.7 | Mean=5.658ms, P95=5.814ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.035ms, P95=0.068ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 3538.9 | Mean=3538.924ms, P95=3666.553ms |
