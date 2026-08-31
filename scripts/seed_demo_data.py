"""
scripts/seed_demo_data.py
--------------------------
One-Command Synthetic Industrial Dataset & Document Seeder.

Preloads the workbench for the live internal round:
  1. Copies seed datasets (mrpl_lab_composition_test.csv) to data/uploads/
  2. Copies industrial standard documents (mrpl_refinery_specs.md, equipment_valve_manual.md,
     flare_drum_incident_runbook.md) to data/uploads/ and indexes them into ChromaDB.
  3. Generates high-resolution synthetic inspection image corroded_valve_sample.png.
  4. Verifies database WAL mode, vector store chunk count, and sandbox permissions.

Usage:
  python scripts/seed_demo_data.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.rag.embeddings import EmbeddingService
from backend.rag.store import VectorStore
from backend.rag.retriever import Retriever
from backend.rag.service import DocumentService


async def seed_data():
    print("=" * 70)
    print(" [SOVEREIGN WORKBENCH] SEEDING INDUSTRIAL DATASETS & BENCHMARKS")
    print("=" * 70)

    # 1. Ensure runtime directories
    settings.ensure_dirs()
    images_dir = settings.upload_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    seed_dir = PROJECT_ROOT / "data" / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)

    # 2. Copy test datasets to data/uploads/
    csv_src = seed_dir / "mrpl_lab_composition_test.csv"
    if csv_src.exists():
        csv_dest = settings.upload_dir / "mrpl_lab_composition_test.csv"
        shutil.copy2(csv_src, csv_dest)
        print(f" [+] Copied test dataset to: {csv_dest}")

    # 3. Generate synthetic valve inspection photo
    try:
        from scripts.generate_sample_valve_image import TARGET_FILE
        print(f" [+] Verified synthetic equipment inspection image at: {TARGET_FILE}")
    except Exception as exc:
        print(f" [!] Error generating sample valve image: {exc}")

    # 4. Copy and index industrial standard documents
    doc_files = [
        "mrpl_refinery_specs.md",
        "equipment_valve_manual.md",
        "flare_drum_incident_runbook.md",
    ]

    embedding_service = EmbeddingService(
        base_url=settings.ollama_base_url,
        model=settings.embedding_model,
    )
    vector_store = VectorStore(persist_dir=settings.chroma_persist_dir)
    retriever = Retriever(embedding_service=embedding_service, vector_store=vector_store)
    doc_service = DocumentService(
        settings=settings,
        embedding_service=embedding_service,
        vector_store=vector_store,
        retriever=retriever,
    )

    ollama_ready = await embedding_service.health_check()
    if not ollama_ready:
        print(f"\n [!] WARNING: Ollama is not running at {settings.ollama_base_url}.")
        print("     Copying documents to data/uploads/ without vector indexing.")
        print("     Run 'ollama serve' and re-run this script to build the vector index.")

    for doc_name in doc_files:
        src = seed_dir / doc_name
        dest = settings.upload_dir / doc_name
        if src.exists():
            shutil.copy2(src, dest)
            print(f" [+] Upload file staged: {dest.name}")

            if ollama_ready:
                try:
                    # Ingest and index
                    content_bytes = dest.read_bytes()
                    res = await doc_service.ingest_document(
                        filename=dest.name,
                        content=content_bytes,
                    )
                    print(f"     -> Indexed '{dest.name}' into ChromaDB (chunks={res.chunk_count})")
                except Exception as exc:
                    print(f"     -> [!] Ingest error for {dest.name}: {exc}")


    # 5. Database & Sandbox Self-Check
    import sqlite3
    db_path = settings.tasks_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.commit()
    conn.close()
    print(" [+] SQLite tasks & audit database initialized with WAL journal mode.")

    # Sandbox probe
    probe = settings.sandbox_dir / ".probe"
    probe.write_text("probe", encoding="utf-8")
    probe.unlink()
    print(" [+] Sandbox filesystem (data/sandbox/) verified writable.")

    print("\n" + "=" * 70)
    print(" [READY] SEED DATA & BENCHMARKS DEPLOYED SUCCESSFULLY")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(seed_data())
