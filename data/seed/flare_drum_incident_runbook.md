# Emergency Runbook: Flare Knock-Out Drum (FKOD-101) Pressure Anomaly
**Document Ref:** SOP-RUNBOOK-087-REV3  
**Classification:** Process Safety Management Emergency Operating Procedure  
**Facility Unit:** Main Flare & Hydrocarbon Relief Network

---

## 1. Trigger Conditions
- **High Pressure Alarm (PAH-104)**: FKOD-101 vessel pressure exceeds **2.40 bar gauge** (Normal operating range: 0.15 – 0.50 bar g).
- **High-High Pressure Trip (PAHH-104)**: FKOD-101 vessel pressure exceeds **3.20 bar gauge**.
- **Differential Variance**: Transmitter `PT-104A` and `PT-104B` reading discrepancy > 0.45 bar.

---

## 2. Mandatory Autonomous Response Sequence

```
[Trigger: Pressure Variance / Spike > 2.40 bar g]
       │
       ▼
Step 1: Instrument Redundancy Verification
        Cross-check PT-104A vs PT-104B. If one transmitter reads normal and second spikes,
        verify liquid level in seal drum before manual blowdown.
       │
       ▼
Step 2: Flare Header Depressurization Interlock
        Command Motor-Operated Bypass Valve MOV-8802 (Secondary Flare Header Vent) to OPEN.
        Confirm limit switch feedback within 12 seconds.
       │
       ▼
Step 3: Liquid Hydrocarbon Drain Activation
        Engage automated sump pump P-101A to evacuate accumulated condensate to Slop Tank TK-904.
       │
       ▼
Step 4: Steam Injection Rate Boost
        Increase smokeless flare tip steam injection control valve FV-401 to 85% to maintain combustion efficiency.
       │
       ▼
Step 5: Operational Notification & Ticker Alert Dispatch
        Generate Incident Status Dispatch Log and publish Level-2 Safety Alert Ticker to Central Control Room console.
```

---

## 3. Incident Logging & Dispatch Data Structure
Every triggered runbook must log:
- Timestamp of anomaly detection
- Primary pressure reading (`PT-104A`, `PT-104B`)
- Status of automatic valves (`MOV-8802`, `FV-401`)
- Liquid sump evacuation rate
- Escalation level (`Level 1: Advisory`, `Level 2: Urgent Interlock`, `Level 3: Plant Trip`)
- Local audit reference hash
