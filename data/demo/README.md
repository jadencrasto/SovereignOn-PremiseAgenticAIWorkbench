# SYNTHETIC DEMO DATASET — Sovereign On-Premise Agentic AI Workbench
## Industrial Refinery Maintenance & Equipment Reliability

> [!IMPORTANT]
> **ALL DATA IN THIS DIRECTORY IS 100% SYNTHETIC AND FICTITIOUS.**
> This dataset was generated solely for benchmark evaluation, integration testing, and local offline demonstration of the Sovereign AI Workbench. It contains NO proprietary, confidential, or actual refinery operational data.

---

### Dataset Contents

| Filename | Equipment Tag | Description | Topics & Failure Modes |
|---|---|---|---|
| `compressor_k101_inspection.md` | `K-101` | Multi-stage Wet Gas Compressor Overhaul & Vibration Report | Rotor unbalance, high vibration on Stage 3, carbon ring seal degradation, bearing clearance drift. |
| `pump_p204_maintenance.md` | `P-204` | Boiler Feed Water Pump Reliability & Overhaul Log | Driven-end bearing overheating (88°C), suction cavitation, impeller vane erosion, mechanical seal leakage. |
| `heat_exchanger_e302_report.md` | `E-302` | Crude Distillation Unit Pre-Heat Exchanger Inspection | Heavy asphaltene tube fouling, delta-P increase across shell (2.4 bar), thermal effectiveness drop. |
| `valve_v401_failure_analysis.md` | `V-401` | Hydrocracker Emergency Depressuring Valve Inspection | Valve stem galling, high-pressure graphite packing failure, fugitive emissions, actuator calibration drift. |
| `pipeline_corrosion_survey.md` | `C-101 / PL-12` | Atmospheric Column Overhead Line UT Thickness Survey | Sulfidation corrosion, local wall thinning (remaining 4.2mm vs min 3.8mm), injection quill erosion. |
| `equipment_recurring_issues_summary.md` | Multiple | Annual Rotating & Static Equipment Failure Summary (2025–2026) | Cross-unit failure trends, recurring pump seal failures, high vibration patterns, root cause actions. |
| `vibration_log_2026.csv` | `K-101`, `P-204`, etc. | Equipment Vibration Telemetry Sample Log | Weekly RMS velocity (mm/s), peak acceleration (g), bearing temperature (°C). |
| `pump_impeller_inspection.png` | `P-204` | Synthetic Equipment Condition Image | Impeller blade erosion and cavitation pitting demonstration for multimodal analysis. |

---

### Usage in Phase 8 Evaluation & Scenarios
- **RAG Evaluation (`eval/rag_eval.py`):** Verified against `eval/data/qa_set.json` for Precision@K, Recall@K, and source attribution.
- **Report Generation Workflow (`eval/report_eval.py`):** Agent analyzes multiple documents to synthesize maintenance trends and generate a sandboxed report.
- **Multimodal Evaluation (`eval/multimodal_eval.py`):** Equipment image visual condition assessment via LLaVA.
