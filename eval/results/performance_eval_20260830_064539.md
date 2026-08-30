# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T06:45:39Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 13.60s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0010` |
| **Health_Ready_Latency_Mean_ms** | `57.9150` |
| **Task_Creation_Latency_Mean_ms** | `5.8890` |
| **Tool_Execution_Latency_Mean_ms** | `0.0180` |
| **Model_Inference_Latency_Mean_ms** | `3543.5410` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.001ms, P95=0.004ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 57.9 | Mean=57.915ms, P95=579.124ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 5.9 | Mean=5.889ms, P95=6.123ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.018ms, P95=0.07ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 3543.5 | Mean=3543.541ms, P95=3631.459ms |
