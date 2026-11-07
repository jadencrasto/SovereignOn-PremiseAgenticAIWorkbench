# Requirements traceability

## Original mandatory SIH requirements: BLOCKED pending complete original

The user has supplied a summary in `docs/requirements/SIH26117_ORIGINAL_REQUIREMENTS.md`. It is not the complete statement and has not been independently verified. The following are traceable to that summary, not verified official wording. No completion percentage is justified.

| ID | User-summarized requirement | Evidence | Status | Gap / priority |
|---|---|---|---|---|
| SIH-S1 | Self-hosted, air-gapped on GPU | backend/models/router.py; frontend/index.html | UNVERIFIED | Network isolation, GPU and full offline deployment need measurement / P1 |
| SIH-S2 | Multiple open-weight models and automatic task selection, extensibility | config/models.yaml; backend/models/router.py | PARTIAL | Live automatic selection for two task types unverified / P1 |
| SIH-S3 | Planning, tools, file operations, sandboxed code, spreadsheets, document search | backend/agent/engine.py; backend/tools | PARTIAL | Real complete workflow and verified sandbox outstanding / P1 |
| SIH-S4 | Scanned PDFs, handwriting, engineering drawings, photos, local OCR and vision | backend/rag/ingest.py; backend/multimodal | PARTIAL | OCR and scanned-document support not established / P1 |
| SIH-S5 | Approval notes, PPTX, DOCX, XLSX, working code and calculations grounded in local knowledge | backend/tools | PARTIAL | PPTX implementation not established; live grounding and generation unverified / P1 |
| SIH-S6 | Workstation/server demonstration with mid-range GPU, visible proof of no external calls | backend/models/hardware.py | UNVERIFIED | Demonstration and network evidence not obtained / P1 |

## General-purpose product and enterprise-readiness requirements

## Provisional product and production requirements

| Requirement ID | Provisional requirement | Source reference | Current implementation | Evidence | Status | Gap | Priority | Proposed phase |
|---|---|---|---|---|---|---|---|---|
| PR-01 | Local chat and model routing | README.md:3 | Ollama provider | backend/models/router.py:294 | UNVERIFIED | live inference blocked | P1 | 2 |
| PR-02 | Self-contained / air-gapped UI | README.md:6 | remote font requests | frontend/src/index.css:1, frontend/index.html:8 | BROKEN | external browser requests | P1 | 2 |
| PR-03 | Grounded document Q&A | docs/MVP_SCOPE.md M7-M10 | parsing and vector retrieval | backend/rag/service.py:100; retriever.py:89 | PARTIAL | evaluate grounding, citations, recall | P1 | later RAG |
| PR-04 | Local tool execution and agent planning | docs/MVP_SCOPE.md M11-M14; README.md | planner, schema and registry | backend/agent/planner.py:496; backend/tools/registry.py:132 | PARTIAL | live reliability and authorization | P1 | 2 |
| PR-05 | Approval and artifact verification | README.md security section | approval hash and verifier | backend/agent/approval.py:38,306 | UNVERIFIED | end-to-end live execution | P1 | 2 |
| PR-06 | User isolation | production objective, not original PS | session IDs are global | backend/api/chat.py:1208; backend/agent/memory.py:127 | BROKEN | ownership enforcement | P1 | 2 |
| PR-07 | Image and document understanding | README.md:5 | image route, PDF text parsing | backend/api/chat.py:288; backend/rag/ingest.py:66 | PARTIAL | live vision, OCR, document formats | P2 | later multimodal |
| PR-08 | Auditable operations | README.md security section | SQLite audit and task records | backend/main.py:601; backend/audit/logger.py | PARTIAL | tamper resistance, lifecycle, recovery | P2 | 2 |
| PR-09 | Safe executable code | docs/MVP_SCOPE.md M12 | Docker and subprocess modes | backend/tools/code_execution.py:1,385 | PARTIAL | verify isolated production image and runtime | P1 | 2 |

Optional research: specialized agents, LoRA/QLoRA, benchmark harness and provider extensions require use cases, local data rights, compute budgets and reproducibility decisions; none are represented as official requirements.
