# Sovereign AI Workbench — Phase 8 Evaluation Scorecard
**Generated:** 2026-08-30 06:45:48 UTC  
**Overall Status:** `FAIL`  
**Air-Gapped Local Environment:** Verified (100% on-premise execution)  
**Total Execution Time:** 26.01s  

---

## 1. Executive Evaluation Summary

| Metric | Count | Percentage |
|---|---|---|
| **Total Evaluation Cases** | `55` | 100.0% |
| **Passed (Verified)** | `54` | 98.2% |
| **Failed (Regressions)** | `1` | 1.8% |
| **Environment Unavailable (Offline LLM)** | `0` | 0.0% |

---

## 2. Evaluation Suite Breakdown

| Suite Name | Total | Passed | Failed | Unavail | Duration (s) | Key Benchmark Metrics |
|---|---|---|---|---|---|---|
| **Agent Reliability & Fault Injection Benchmark** | 7 | 7 | 0 | 0 | 0.26 | Passed_Scenarios: `7`<br>Total_Scenarios: `7`<br>Reliability_Score_Percent: `100.0` |
| **Security & Enterprise Hardening Evaluation Suite** | 10 | 10 | 0 | 0 | 0.25 | Security_Controls_Passed: `10`<br>Total_Security_Controls: `10`<br>Compliance_Rate_Percent: `100.0` |
| **Tool Execution, Authorization & Latency Benchmark** | 7 | 7 | 0 | 0 | 0.01 | Tools_Evaluated: `5`<br>Passed_Tool_Operations: `7`<br>Average_Tool_Latency_ms: `0.7`<br>Success_Rate_Percent: `100.0` |
| **System Latency & Performance Benchmark** | 5 | 5 | 0 | 0 | 13.60 | Health_Live_Latency_Mean_ms: `0.001`<br>Health_Ready_Latency_Mean_ms: `57.915`<br>Task_Creation_Latency_Mean_ms: `5.889`<br>Tool_Execution_Latency_Mean_ms: `0.018`<br>Model_Inference_Latency_Mean_ms: `3543.541` |
| **Baseline vs System Comparative Benchmark** | 2 | 2 | 0 | 0 | 0.00 | RAG_Fact_Coverage: `1.0`<br>No_Retrieval_Fact_Coverage: `0.0`<br>Agent_Workflow_Decomposition_Support: `Enabled (Multi-Step DAG + Approval Binding)`<br>Baseline_Direct_Execution_Support: `Single-Turn Only (No Approval Binding)` |
| **Multimodal Vision & Equipment Inspection Benchmark** | 4 | 4 | 0 | 0 | 2.35 | Image_Processing_Verified: `True`<br>Context_Injection_Verified: `True`<br>Degradation_Tolerance_Verified: `True` |
| **Industrial Report Generation Workflow Benchmark** | 5 | 5 | 0 | 0 | 0.21 | Workflow_Stages_Completed: `5`<br>Total_Workflow_Stages: `5`<br>Workflow_Success_Rate: `1.0` |
| **RAG Industrial Retrieval & Groundedness Benchmark** | 15 | 14 | 1 | 0 | 6.87 | Mean_Precision_at_K: `0.7333`<br>Mean_Recall_at_K: `1.0`<br>Mean_Retrieval_Latency_ms: `11.37`<br>Groundedness_Fact_Overlap_Rate: `0.8333`<br>Success_Rate_Percent: `93.3` |

---

## 3. Environment & Deployment Diagnostics

- **Operating System:** `nt`
- **Python Version:** `3.13.7`
- **Local Ollama Online:** `True`
- **Loaded Local Models:** `nomic-embed-text:latest, llava:7b, qwen2.5:7b`
- **Data Sovereignty Assurance:** Zero external cloud egress, all embeddings & vector stores local

