"""
eval/common.py
--------------
Phase 8: Evaluation framework core types, metric calculators,
environment probes, and result persistence helpers.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("eval")

EVAL_DIR = Path(__file__).parent
RESULTS_DIR = EVAL_DIR / "results"
DATA_DIR = EVAL_DIR / "data"
DEMO_DATA_DIR = EVAL_DIR.parent / "data" / "demo"


class EvalStatus:
    PASS = "PASS"
    FAIL = "FAIL"
    ENVIRONMENT_UNAVAILABLE = "ENVIRONMENT_UNAVAILABLE"


@dataclass
class TestCaseResult:
    __test__ = False
    test_id: str
    name: str
    category: str
    status: str  # PASS, FAIL, ENVIRONMENT_UNAVAILABLE
    duration_ms: float
    metrics: Dict[str, Any] = field(default_factory=dict)
    details: Optional[str] = None
    error: Optional[str] = None


@dataclass
class EvaluationSuiteResult:
    __test__ = False
    suite_name: str
    timestamp: str
    environment: str
    total_cases: int
    passed: int
    failed: int
    environment_unavailable: int
    duration_seconds: float
    summary_metrics: Dict[str, Any] = field(default_factory=dict)
    test_cases: List[TestCaseResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def check_ollama_available(base_url: str = "http://localhost:11434", timeout: float = 2.0) -> Tuple[bool, List[str]]:
    """
    Check if local Ollama instance is running and retrieve loaded model tags.
    Returns (is_available, model_names).
    """
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base_url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                return True, models
    except Exception:
        pass
    return False, []


def get_environment_info() -> Dict[str, Any]:
    """Capture environment metadata for evaluation reporting."""
    ollama_ok, models = check_ollama_available()
    return {
        "os": os.name,
        "python_version": os.sys.version.split()[0],
        "ollama_available": ollama_ok,
        "available_models": models,
        "air_gapped": True,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def save_evaluation_results(suite_result: EvaluationSuiteResult, filename_prefix: str) -> Tuple[Path, Path]:
    """
    Save evaluation results in both structured JSON and formatted Markdown.
    Returns (json_path, markdown_path).
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # 1. JSON output
    json_filename = f"{filename_prefix}_{timestamp_slug}.json"
    latest_json = RESULTS_DIR / f"{filename_prefix}_latest.json"
    json_path = RESULTS_DIR / json_filename

    data = asdict(suite_result)
    json_content = json.dumps(data, indent=2)
    json_path.write_text(json_content, encoding="utf-8")
    latest_json.write_text(json_content, encoding="utf-8")

    # 2. Markdown summary
    md_filename = f"{filename_prefix}_{timestamp_slug}.md"
    latest_md = RESULTS_DIR / f"{filename_prefix}_latest.md"
    md_path = RESULTS_DIR / md_filename

    lines = [
        f"# Evaluation Report: {suite_result.suite_name}",
        f"**Timestamp:** {suite_result.timestamp} UTC  ",
        f"**Environment:** {suite_result.environment}  ",
        f"**Total Duration:** {suite_result.duration_seconds:.2f}s  ",
        "",
        "## Summary Scorecard",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| **Total Test Cases** | `{suite_result.total_cases}` |",
        f"| **Passed** | `{suite_result.passed}` |",
        f"| **Failed** | `{suite_result.failed}` |",
        f"| **Environment Unavailable** | `{suite_result.environment_unavailable}` |",
    ]

    for k, v in suite_result.summary_metrics.items():
        val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
        lines.append(f"| **{k}** | `{val_str}` |")

    lines.extend([
        "",
        "## Detailed Test Cases",
        "",
        "| ID | Test Name | Category | Status | Latency (ms) | Details |",
        "|---|---|---|---|---|---|",
    ])

    for tc in suite_result.test_cases:
        status_badge = f"**{tc.status}**"
        details_escaped = (tc.details or "").replace("|", "\\|")
        lines.append(
            f"| `{tc.test_id}` | {tc.name} | {tc.category} | {status_badge} | {tc.duration_ms:.1f} | {details_escaped} |"
        )

    if suite_result.errors:
        lines.extend([
            "",
            "## Errors & Diagnostics",
            "",
        ])
        for err in suite_result.errors:
            lines.append(f"- {err}")

    md_content = "\n".join(lines) + "\n"
    md_path.write_text(md_content, encoding="utf-8")
    latest_md.write_text(md_content, encoding="utf-8")

    return json_path, md_path
