"""
eval/run_all.py
---------------
Phase 8: Master Evaluation Runner.

Executes all 8 Phase 8 evaluation benchmarks in sequence:
  1. RAG Retrieval & Groundedness Benchmark (rag_eval)
  2. Agent Reliability & Fault Injection Benchmark (agent_reliability_eval)
  3. Security & Enterprise Hardening Benchmark (security_eval)
  4. Tool Execution & Authorization Benchmark (tool_eval)
  5. Latency & Performance Benchmark (performance_eval)
  6. Baseline vs System Comparative Benchmark (baseline_eval)
  7. Multimodal Vision Benchmark (multimodal_eval)
  8. End-to-End Industrial Report Generation Workflow (report_eval)

Generates consolidated JSON and Markdown scorecards in eval/results/.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from eval.agent_reliability_eval import AgentReliabilityEvaluator
from eval.baseline_eval import BaselineEvaluator
from eval.common import (
    RESULTS_DIR,
    EvalStatus,
    EvaluationSuiteResult,
    check_ollama_available,
    get_environment_info,
)
from eval.multimodal_eval import MultimodalEvaluator
from eval.performance_eval import PerformanceEvaluator
from eval.rag_eval import RAGEvaluator
from eval.report_eval import ReportWorkflowEvaluator
from eval.security_eval import SecurityEvaluator
from eval.tool_eval import ToolEvaluator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("eval.master")


def generate_consolidated_report(
    suite_results: List[EvaluationSuiteResult],
    env_info: Dict[str, Any],
    total_duration_sec: float,
    expected_suites: int = 8,
) -> Tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    total_cases = sum(s.total_cases for s in suite_results)
    total_passed = sum(s.passed for s in suite_results)
    total_failed = sum(s.failed for s in suite_results)
    total_unavail = sum(s.environment_unavailable for s in suite_results)

    if total_failed:
        overall_status = "FAIL"
    elif not suite_results or total_cases == 0 or total_passed == 0:
        overall_status = "BLOCKED"
    elif len(suite_results) != expected_suites or total_unavail or total_passed != total_cases:
        overall_status = "INCOMPLETE"
    else:
        overall_status = "PASS"

    consolidated_data = {
        "title": "Sovereign AI Workbench — Phase 8 Evaluation Scorecard",
        "timestamp": timestamp_str,
        "overall_status": overall_status,
        "total_duration_seconds": round(total_duration_sec, 2),
        "environment": env_info,
        "summary": {
            "total_cases": total_cases,
            "passed": total_passed,
            "failed": total_failed,
            "environment_unavailable": total_unavail,
            "pass_rate_runnable_percent": round(
                (total_passed / (total_cases - total_unavail) * 100) if (total_cases - total_unavail) > 0 else 0.0, 1
            ),
        },
        "suites": [
            {
                "name": s.suite_name,
                "total": s.total_cases,
                "passed": s.passed,
                "failed": s.failed,
                "unavailable": s.environment_unavailable,
                "duration_seconds": round(s.duration_seconds, 2),
                "metrics": s.summary_metrics,
            }
            for s in suite_results
        ],
    }

    # 1. Write Consolidated JSON
    json_path = RESULTS_DIR / f"consolidated_report_{timestamp_slug}.json"
    latest_json = RESULTS_DIR / "consolidated_report_latest.json"
    json_str = json.dumps(consolidated_data, indent=2)
    json_path.write_text(json_str, encoding="utf-8")
    latest_json.write_text(json_str, encoding="utf-8")

    # 2. Write Consolidated Markdown
    md_path = RESULTS_DIR / f"consolidated_report_{timestamp_slug}.md"
    latest_md = RESULTS_DIR / "consolidated_report_latest.md"

    md_lines = [
        "# Sovereign AI Workbench — Phase 8 Evaluation Scorecard",
        f"**Generated:** {timestamp_str}  ",
        f"**Overall Status:** `{overall_status}`  ",
        "**Network isolation:** Not verified by this evaluation.  ",
        f"**Total Execution Time:** {total_duration_sec:.2f}s  ",
        "",
        "---",
        "",
        "## 1. Executive Evaluation Summary",
        "",
        "| Metric | Count | Percentage |",
        "|---|---|---|",
        f"| **Total Evaluation Cases** | `{total_cases}` | 100.0% |",
        f"| **Passed (Verified)** | `{total_passed}` | {round(total_passed/total_cases*100, 1) if total_cases else 0}% |",
        f"| **Failed (Regressions)** | `{total_failed}` | {round(total_failed/total_cases*100, 1) if total_cases else 0}% |",
        f"| **Environment Unavailable (Offline LLM)** | `{total_unavail}` | {round(total_unavail/total_cases*100, 1) if total_cases else 0}% |",
        "",
        "---",
        "",
        "## 2. Evaluation Suite Breakdown",
        "",
        "| Suite Name | Total | Passed | Failed | Unavail | Duration (s) | Key Benchmark Metrics |",
        "|---|---|---|---|---|---|---|",
    ]

    for s in suite_results:
        metrics_str = "<br>".join([f"{k}: `{v}`" for k, v in s.summary_metrics.items()])
        md_lines.append(
            f"| **{s.suite_name}** | {s.total_cases} | {s.passed} | {s.failed} | {s.environment_unavailable} | {s.duration_seconds:.2f} | {metrics_str} |"
        )

    md_lines.extend([
        "",
        "---",
        "",
        "## 3. Environment & Deployment Diagnostics",
        "",
        f"- **Operating System:** `{env_info.get('os')}`",
        f"- **Python Version:** `{env_info.get('python_version')}`",
        f"- **Local Ollama Online:** `{env_info.get('ollama_available')}`",
        f"- **Loaded Local Models:** `{', '.join(env_info.get('available_models', [])) or 'None (Offline Mode)'}`",
        "- **Data Sovereignty Assurance:** Not established; verify network isolation separately.",
        "",
    ])

    md_str = "\n".join(md_lines) + "\n"
    md_path.write_text(md_str, encoding="utf-8")
    latest_md.write_text(md_str, encoding="utf-8")

    return json_path, md_path


async def main() -> int:
    t_all = time.monotonic()
    print("=" * 75)
    print(" SOVEREIGN ON-PREMISE AGENTIC AI WORKBENCH — PHASE 8 MASTER EVALUATION")
    print("=" * 75)

    env_info = get_environment_info()
    print(f"[*] Environment Detected: Python {env_info['python_version']} on {env_info['os']}")
    print(f"[*] Ollama Instance: {'ONLINE (' + str(len(env_info['available_models'])) + ' models)' if env_info['ollama_available'] else 'UNAVAILABLE'}")
    print("-" * 75)

    suite_evaluators = [
        ("Agent Reliability & Fault Injection", AgentReliabilityEvaluator()),
        ("Security & Enterprise Hardening", SecurityEvaluator()),
        ("Tool Execution & Authorization", ToolEvaluator()),
        ("Performance & Latency", PerformanceEvaluator()),
        ("Baseline vs System Comparison", BaselineEvaluator()),
        ("Multimodal Vision Inspection", MultimodalEvaluator()),
        ("Industrial Report Workflow", ReportWorkflowEvaluator()),
        ("RAG Industrial Retrieval", RAGEvaluator()),
    ]

    suite_results: List[EvaluationSuiteResult] = []

    for name, evaluator in suite_evaluators:
        print(f"\n>>> Running Suite: {name}...")
        try:
            res = await evaluator.run()
            suite_results.append(res)
            print(f"    [DONE] {res.passed}/{res.total_cases} Passed | {res.failed} Failed | {res.environment_unavailable} Unavail ({res.duration_seconds:.2f}s)")
        except Exception as exc:
            logger.exception("Suite %s crashed: %s", name, exc)
            print(f"    [CRASH] {name}: {exc}")

    total_duration = time.monotonic() - t_all
    json_out, md_out = generate_consolidated_report(suite_results, env_info, total_duration)

    print("\n" + "=" * 75)
    print(" CONSOLIDATED EVALUATION COMPLETED")
    print("=" * 75)
    print(f"[*] Consolidated JSON Report: {json_out.resolve()}")
    print(f"[*] Consolidated Markdown Scorecard: {md_out.resolve()}")

    result_data = json.loads(json_out.read_text(encoding="utf-8"))
    if result_data["overall_status"] != "PASS":
        print(f"\n[!] Evaluation status: {result_data['overall_status']}. Non-zero exit.")
        return 1

    print("\n[OK] ALL RUNNABLE EVALUATIONS PASSED WITH ZERO REGRESSIONS.")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
