# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-08-30T06:46:30Z UTC  
**Environment:** Air-Gapped Local Benchmarking  
**Total Duration:** 13.39s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0010` |
| **Health_Ready_Latency_Mean_ms** | `58.9530` |
| **Task_Creation_Latency_Mean_ms** | `6.2920` |
| **Tool_Execution_Latency_Mean_ms** | `0.0180` |
| **Model_Inference_Latency_Mean_ms** | `3459.2940` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.001ms, P95=0.003ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 59.0 | Mean=58.953ms, P95=589.512ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 6.3 | Mean=6.292ms, P95=6.768ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.018ms, P95=0.069ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 3459.3 | Mean=3459.294ms, P95=3584.297ms |
