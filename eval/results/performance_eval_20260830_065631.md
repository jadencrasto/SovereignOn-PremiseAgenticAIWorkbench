# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T06:56:31Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 23.55s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0010` |
| **Health_Ready_Latency_Mean_ms** | `0.0050` |
| **Task_Creation_Latency_Mean_ms** | `5.3780` |
| **Tool_Execution_Latency_Mean_ms** | `0.0310` |
| **Model_Inference_Latency_Mean_ms** | `7059.2520` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.001ms, P95=0.002ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.005ms, P95=0.01ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 5.4 | Mean=5.378ms, P95=5.736ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.031ms, P95=0.061ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 7059.3 | Mean=7059.252ms, P95=14159.737ms |
