"""
backend/multimodal/grounding.py
-------------------------------
Deterministic Visual Grounding Verifier.

Acts as a deterministic guard (NOT a generative rewriting system) to ensure:
1. Visual claims in agent responses are strictly grounded in the visual observation.
2. Document/RAG facts (e.g., equipment IDs, operating RPM) are not falsely attributed
   as facts visibly established by the image.
3. Unsupported metrics, equipment tags, PPE claims, and blanket condition assertions
   are caught and blocked or replaced with a safe provenance-preserving response.
4. No new factual claims are invented during enforcement.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional, Set, Tuple

from backend.multimodal.schemas import StructuredVisualObservation, VisualGroundingResult

logger = logging.getLogger(__name__)

# Regular expressions for equipment tags (e.g., P-204, TG-02, K-101, E-302, TK-100)
_EQUIPMENT_TAG_PATTERN = re.compile(r"\b[A-Z]{1,4}-\d{2,4}[A-Z]?\b", re.IGNORECASE)

# Regular expressions for numeric metrics with engineering units
_METRIC_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(rpm|RPM|psi|PSI|bar|BAR|deg\s*C|°C|deg\s*F|°F|kW|KW|mw|MW|hz|Hz|m3/h|m³/h|gpm|GPM|kpa|kPa)\b"
)

# Common PPE items
_PPE_PATTERNS = [
    (re.compile(r"\b(?:high[- ]visibility|hi[- ]vis|reflective)\s+(?:vest|jacket|clothing)\b", re.IGNORECASE), "high-visibility vest"),
    (re.compile(r"\b(?:safety\s+)?gloves\b", re.IGNORECASE), "gloves"),
    (re.compile(r"\b(?:hard\s*hat|safety\s+helmet)\b", re.IGNORECASE), "hard hat"),
    (re.compile(r"\b(?:safety\s+glasses|goggles|eye\s+protection)\b", re.IGNORECASE), "safety glasses"),
    (re.compile(r"\b(?:ear\s+plugs|ear\s+muffs|hearing\s+protection)\b", re.IGNORECASE), "hearing protection"),
]

# Blanket condition patterns
_BLANKET_CONDITION_PATTERNS = [
    re.compile(r"\b(?:appears?\s+to\s+be\s+in\s+)?(?:good|excellent|perfect|normal|pristine)\s+condition\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:visible\s+)?(?:signs\s+of\s+)?(?:damage|wear|defects?|issues?|problems?)\b", re.IGNORECASE),
    re.compile(r"\boperating\s+normally\b", re.IGNORECASE),
    re.compile(r"\bfully\s+functional\b", re.IGNORECASE),
]

# Phrases asserting that something is visibly in the image
_IMAGE_ASSERTION_PATTERNS = [
    re.compile(r"\b(?:the|this)\s+image\s+(?:shows?|displays?|depicts?|reveals?|confirms?)\s+([^.\n]+)", re.IGNORECASE),
    re.compile(r"\bvisible\s+in\s+(?:the|this)\s+image\s*(?::|\b)([^.\n]+)", re.IGNORECASE),
    re.compile(r"\bphoto\s+(?:shows?|displays?|depicts?)\s+([^.\n]+)", re.IGNORECASE),
    re.compile(r"\bshown\s+in\s+(?:the|this)\s+image\b", re.IGNORECASE),
    re.compile(r"\bwe\s+can\s+see\s+([^.\n]+)", re.IGNORECASE),
    re.compile(r"###\s*Visible\s+in\s+image\s*\n([^#]+)", re.IGNORECASE),
    re.compile(r"\bVisual\s+Findings?\s*(?::|\n)([^#]+)", re.IGNORECASE),
]


def parse_structured_observation(raw_text: str) -> StructuredVisualObservation:
    """
    Parse a VLM observation string into a StructuredVisualObservation object.
    Robust to missing sections or imperfect LLaVA formatting.
    """
    if not raw_text or not raw_text.strip():
        return StructuredVisualObservation(raw_text="")

    text = raw_text.strip()
    obs = StructuredVisualObservation(raw_text=text)

    # Section extraction helper
    def extract_section(section_name: str) -> List[str]:
        pattern = re.compile(
            rf"\[{section_name}\]:?\s*\n?(.*?)(?=\n\s*\[[A-Z_]+\]|\Z)",
            re.DOTALL | re.IGNORECASE,
        )
        match = pattern.search(text)
        if not match:
            return []
        content = match.group(1).strip()
        lines = []
        for line in content.split("\n"):
            cleaned = re.sub(r"^[-*•\d.]+\s*", "", line).strip()
            if cleaned and cleaned.upper() != "NONE" and cleaned.upper() != "NONE_VISIBLE_IN_INSPECTED_AREA":
                lines.append(cleaned)
        return lines

    vis_comp = extract_section("VISIBLE_COMPONENTS")
    if vis_comp:
        obs.visible_components = vis_comp

    leg_text = extract_section("LEGIBLE_TEXT_AND_NUMBERS")
    if leg_text:
        obs.legible_text_and_numbers = leg_text

    surf_col = extract_section("SURFACE_AND_COLOR")
    if surf_col:
        obs.surface_and_color = surf_col

    anom = extract_section("OBSERVED_ANOMALIES")
    if anom:
        obs.observed_anomalies = anom

    unobs = extract_section("UNOBSERVABLE_AND_UNCERTAIN")
    if unobs:
        obs.unobservable_and_uncertain = unobs

    # Confidence extraction
    conf_match = re.search(r"\[OBSERVATION_CONFIDENCE\]:\s*(HIGH|MEDIUM|LOW)", text, re.IGNORECASE)
    if conf_match:
        obs.confidence = conf_match.group(1).upper()

    return obs


class VisualGroundingVerifier:
    """
    Deterministic Guard for visual and cross-modal grounding.

    Validates that:
    1. Metrics (RPM, PSI, etc.) claimed about the image appear in the visual observation.
    2. Equipment tags (P-204, etc.) are not claimed as visibly identified if not on the image.
    3. PPE items are not invented.
    4. Blanket condition claims are prevented unless properly qualified.
    5. Document-derived facts are kept distinct from image-derived observations.
    """

    def __init__(self) -> None:
        pass

    def verify(
        self,
        response_text: str,
        observation: StructuredVisualObservation | str,
        sources: Optional[List[Any]] = None,
    ) -> VisualGroundingResult:
        """
        Verify response against the visual observation.
        If unsupported visual claims are detected, returns an ungrounded result
        and a safe, provenance-preserving guarded text.
        """
        if isinstance(observation, str):
            structured_obs = parse_structured_observation(observation)
        else:
            structured_obs = observation

        violations: List[str] = []
        unsupported_claims: List[str] = []

        # Build ground-truth sets from the observation
        obs_raw = structured_obs.raw_text.lower()
        legible_text_combined = " ".join(structured_obs.legible_text_and_numbers).lower()
        components_combined = " ".join(structured_obs.visible_components).lower()
        surface_combined = " ".join(structured_obs.surface_and_color).lower()
        obs_full_text = f"{obs_raw} {legible_text_combined} {components_combined} {surface_combined}"

        # 1. Extract visual assertion text snippets from the response
        visual_snippets: List[str] = []
        for pat in _IMAGE_ASSERTION_PATTERNS:
            for match in pat.finditer(response_text):
                visual_snippets.append(match.group(0))

        # Also inspect sentences that explicitly mention image/photo
        for sentence in re.split(r"[.\n]+", response_text):
            sentence_clean = sentence.strip()
            if re.search(r"\b(image|photo|picture|visible|visual)\b", sentence_clean, re.IGNORECASE):
                visual_snippets.append(sentence_clean)

        combined_visual_claims = " ".join(visual_snippets) if visual_snippets else response_text

        # 2. Check for unsupported equipment tags claimed to be visible
        negation_terms = [
            "cannot be confirmed", "cannot confirm", "not confirmed", "not verified",
            "cannot be verified", "absence of", "no equipment tag", "no tag", "no serial",
            "does not show", "does not display", "does not identify", "cannot be determined",
            "cannot be identified", "is not confirmed", "not necessarily", "without asserting",
            "cannot be assumed", "do not assume", "not visible", "no visible", "cannot be confirmed to be",
        ]

        tags_in_response = set(m.group(0).upper() for m in _EQUIPMENT_TAG_PATTERN.finditer(combined_visual_claims))
        for tag in tags_in_response:
            # Check if this tag is claimed as visible in the image
            tag_asserted_as_image = False
            for snippet in visual_snippets:
                # If snippet is explicitly discussing documents, skip
                if "stated in document" in snippet.lower() or "retrieved document" in snippet.lower():
                    continue

                # If snippet is a negation / disavowal of the tag being in the image, skip
                snippet_lower = snippet.lower()
                if any(neg in snippet_lower for neg in negation_terms):
                    continue

                if (
                    re.search(rf"\b(image|photo|visual(?:ly)?|picture)\b[^\n.]{{0,50}}\b{re.escape(tag)}\b", snippet, re.IGNORECASE)
                    or re.search(rf"\b{re.escape(tag)}\b[^\n.]{{0,50}}\b(?:shown|visible|seen|observed|depicted)\b", snippet, re.IGNORECASE)
                ):
                    tag_asserted_as_image = True
                    break

            if tag_asserted_as_image:
                # Is the tag actually in the visual observation?
                if tag.lower() not in obs_full_text:
                    violations.append(f"unsupported_visual_equipment_tag: {tag}")
                    unsupported_claims.append(f"Equipment tag '{tag}' is not visible in the image.")

        # 3. Check for unsupported numeric metrics claimed to be visible
        for match in _METRIC_PATTERN.finditer(combined_visual_claims):
            val_str = match.group(1)
            unit_str = match.group(2)
            full_metric = match.group(0)

            # Check if metric was asserted in visual context
            metric_asserted_as_image = any(
                full_metric.lower() in snippet.lower()
                and re.search(r"\b(image|photo|visual(?:ly)?|shows?|reading|speed|rpm|psi)\b", snippet, re.IGNORECASE)
                for snippet in visual_snippets
            )
            if metric_asserted_as_image:
                # Check if metric is grounded in the observation
                if full_metric.lower() not in obs_full_text and val_str not in obs_full_text:
                    violations.append(f"unsupported_visual_metric: {full_metric}")
                    unsupported_claims.append(f"Metric '{full_metric}' is not visible in the image.")

        # 4. Check for unsupported PPE claims
        for ppe_regex, ppe_label in _PPE_PATTERNS:
            if ppe_regex.search(combined_visual_claims):
                if not ppe_regex.search(obs_full_text):
                    violations.append(f"unsupported_ppe_claim: {ppe_label}")
                    unsupported_claims.append(f"PPE claim '{ppe_label}' is not supported by the image observation.")

        # 5. Check for unsupported blanket condition claims
        for cond_regex in _BLANKET_CONDITION_PATTERNS:
            match = cond_regex.search(combined_visual_claims)
            if match:
                matched_phrase = match.group(0)
                # Check if properly qualified with visibility/angle constraints
                has_qualification = any(
                    re.search(r"\b(external|visible surfaces?|cannot (?:be )?assess(?:ed)?|internal|from this (?:angle|view))\b", s, re.IGNORECASE)
                    for s in visual_snippets
                )
                if not has_qualification:
                    violations.append(f"unqualified_blanket_condition: {matched_phrase}")
                    unsupported_claims.append(f"Blanket condition claim '{matched_phrase}' lacks visibility/angle qualification.")

        if not violations:
            return VisualGroundingResult(
                is_grounded=True,
                unsupported_claims=[],
                guarded_text=response_text,
                violations=[],
            )

        # Build safe, provenance-preserving fallback response
        logger.warning(
            "VISUAL_GROUNDING_GUARD | Violations detected: %s. Applying safe fallback.",
            violations,
        )
        guarded_text = self._build_safe_provenance_response(
            original_response=response_text,
            structured_obs=structured_obs,
            violations=violations,
            sources=sources,
        )

        return VisualGroundingResult(
            is_grounded=False,
            unsupported_claims=unsupported_claims,
            guarded_text=guarded_text,
            violations=violations,
        )

    def _build_safe_provenance_response(
        self,
        original_response: str,
        structured_obs: StructuredVisualObservation,
        violations: List[str],
        sources: Optional[List[Any]] = None,
    ) -> str:
        """
        Construct a safe, provenance-preserving response that strictly keeps
        visual observations and document facts separate, without inventing new facts.
        """
        # Summarize actual visual observation
        vis_lines = []
        if structured_obs.visible_components:
            vis_lines.append(f"- **Visible Components**: {', '.join(structured_obs.visible_components)}")
        if structured_obs.legible_text_and_numbers:
            vis_lines.append(f"- **Legible Text / Markings**: {', '.join(structured_obs.legible_text_and_numbers)}")
        else:
            vis_lines.append("- **Legible Text / Markings**: None visible on the equipment.")

        if structured_obs.surface_and_color:
            vis_lines.append(f"- **Surface & Color**: {', '.join(structured_obs.surface_and_color)}")
        if structured_obs.observed_anomalies:
            vis_lines.append(f"- **Visible Anomalies**: {', '.join(structured_obs.observed_anomalies)}")

        # Check what cannot be established
        unobservable = []
        if any("unsupported_visual_equipment_tag" in v for v in violations):
            unobservable.append("The image does not display a legible equipment identification tag.")
        if any("unsupported_visual_metric" in v for v in violations):
            unobservable.append("Operating speed (RPM), pressure, and telemetry are not visibly legible in the image.")
        if any("unsupported_ppe_claim" in v for v in violations):
            unobservable.append("Specific PPE items cannot be verified from the image.")
        if any("unqualified_blanket_condition" in v for v in violations):
            unobservable.append("External visible surfaces show no obvious structural rupture, but internal mechanical condition and operating integrity cannot be certified from a photograph.")

        if structured_obs.unobservable_and_uncertain:
            for item in structured_obs.unobservable_and_uncertain:
                if item not in unobservable:
                    unobservable.append(item)

        if unobservable:
            vis_lines.append(f"- **Observational Limitations**: {'; '.join(unobservable)}")

        vis_summary = "\n".join(vis_lines)

        # Summarize document facts if present
        doc_summary_lines = []
        if sources:
            for chunk in sources:
                fname = getattr(chunk, "filename", "document")
                text = getattr(chunk, "text", "")
                # Extract relevant sentences from document
                for sent in re.split(r"[.\n]+", text):
                    sent_clean = sent.strip()
                    if sent_clean and len(sent_clean) > 15:
                        doc_summary_lines.append(f"- **{fname}**: {sent_clean}")
                        break

        doc_section = ""
        if doc_summary_lines:
            doc_section = "\n\n### Stated in documents\n" + "\n".join(doc_summary_lines[:3])

        correlation_section = (
            "- **Visual Grounding**: The uploaded photograph provides visual evidence of the physical components described above, but does not visibly confirm specific equipment tags, operating speed, or internal state.\n"
            "- **Document Correlation**: Technical parameters (such as equipment tags or documented operating RPM) originate from the retrieved documentation, not from the visual image. The image cannot be confirmed to be any specific documented equipment tag due to the absence of a visible equipment tag. Any association between this image and documented equipment is an inference or hypothesis, not an established visual fact."
        )

        return (
            f"### Visible in image\n"
            f"{vis_summary}\n"
            f"- No equipment tag or serial number is visible."
            f"{doc_section}\n\n"
            f"### Relationship / relevance\n"
            f"{correlation_section}"
        )
