# CRITICAL VALVE FAILURE ANALYSIS REPORT: V-401 EMERGENCY DEPRESSURING VALVE
**Document ID:** DOC-FAIL-2026-V401
**Date of Incident / Analysis:** 2026-02-28
**Equipment Tag:** V-401 (Hydrocracker Reactor Loop Emergency Depressuring Control Valve)
**Service:** High-Pressure Hydrogen / Sour Hydrocarbon Vapor (Design Pressure: 175 bar, Temp: 380°C)
**Safety Integrity Level:** SIL-3 Instrumented Protective Function
**Lead Failure Analyst:** Dr. K. N. Rao (Materials & Reliability Engineering)

---

## 1. INCIDENT DESCRIPTION
During routine quarter-yearly Partial Stroke Testing (PST), emergency depressuring valve V-401 failed to complete its commanded 10% stroke within the required safety time limit of 2.0 seconds (actual response time: 8.4 seconds with severe stick-slip behavior). Concurrently, optical gas imaging (OGI) detected fugitive hydrocarbon emissions at the valve packing gland exceeding 8,500 ppm VOC.

## 2. METALLURGICAL & MECHANICAL ROOT CAUSE
- **Stem Galling & Friction Lock:**
  - The 17-4 PH stainless steel valve stem exhibited severe micro-abrasive galling and axial scoring along a 180 mm travel stroke zone.
  - Hardness testing showed localized hardening to 44 HRC in galled zones, caused by sliding adhesion under high contact stress against the packing follower bushing.
- **Graphite Packing Degradation:**
  - The high-pressure die-formed flexible graphite packing rings had lost elasticity due to thermal oxidation and trace contamination from sour hydrogen sulfide ($H_2S$) stream.
  - Packing gland bolt torque was found non-uniform (ranging from 45 Nm on north stud to 110 Nm on south stud vs specified 85 Nm), creating angular misalignment and excessive side loading on the stem.
- **Actuator Pneumatics:**
  - Air filter regulator on the double-acting pneumatic piston actuator was partially saturated with condensate water, reducing supply air pressure from 6.0 bar to 4.1 bar.

## 3. IMMEDIATE CORRECTIVE ACTIONS
1. Replaced scored valve stem with new OEM Stellite-coated Nitronic 50 (XM-19) high-strength, gall-resistant austenitic stainless steel stem.
2. Replaced complete packing set with Chesterton 1600 Low-Emission certified live-loaded graphite packing featuring Inconel wire reinforcement and Belleville spring washers to maintain constant sealing pressure during thermal cycling.
3. Cleaned and overhauled the pneumatic actuator; replaced air filter-regulator with automatic drain module and re-calibrated Fisher DVC6200 digital valve controller.
4. Performed Helium leak test per ISO 15848-1 Class A (tightness < 10⁻⁵ Pa·m³/s·mm); measured post-repair fugitive emission concentration: **12 ppm VOC** (well below 50 ppm site limit).

## 4. SAFETY VERIFICATION & STROKE TESTING
- Full-stroke travel time (0% to 100% emergency opening): **1.42 seconds** (Acceptable design target: < 2.0 seconds).
- Partial stroke test execution: PASS (0.45 second response).
- SIL-3 proof test documentation signed off and updated in Safety Instrumented System registry.
