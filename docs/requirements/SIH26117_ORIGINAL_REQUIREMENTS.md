# SIH26117 requirements — user-provided summary

This is the user's summary of the original problem statement, not a verbatim copy and not independently verified against the official SIH website. The complete original statement remains required for authoritative traceability.

## Background
Refineries, PSUs, defence-linked manufacturing units and government offices generate routine but sensitive knowledge work, including approval notes, board presentations, engineering calculations, internal code, scanned drawings and inspection reports. Confidential organizational data must remain on premises rather than being uploaded to external AI assistants.

## Description
The required solution is a self-hosted, air-gapped AI workbench running on organizational GPU infrastructure. It must support multiple open-weight models, automatically select suitable models for different tasks, and permit new models without redesign.

The assistant must perform multi-step planning and local tool execution, including file operations, sandboxed code, spreadsheet processing, and internal document search. It must support scanned PDFs, handwritten notes, engineering drawings, photographs, local OCR and vision models.

It must generate real deliverables, including approval notes, PPTX, DOCX, XLSX, working code and calculations, grounded in an entirely local organizational knowledge base.

## Expected solution
A working local deployment on a workstation or server with a mid-range GPU, demonstrating automatic model selection across at least two task types, an end-to-end agentic document workflow, verified sandboxed coding, multimodal image/scanned-document understanding, and visible evidence that no external calls occur.
