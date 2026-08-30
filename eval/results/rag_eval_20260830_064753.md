# Evaluation Report: RAG Industrial Retrieval & Groundedness Benchmark
**Timestamp:** 2026-08-30T06:47:53Z UTC  
**Environment:** Air-Gapped Local ChromaDB  
**Total Duration:** 7.08s  

## Summary Scorecard

| Metric | Value |
|---|---|
| **Total Test Cases** | `15` |
| **Passed** | `15` |
| **Failed** | `0` |
| **Environment Unavailable** | `0` |
| **Mean_Precision_at_K** | `0.7500` |
| **Mean_Recall_at_K** | `1.0000` |
| **Mean_Retrieval_Latency_ms** | `11.7200` |
| **Groundedness_Fact_Overlap_Rate** | `0.9000` |
| **Success_Rate_Percent** | `100.0000` |

## Detailed Test Cases

| ID | Test Name | Category | Status | Latency (ms) | Details |
|---|---|---|---|---|---|
| `QA-01` | RAG: vibration_diagnostics | vibration_diagnostics | **PASS** | 14.7 | P@4=0.75, R@4=1.00, Facts=3/3, Docs=['compressor_k101_inspection.md', 'compressor_k101_inspection.md'] |
| `QA-02` | RAG: root_cause_analysis | root_cause_analysis | **PASS** | 11.2 | P@4=0.50, R@4=1.00, Facts=2/3, Docs=['compressor_k101_inspection.md', 'compressor_k101_inspection.md'] |
| `QA-03` | RAG: temperature_monitoring | temperature_monitoring | **PASS** | 11.5 | P@4=0.75, R@4=1.00, Facts=3/3, Docs=['pump_p204_maintenance.md', 'pump_p204_maintenance.md'] |
| `QA-04` | RAG: hydraulic_performance | hydraulic_performance | **PASS** | 13.4 | P@4=0.75, R@4=1.00, Facts=4/4, Docs=['pump_p204_maintenance.md', 'pump_p204_maintenance.md'] |
| `QA-05` | RAG: materials_specification | materials_specification | **PASS** | 9.8 | P@4=0.75, R@4=1.00, Facts=2/2, Docs=['pump_p204_maintenance.md', 'pump_p204_maintenance.md'] |
| `QA-06` | RAG: static_equipment | static_equipment | **PASS** | 11.5 | P@4=1.00, R@4=1.00, Facts=2/2, Docs=['heat_exchanger_e302_report.md', 'heat_exchanger_e302_report.md'] |
| `QA-07` | RAG: maintenance_procedures | maintenance_procedures | **PASS** | 11.3 | P@4=1.00, R@4=1.00, Facts=3/3, Docs=['heat_exchanger_e302_report.md', 'heat_exchanger_e302_report.md'] |
| `QA-08` | RAG: safety_critical_valves | safety_critical_valves | **PASS** | 11.6 | P@4=0.75, R@4=1.00, Facts=2/2, Docs=['valve_v401_failure_analysis.md', 'valve_v401_failure_analysis.md'] |
| `QA-09` | RAG: materials_specification | materials_specification | **PASS** | 11.4 | P@4=0.75, R@4=1.00, Facts=4/4, Docs=['valve_v401_failure_analysis.md', 'valve_v401_failure_analysis.md'] |
| `QA-10` | RAG: corrosion_management | corrosion_management | **PASS** | 12.4 | P@4=0.75, R@4=1.00, Facts=2/3, Docs=['pipeline_corrosion_survey.md', 'pipeline_corrosion_survey.md'] |
| `QA-11` | RAG: corrosion_management | corrosion_management | **PASS** | 11.0 | P@4=0.75, R@4=1.00, Facts=3/3, Docs=['pipeline_corrosion_survey.md', 'pipeline_corrosion_survey.md'] |
| `QA-12` | RAG: reliability_strategy | reliability_strategy | **PASS** | 11.8 | P@4=1.00, R@4=1.00, Facts=2/3, Docs=['equipment_recurring_issues_summary.md', 'equipment_recurring_issues_summary.md'] |
| `QA-13` | RAG: reliability_metrics | reliability_metrics | **PASS** | 11.3 | P@4=0.25, R@4=1.00, Facts=1/2, Docs=['pump_p204_maintenance.md', 'compressor_k101_inspection.md'] |
| `QA-14` | RAG: vibration_diagnostics | vibration_diagnostics | **PASS** | 11.5 | P@4=1.00, R@4=1.00, Facts=2/2, Docs=['compressor_k101_inspection.md', 'compressor_k101_inspection.md'] |
| `QA-15` | RAG: maintenance_procedures | maintenance_procedures | **PASS** | 11.2 | P@4=0.50, R@4=1.00, Facts=3/3, Docs=['pipeline_corrosion_survey.md', 'equipment_recurring_issues_summary.md'] |
