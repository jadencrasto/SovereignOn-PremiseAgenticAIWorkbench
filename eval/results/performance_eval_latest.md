# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-31T15:55:40Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 8.91s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0030` |
| **Health_Ready_Latency_Mean_ms** | `118.6280` |
| **Task_Creation_Latency_Mean_ms** | `22.4730` |
| **Tool_Execution_Latency_Mean_ms** | `0.1260` |
| **Model_Inference_Latency_Mean_ms** | `0.0000` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.003ms, P95=0.007ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 118.6 | Mean=118.628ms, P95=355.845ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 22.5 | Mean=22.473ms, P95=25.004ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.1 | Mean=0.126ms, P95=0.221ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 0.0 | Mean=0.0ms, P95=0.0ms |
