# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-31T14:53:28Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 8.81s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0020` |
| **Health_Ready_Latency_Mean_ms** | `119.0370` |
| **Task_Creation_Latency_Mean_ms** | `25.1450` |
| **Tool_Execution_Latency_Mean_ms** | `0.1190` |
| **Model_Inference_Latency_Mean_ms** | `0.0000` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.002ms, P95=0.005ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 119.0 | Mean=119.037ms, P95=357.071ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 25.1 | Mean=25.145ms, P95=29.851ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.1 | Mean=0.119ms, P95=0.211ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 0.0 | Mean=0.0ms, P95=0.0ms |
