# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T06:44:37Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 13.57s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0010` |
| **Health_Ready_Latency_Mean_ms** | `57.8260` |
| **Task_Creation_Latency_Mean_ms** | `5.8910` |
| **Tool_Execution_Latency_Mean_ms** | `0.0190` |
| **Model_Inference_Latency_Mean_ms** | `3542.9430` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.001ms, P95=0.003ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 57.8 | Mean=57.826ms, P95=578.234ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 5.9 | Mean=5.891ms, P95=6.904ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.019ms, P95=0.077ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 3542.9 | Mean=3542.943ms, P95=3681.751ms |
