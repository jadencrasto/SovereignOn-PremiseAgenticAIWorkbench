# Sovereign AI Workbench — Phase 8 Evaluation Scorecard
**Generated:** 2026-08-30 06:44:47 UTC  
**Overall Status:** `FAIL`  
**Air-Gapped Local Environment:** Verified (100% on-premise execution)  
**Total Execution Time:** 25.91s  

---

## 1. Executive Evaluation Summary

| Metric | Count | Percentage |
|---|---|---|
| **Total Evaluation Cases** | `55` | 100.0% |
| **Passed (Verified)** | `52` | 94.5% |
| **Failed (Regressions)** | `3` | 5.5% |
| **Environment Unavailable (Offline LLM)** | `0` | 0.0% |

---

## 2. Evaluation Suite Breakdown

| Suite Name | Total | Passed | Failed | Unavail | Duration (s) | Key Benchmark Metrics |
|---|---|---|---|---|---|---|
| **Agent Reliability & Fault Injection Benchmark** | 7 | 7 | 0 | 0 | 0.23 | Passed_Scenarios: `7`<br>Total_Scenarios: `7`<br>Reliability_Score_Percent: `100.0` |
| **Security & Enterprise Hardening Evaluation Suite** | 10 | 10 | 0 | 0 | 0.24 | Security_Controls_Passed: `10`<br>Total_Security_Controls: `10`<br>Compliance_Rate_Percent: `100.0` |
| **Tool Execution, Authorization & Latency Benchmark** | 7 | 7 | 0 | 0 | 0.01 | Tools_Evaluated: `5`<br>Passed_Tool_Operations: `7`<br>Average_Tool_Latency_ms: `0.61`<br>Success_Rate_Percent: `100.0` |
| **System Latency & Performance Benchmark** | 5 | 5 | 0 | 0 | 13.57 | Health_Live_Latency_Mean_ms: `0.001`<br>Health_Ready_Latency_Mean_ms: `57.826`<br>Task_Creation_Latency_Mean_ms: `5.891`<br>Tool_Execution_Latency_Mean_ms: `0.019`<br>Model_Inference_Latency_Mean_ms: `3542.943` |
| **Baseline vs System Comparative Benchmark** | 2 | 2 | 0 | 0 | 0.00 | RAG_Fact_Coverage: `1.0`<br>No_Retrieval_Fact_Coverage: `0.0`<br>Agent_Workflow_Decomposition_Support: `Enabled (Multi-Step DAG + Approval Binding)`<br>Baseline_Direct_Execution_Support: `Single-Turn Only (No Approval Binding)` |
| **Multimodal Vision & Equipment Inspection Benchmark** | 4 | 4 | 0 | 0 | 2.36 | Image_Processing_Verified: `True`<br>Context_Injection_Verified: `True`<br>Degradation_Tolerance_Verified: `True` |
| **Industrial Report Generation Workflow Benchmark** | 5 | 5 | 0 | 0 | 0.20 | Workflow_Stages_Completed: `5`<br>Total_Workflow_Stages: `5`<br>Workflow_Success_Rate: `1.0` |
| **RAG Industrial Retrieval & Groundedness Benchmark** | 15 | 12 | 3 | 0 | 6.89 | Mean_Precision_at_K: `0.7167`<br>Mean_Recall_at_K: `1.0`<br>Mean_Retrieval_Latency_ms: `11.46`<br>Groundedness_Fact_Overlap_Rate: `0.7389`<br>Success_Rate_Percent: `80.0` |

---

## 3. Environment & Deployment Diagnostics

- **Operating System:** `nt`
- **Python Version:** `3.13.7`
- **Local Ollama Online:** `True`
- **Loaded Local Models:** `nomic-embed-text:latest, llava:7b, qwen2.5:7b`
- **Data Sovereignty Assurance:** Zero external cloud egress, all embeddings & vector stores local

