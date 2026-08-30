# ANNUAL PLANT-WIDE RECURRING EQUIPMENT ISSUES & RELIABILITY SUMMARY (2025–2026)
**Document ID:** DOC-SUMMARY-2026-RECURRING
**Reporting Period:** March 2025 – February 2026
**Facility:** Synthetic Industrial Refinery & Petrochemical Complex
**Author:** Reliability & Mechanical Asset Management Department
**Classification:** Internal Operational Reliability Benchmark

---

## 1. STRATEGIC OVERVIEW & KEY EQUIPMENT TRENDS
Over the past 12 months, cross-unit equipment health diagnostics identified three high-frequency recurring failure modes across rotating and static assets:

1. **Rotating Machinery (Pumps & Compressors):**
   - **Mechanical Seal Degradation:** 14 pump seal replacements across CDU, VDU, and Utilities. Primary causes: seal flush cooler fouling (API Plan 23/32), dry running during priming, and thermal face distortion.
   - **Bearing Overheating & High Vibration:** 8 events attributed to grease starvation, flinger ring detachment, and dynamic unbalance from process polymer deposition (notably on K-101 and P-204).
2. **Static & Heat Transfer Equipment:**
   - **Exchanger Tube Fouling (E-302 / E-104):** Heavy asphaltene and coke deposition causing up to 2.45 bar delta-P increase and 45% loss of heat recovery efficiency.
   - **Chloride-Induced Local Wall Thinning:** Accelerated corrosion downstream of neutralizing injection quills on atmospheric column overhead lines (PL-12 remaining corrosion allowance down to 0.42 mm).
3. **Valves & Safety Systems:**
   - **Emergency Depressuring & Control Valve Sticking (V-401 / V-202):** Stem galling and graphite packing friction locking during Partial Stroke Tests (PST).

---

## 2. CROSS-UNIT RECURRING ASSET BREAKDOWN

| Equipment Tag | Description | Unit | Incident Count (12 mo) | Primary Failure Mechanism | Status / Mitigations |
|---|---|---|---|---|---|
| **K-101** | Wet Gas Compressor | FCCU | 3 alarms / 1 overhaul | Stage 3 polymer unbalance, carbon ring seal scoring | Dynamic balancing complete; improved mist eliminator planned |
| **P-204** | Boiler Feed Pump | Utilities | 2 trips / 1 overhaul | Suction strainer magnetite clogging, bearing overheating (88°C) | New 13Cr impeller installed; daily delta-P logging implemented |
| **E-302** | Crude Pre-Heat Exchanger | CDU | 2 bundle cleanings | Asphaltene fouling, tube thinning on top row | High-pressure hydro-jetting (1,400 bar); 14 tubes plugged |
| **V-401** | Emergency Depressuring Valve | Hydrocracker | 2 test failures | 17-4 PH stem galling, non-uniform packing torque | Replaced with Nitronic 50 stem & live-loaded packing |
| **PL-12** | CDU Overhead Vapor Line | CDU | 1 survey flag | Accelerated local thinning (4.22 mm remaining vs 3.80 mm min) | Water wash rate increased; Hastelloy quill replacement planned |

---

## 3. ASSET MANAGEMENT RECOMMENDATIONS FOR UPCOMING WORKBENCH ACTIONS
1. **Automated Maintenance Reporting:** Aggregate maintenance logs across K-101, P-204, E-302, and V-401 into a unified digital workspace report with citations.
2. **Predictive Monitoring Protocol:** Cross-reference vibration telemetry log `vibration_log_2026.csv` with bearing temperature thresholds.
3. **Air-Gapped Reliability Knowledgebase:** Ingest all inspection markdown and technical records into local ChromaDB for instantaneous RAG retrieval by plant operators.
