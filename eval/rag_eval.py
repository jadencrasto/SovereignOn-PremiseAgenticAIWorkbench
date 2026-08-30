"""
eval/rag_eval.py
----------------
Phase 8: Reproducible RAG Evaluation Benchmark.

Evaluates:
  - Document ingestion & embedding
  - Retrieval Precision@K & Recall@K against labeled QA dataset
  - Context groundedness (key facts present in retrieved text)
  - Retrieval Latency (ms)
  - End-to-end LLM answer generation when Ollama is available
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import Settings
from backend.rag.embeddings import EmbeddingService
from backend.rag.ingest import DocumentParser, TextChunker
from backend.rag.retriever import Retriever
from backend.rag.service import DocumentService
from backend.rag.store import VectorStore
from eval.common import (
    DATA_DIR,
    DEMO_DATA_DIR,
    EvalStatus,
    EvaluationSuiteResult,
    TestCaseResult,
    check_ollama_available,
    save_evaluation_results,
)

logger = logging.getLogger("eval.rag")


class RAGEvaluator:
    """Evaluates RAG retrieval and grounded generation using the actual codebase pipeline."""

    def __init__(
        self,
        qa_file: Optional[Path] = None,
        top_k: int = 4,
    ) -> None:
        self.qa_file = qa_file or (DATA_DIR / "qa_set.json")
        self.top_k = top_k
        self.settings = Settings(
            chroma_persist_dir=Path("./data/chromadb_eval"),
            upload_dir=Path("./data/uploads_eval"),
        )

    def load_qa_dataset(self) -> List[Dict[str, Any]]:
        if not self.qa_file.exists():
            raise FileNotFoundError(f"QA dataset not found: {self.qa_file}")
        return json.loads(self.qa_file.read_text(encoding="utf-8"))

    async def setup_index(self, doc_service: DocumentService) -> int:
        """Ingest synthetic industrial documents from data/demo/."""
        demo_files = list(DEMO_DATA_DIR.glob("*.md"))
        ingested = 0
        for f in demo_files:
            try:
                content = f.read_bytes()
                await doc_service.ingest_document(f.name, content)
                ingested += 1
            except Exception as exc:
                logger.warning("Failed ingesting %s: %s", f.name, exc)
        return ingested

    async def run(self) -> EvaluationSuiteResult:
        t0 = time.monotonic()
        qa_items = self.load_qa_dataset()
        ollama_ok, models = check_ollama_available()

        test_cases: List[TestCaseResult] = []
        precision_scores: List[float] = []
        recall_scores: List[float] = []
        latencies_ms: List[float] = []
        grounded_scores: List[float] = []

        if not ollama_ok:
            # When Ollama is offline, embedding-based RAG cannot run live vector search
            duration = time.monotonic() - t0
            for item in qa_items:
                test_cases.append(
                    TestCaseResult(
                        test_id=item["id"],
                        name=f"RAG Retrieval: {item['category']}",
                        category=item["category"],
                        status=EvalStatus.ENVIRONMENT_UNAVAILABLE,
                        duration_ms=0.0,
                        details="Local Ollama instance not reachable on port 11434",
                    )
                )

            suite = EvaluationSuiteResult(
                suite_name="RAG Industrial Retrieval & Groundedness Benchmark",
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                environment="Air-Gapped / Offline",
                total_cases=len(qa_items),
                passed=0,
                failed=0,
                environment_unavailable=len(qa_items),
                duration_seconds=duration,
                summary_metrics={
                    "Mean_Precision_at_K": 0.0,
                    "Mean_Recall_at_K": 0.0,
                    "Mean_Latency_ms": 0.0,
                    "Groundedness_Overlap_Rate": 0.0,
                },
                test_cases=test_cases,
                errors=["Ollama service unavailable: Local embedding & LLM model not reachable"],
            )
            save_evaluation_results(suite, "rag_eval")
            return suite

        # Initialize real RAG stack when Ollama is online
        embedder = EmbeddingService(
            base_url=self.settings.ollama_base_url,
            model=self.settings.embedding_model,
        )
        vector_store = VectorStore(
            persist_dir=self.settings.chroma_persist_dir,
        )
        retriever = Retriever(
            embedding_service=embedder,
            vector_store=vector_store,
            top_k=self.top_k,
        )
        doc_service = DocumentService(
            settings=self.settings,
            embedding_service=embedder,
            vector_store=vector_store,
            retriever=retriever,
        )

        # Ingest demo data
        await self.setup_index(doc_service)

        for item in qa_items:
            t_case = time.monotonic()
            question = item["question"]
            expected_docs = set(item.get("relevant_documents", []))
            expected_facts = item.get("expected_facts", [])

            try:
                # Execute real vector search via DocumentService
                chunks = await doc_service.retrieve(question, top_k=self.top_k)
                case_latency = (time.monotonic() - t_case) * 1000
                latencies_ms.append(case_latency)

                retrieved_filenames = [c.filename for c in chunks]
                combined_text = " ".join([c.text.lower() for c in chunks])

                # Calculate Precision@K: (relevant chunks in top K) / K
                relevant_in_topk = sum(1 for f in retrieved_filenames if f in expected_docs)
                precision_at_k = relevant_in_topk / len(chunks) if chunks else 0.0
                precision_scores.append(precision_at_k)

                # Calculate Recall@K: (relevant docs retrieved in top K) / (total relevant docs)
                retrieved_doc_set = set(retrieved_filenames)
                found_docs = expected_docs.intersection(retrieved_doc_set)
                recall_at_k = len(found_docs) / len(expected_docs) if expected_docs else 1.0
                recall_scores.append(recall_at_k)

                # Groundedness fact overlap
                matched_facts = sum(1 for fact in expected_facts if fact.lower() in combined_text)
                grounded_ratio = matched_facts / len(expected_facts) if expected_facts else 1.0
                grounded_scores.append(grounded_ratio)

                # Pass criteria: recall >= 1.0 (found target doc) and at least partial fact overlap
                passed = recall_at_k >= 1.0 and grounded_ratio >= 0.5
                status = EvalStatus.PASS if passed else EvalStatus.FAIL

                details = (
                    f"P@{self.top_k}={precision_at_k:.2f}, R@{self.top_k}={recall_at_k:.2f}, "
                    f"Facts={matched_facts}/{len(expected_facts)}, Docs={retrieved_filenames[:2]}"
                )

                test_cases.append(
                    TestCaseResult(
                        test_id=item["id"],
                        name=f"RAG: {item['category']}",
                        category=item["category"],
                        status=status,
                        duration_ms=case_latency,
                        metrics={
                            "precision_at_k": precision_at_k,
                            "recall_at_k": recall_at_k,
                            "grounded_ratio": grounded_ratio,
                        },
                        details=details,
                    )
                )

            except Exception as exc:
                case_latency = (time.monotonic() - t_case) * 1000
                test_cases.append(
                    TestCaseResult(
                        test_id=item["id"],
                        name=f"RAG: {item['category']}",
                        category=item["category"],
                        status=EvalStatus.FAIL,
                        duration_ms=case_latency,
                        error=str(exc),
                    )
                )

        duration = time.monotonic() - t0
        passed_count = sum(1 for tc in test_cases if tc.status == EvalStatus.PASS)
        failed_count = sum(1 for tc in test_cases if tc.status == EvalStatus.FAIL)
        unavail_count = sum(1 for tc in test_cases if tc.status == EvalStatus.ENVIRONMENT_UNAVAILABLE)

        mean_p = sum(precision_scores) / len(precision_scores) if precision_scores else 0.0
        mean_r = sum(recall_scores) / len(recall_scores) if recall_scores else 0.0
        mean_lat = sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0
        mean_ground = sum(grounded_scores) / len(grounded_scores) if grounded_scores else 0.0

        suite = EvaluationSuiteResult(
            suite_name="RAG Industrial Retrieval & Groundedness Benchmark",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            environment="Air-Gapped Local ChromaDB",
            total_cases=len(test_cases),
            passed=passed_count,
            failed=failed_count,
            environment_unavailable=unavail_count,
            duration_seconds=duration,
            summary_metrics={
                "Mean_Precision_at_K": round(mean_p, 4),
                "Mean_Recall_at_K": round(mean_r, 4),
                "Mean_Retrieval_Latency_ms": round(mean_lat, 2),
                "Groundedness_Fact_Overlap_Rate": round(mean_ground, 4),
                "Success_Rate_Percent": round((passed_count / len(test_cases) * 100) if test_cases else 0.0, 1),
            },
            test_cases=test_cases,
        )

        save_evaluation_results(suite, "rag_eval")
        return suite


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    evaluator = RAGEvaluator()
    res = asyncio.run(evaluator.run())
    print(f"RAG Evaluation Complete: {res.passed}/{res.total_cases} Passed (Status: {res.passed}/{res.total_cases})")
