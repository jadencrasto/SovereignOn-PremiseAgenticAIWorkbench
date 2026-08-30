# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T06:58:21Z UTC  
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
| **Health_Ready_Latency_Mean_ms** | `60.1590` |
| **Task_Creation_Latency_Mean_ms** | `5.6670` |
| **Tool_Execution_Latency_Mean_ms** | `0.0250` |
| **Model_Inference_Latency_Mean_ms** | `3477.2260` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.001ms, P95=0.003ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 60.2 | Mean=60.159ms, P95=601.56ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 5.7 | Mean=5.667ms, P95=7.406ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.025ms, P95=0.092ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 3477.2 | Mean=3477.226ms, P95=3521.288ms |
