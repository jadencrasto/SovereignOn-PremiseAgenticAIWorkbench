# Evaluation Report: System Latency & Performance Benchmark
**Timestamp:** 2026-09-27T20:18:07Z UTC
**Environment:** Air-Gapped Local Benchmarking
**Total Duration:** 30.01s

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `5` |
| **Passed** | `5` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Health_Live_Latency_Mean_ms** | `0.0020` |
| **Health_Ready_Latency_Mean_ms** | `196.0010` |
| **Task_Creation_Latency_Mean_ms** | `5.8340` |
| **Tool_Execution_Latency_Mean_ms** | `0.0330` |
| **Model_Inference_Latency_Mean_ms** | `9015.5810` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `PERF-01` | Health Live Probe Latency | health_probes | **PASS** | 0.0 | Mean=0.002ms, P95=0.003ms |
| `PERF-02` | Health Ready Probe Latency | health_probes | **PASS** | 196.0 | Mean=196.001ms, P95=587.991ms |
| `PERF-03` | Task Creation SQLite WAL Latency | task_persistence | **PASS** | 5.8 | Mean=5.834ms, P95=6.227ms |
| `PERF-04` | Tool Execution Latency (Calculator) | tool_dispatch | **PASS** | 0.0 | Mean=0.033ms, P95=0.068ms |
| `PERF-05` | Local LLM Inference Latency | model_inference | **PASS** | 9015.6 | Mean=9015.581ms, P95=19670.824ms |
