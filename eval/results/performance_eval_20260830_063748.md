# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T06:37:48Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 24.80s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0010` |
| **Health_Ready_Latency_Mean_ms** | `205.1730` |
| **Task_Creation_Latency_Mean_ms** | `8.0750` |
| **Tool_Execution_Latency_Mean_ms** | `0.0440` |
| **Model_Inference_Latency_Mean_ms** | `7279.4790` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.001ms, P95=0.002ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 205.2 | Mean=205.173ms, P95=615.505ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 8.1 | Mean=8.075ms, P95=13.619ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.044ms, P95=0.085ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 7279.5 | Mean=7279.479ms, P95=14721.605ms |
