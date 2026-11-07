# Workflow smoke tests

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
