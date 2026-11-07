# Workflow smoke tests

## Security implementation continuation, 2026-11-07

Two-user conversation isolation was exercised through an in-process ASGI client with fake authentication/model services and isolated JSON storage. Restart, threaded and two-process persistence, clearance filtering over synthetic metadata, one-use approval dispatch and request-body limits were exercised in targeted tests. These are not real network/browser, full application startup, live RAG or real-model end-to-end tests. No full-suite retry was attempted.

## Continuation, 2026-11-07

| Check | Result | Scope |
|---|---|---|
| Qwen local inference | PASS: `READY`, 14.51s | Direct Ollama API, synthetic prompt, not application chat |
| Nomic embeddings | PASS: 768 dimensions, 5.7s | Direct Ollama API, synthetic text |
| LLaVA vision | PASS: `Red`, 12.93s | Direct Ollama API, generated red rectangle |
| DOCX and XLSX | PASS | Direct tool invocation with synthetic text/table in a temporary directory; reopened with python-docx/openpyxl; no model, agent or approval |
| Approval and denial | PARTIAL | Temporary SQLite approval-manager and registry regression; not end-to-end UI workflow |
| Restart and recovery | PARTIAL | Temporary legacy JSON and threaded write tests; ownership and multi-process scenarios unverified |
| RAG/citation, full agent workflow, browser offline, verified Docker code isolation | BLOCKED/NOT RUN | Full-suite telemetry auto-review rejection or no isolated verified setup |

These direct checks used only synthetic content. They do not replace live application workflow validation.

No live model workflow was verified: the environment denied loopback access to Ollama and a minimal local asyncio loop did not complete. No production data was used. Test durations and outputs below are unavailable.

| Synthetic workflow | Result | Reason |
|---|---|---|
| Basic chat | BLOCKED | Ollama and async runtime |
| Multi-step agent execution | BLOCKED | Ollama and async runtime |
| PDF/DOCX ingestion and grounded Q&A | BLOCKED | Embedding model and async runtime |
| DOCX report generation | NOT RUN | No isolated functioning agent workflow |
| XLSX workbook generation | NOT RUN | No isolated functioning agent workflow |
| Sandboxed code and verification | NOT RUN | No approved verified isolated Docker image established |
| Image/scanned document | BLOCKED | Vision model and async runtime |
| Tool-disabled negative | BLOCKED | Async test added, not completed |
| Approval-required execution | BLOCKED | Async runtime; full approval path not exercised |
| Conversation restart and recovery | NOT RUN | Ownership migration not implemented |

No mock-only test is counted as a live workflow.
