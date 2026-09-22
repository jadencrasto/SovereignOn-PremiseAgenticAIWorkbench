"""
backend/multimodal/schemas.py
-----------------------------
Structured schemas for visual observations and multimodal grounding verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class StructuredVisualObservation:
    """
    Structured representation of a VLM (e.g., LLaVA) observation.

    Note: This is an UNTRUSTED model-generated extraction, not objective ground truth.
    """
    visible_components: List[str] = field(default_factory=list)
    legible_text_and_numbers: List[str] = field(default_factory=list)
    surface_and_color: List[str] = field(default_factory=list)
    observed_anomalies: List[str] = field(default_factory=list)
    unobservable_and_uncertain: List[str] = field(default_factory=list)
    confidence: str = "MEDIUM"
    raw_text: str = ""

    def to_formatted_text(self) -> str:
        """Convert structured fields to a clean, delimited text block."""
        parts = []

        components = "\n".join(f"- {c}" for c in self.visible_components) if self.visible_components else "- None explicitly identified"
        parts.append(f"[VISIBLE_COMPONENTS]:\n{components}")

        text_items = "\n".join(f"- {t}" for t in self.legible_text_and_numbers) if self.legible_text_and_numbers else "- NONE"
        parts.append(f"[LEGIBLE_TEXT_AND_NUMBERS]:\n{text_items}")

        surfaces = "\n".join(f"- {s}" for s in self.surface_and_color) if self.surface_and_color else "- None described"
        parts.append(f"[SURFACE_AND_COLOR]:\n{surfaces}")

        anomalies = "\n".join(f"- {a}" for a in self.observed_anomalies) if self.observed_anomalies else "- NONE_VISIBLE_IN_INSPECTED_AREA"
        parts.append(f"[OBSERVED_ANOMALIES]:\n{anomalies}")

        unobservable = "\n".join(f"- {u}" for u in self.unobservable_and_uncertain) if self.unobservable_and_uncertain else "- None reported"
        parts.append(f"[UNOBSERVABLE_AND_UNCERTAIN]:\n{unobservable}")

        parts.append(f"[OBSERVATION_CONFIDENCE]: {self.confidence.upper()}")
        return "\n\n".join(parts)


@dataclass
class VisualGroundingResult:
    """Result of visual grounding verification against an observation."""
    is_grounded: bool
    unsupported_claims: List[str] = field(default_factory=list)
    guarded_text: str = ""
    violations: List[str] = field(default_factory=list)
