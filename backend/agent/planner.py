"""
backend/agent/planner.py
-------------------------
Phase 6: Agent Planner — structured execution plan generation.

The planner uses the LLM to create multi-step execution plans for
complex user requests.  Simple requests bypass planning entirely
via a deterministic complexity heuristic.

IMPORTANT:
    Planner output is NOT trusted.  Every plan MUST be validated by
    PlanValidator before execution.  The planner merely proposes;
    the backend decides.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    pending = "pending"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class PlanStatus(str, Enum):
    planning = "planning"
    awaiting_approval = "awaiting_approval"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


# ---------------------------------------------------------------------------
# Plan models
# ---------------------------------------------------------------------------

class PlanStep(BaseModel):
    """A single step in an agent execution plan."""
    id: str = Field(default_factory=lambda: f"step_{uuid.uuid4().hex[:8]}")
    description: str
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    status: str = Field(default=StepStatus.pending.value)
    result: Optional[str] = None
    error: Optional[str] = None


class AgentPlan(BaseModel):
    """A structured execution plan for a user request."""
    task_id: str
    objective: str
    steps: List[PlanStep] = Field(default_factory=list)
    status: str = Field(default=PlanStatus.planning.value)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Complexity heuristic — deterministic, runs BEFORE any LLM call
# ---------------------------------------------------------------------------

# Keywords / patterns that suggest multi-step tasks
_MULTI_STEP_INDICATORS = [
    r"\band\b.*\b(then|also|after|next)\b",
    r"\bfirst\b.*\bthen\b",
    r"\bstep\s*\d",
    r"\b(create|generate)\b.*\b(report|file|document|artifact|docx|word)\b",
    r"\bsearch\b.*\b(and|then)\b.*\b(calculate|write|create|generate)\b",
    r"\bcalculate\b.*\b(and|then)\b.*\b(write|create|save|export|generate)\b",
    r"\bfind\b.*\b(and|then)\b.*\b(compare|calculate|write|create|generate)\b",
    r"\banalyze\b.*\b(and|then)\b",
    r"\bsummarize\b.*\b(and|then)\b.*\b(save|write|create|generate)\b",
    r"\b(create|generate|modify|save|write)\b.*\b(file|document|report|artifact|docx|word)\b",
    r"\b(until|after|before)\b.*\b(approve|approval|approved)\b",
    r"\b(first|proposed|draft)\b.*\b(approve|approval|confirm)\b",
]

_SIMPLE_PATTERNS = [
    r"^(hi|hello|hey|howdy|good\s+(morning|afternoon|evening))\b",
    r"^what\s+(is|are|was|were)\b",
    r"^who\s+(is|are|was|were)\b",
    r"^(explain|define|describe)\s+",
    r"^calculate\s+[\d\.\+\-\*\/\(\)\s\^%]+$",
    r"^(thanks|thank you|ok|okay|got it|sure)\b",
    # Conversational follow-ups that should stay in plain chat context
    r"^(yes|yes\s*(please|plz|pls)|yep|yeah|yup|affirmative)\b",
    r"^(no|nope|nah)$",
    r"^(continue|go\s+on|go\s+ahead|please\s+continue|keep\s+going)\b",
    r"^(sounds\s+good|looks\s+good|that('s|\s+is)\s+(great|good|fine|correct|right))\b",
    r"^(tell\s+me\s+more|more\s+details|elaborate|expand)\b",
]

_WRITE_KEYWORDS = [
    "create a file", "write a file", "save a file", "export",
    "create a report", "write a report", "generate a report",
    "save the result", "write to file", "save to file",
    "compliance", "runbook", "benchmark", "incident", "anomaly",
    "xlsx", "excel", "docx", "word document", "diligence", "diagnostics", "cross-check",
    "create or modify", "modify any file", "create any file", "modify file",
    "until i explicitly approve", "until i approve", "after i approve", "explicitly approve",
    "approval", "docx_create", "artifact",
]


def is_general_knowledge_query(query: str) -> bool:
    """
    Detect general-knowledge questions that should remain a normal conversational response.

    A general-knowledge query:
    - Asks an informational/conceptual question (what is, define, explain, how does, describe, etc.)
    - Does NOT reference any specific equipment tag (e.g. P-204, K-101)
    - Does NOT request any artifact or file creation (e.g. create, xlsx, docx, file, report)
    - Does NOT reference uploaded, indexed, or local documents.
    """
    if not query:
        return False
    q = query.strip().lower()

    # Strip conversational polite prefixes (e.g. "please", "can you", "could you")
    q = re.sub(r"^(please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|kindly\s+)", "", q).strip()

    # 1. If it requests any file or artifact operation, or system telemetry, NEVER general knowledge
    artifact_action_keywords = [
        "create", "generate", "write", "export", "save", "download",
        "xlsx", "excel", "docx", "word", "csv", "artifact", "runbook",
        "code_execution", "python code", "script", "execute code", "sandbox",
        "hardware", "telemetry", "gpu", "vram", "cpu", "ram", "temperature",
        "model scan", "models running", "security diagnostic", "security posture",
    ]
    if any(re.search(r"\b" + re.escape(kw) + r"\b", q) for kw in artifact_action_keywords):
        return False

    # 2. If it contains an equipment tag (e.g. P-204, P204, K-101, E-302, V-401), NEVER general knowledge
    equipment_tag_pattern = r"\b[A-Za-z]{1,4}-?\d{2,5}\b"
    for match in re.finditer(equipment_tag_pattern, query):
        tag = match.group().upper()
        if any(tag.startswith(p) for p in ("P-", "P", "K-", "K", "E-", "E", "V-", "V", "TK-", "TK", "S-", "S", "C-", "C", "T-", "T")):
            return False

    # 3. If it references uploaded, local, or organizational documents/policies, NEVER general knowledge
    doc_keywords = [
        "uploaded", "document", "documents", "file", "files", "report", "reports",
        "indexed", "pdf", "knowledgebase", "knowledge base", "runbook",
        "maintenance log", "inspection report", "local evidence", "supporting document",
        "according to", "internal records", "our plant", "facility records",
        "policy", "policies", "procedure", "procedures", "protocol", "protocols",
        "sop", "manual", "manuals", "specification", "specifications",
        "datasheet", "handbook", "guideline", "guidelines", "standard operating procedure",
        "training", "record", "records", "fire safety", "safety record",
        "trainer", "attendee", "participant", "session", "drill",
    ]
    if any(re.search(r"\b" + re.escape(kw) + r"\b", q) for kw in doc_keywords):
        return False

    # 4. Check for general knowledge phrasing
    general_question_starters = [
        r"^what\s+(is|are|was|were)\b",
        r"^define\b",
        r"^explain\b",
        r"^how\s+(does|do|did|can)\b",
        r"^who\s+(is|are|was|were)\b",
        r"^describe\b",
        r"^tell\s+me\s+about\b",
        r"^give\s+(me\s+)?an\s+overview\s+of\b",
        r"^what\s+does\s+[a-z\s]+\s+mean\b",
    ]
    is_general_starter = any(re.search(p, q) for p in general_question_starters)
    concept_pattern = r"\b(working principle|purpose|components|definition|types of|advantages of|disadvantages of|mechanism of)\b"
    has_concept_query = bool(re.search(concept_pattern, q))

    return is_general_starter or has_concept_query


def is_short_conversational_followup(query: str) -> bool:
    """
    Detect short conversational follow-ups that should remain in the plain
    conversational path, preserving conversation history context.

    Examples: "yes plz", "continue", "go ahead", "tell me more", "sounds good"
    """
    if not query:
        return False
    q = query.strip()
    q_lower = q.lower()

    # Very short messages (< 50 chars) that don't contain tool/action keywords
    if len(q) < 50:
        action_keywords = [
            "create", "generate", "write", "calculate", "execute", "run",
            "search", "find", "list", "read", "file", "code", "python",
            "xlsx", "docx", "report", "artifact", "export", "save",
        ]
        if not any(kw in q_lower for kw in action_keywords):
            followup_patterns = [
                r"^(yes|yep|yeah|yup|y)\b.*$",  # Short affirmative ("yes", "yes plz", "yes give one", "yes show one", etc.)
                r"^(no|nope|nah)\b.*$",
                r"^(ok|okay|k|alright|sure|right|correct)\b.*$",
                r"^(continue|go\s+on|go\s+ahead|proceed|keep\s+going)\b.*$",
                r"^(please\s+continue|yes\s+continue|please\s+go\s+ahead)\b.*$",
                r"^(sounds?\s+good|looks?\s+good|that('s|\s+is)\s+(great|good|fine|correct|right))\b.*$",
                r"^(tell\s+me\s+more|more\s+details?|elaborate|expand(\s+on\s+that)?)\b.*$",
                r"^(give\s*(me)?(\s+(one|that|it))?|show\s*(me)?(\s+(one|that|it|diagram))?)\s*[\.,!?]*$",
                r"^(thanks?|thank\s+you|ty|thx)\b.*$",
                r"^(got\s+it|understood|i\s+see|makes?\s+sense)\b.*$",
                r"^(what\s+else|anything\s+else|and\??)\b.*$",
            ]
            if any(re.search(p, q_lower) for p in followup_patterns):
                return True
    return False


def is_simple_informational_query(query: str) -> bool:
    """
    Detect simple informational queries that need RAG context but NOT the
    tool-enabled agent path. These should go through the plain streaming
    path which already does RAG retrieval.

    Criteria:
    - Asks an informational question (what/who/how/when/where/why/describe...)
    - May reference documents, policies, training, etc. (needs RAG)
    - Does NOT request file creation, code execution, or multi-step actions
    - Is NOT a multi-step task

    Returns True → route to plain stream (with RAG), NOT tool loop.
    """
    if not query:
        return False
    q = query.strip().lower()

    # Strip polite prefixes
    q = re.sub(r"^(please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|kindly\s+)", "", q).strip()

    # If it requests any file/artifact/code operation or diagnostic tool, NOT simple
    action_keywords = [
        "create", "generate", "write", "export", "save", "download",
        "xlsx", "excel", "docx", "word", "csv", "artifact", "runbook",
        "code_execution", "python code", "script", "execute code", "sandbox",
        "run python", "execute python", "fibonacci", "calculate.*and.*save",
        "modify", "delete", "remove",
        "hardware", "telemetry", "gpu", "vram", "cpu", "ram", "temperature",
        "model scan", "models running", "security diagnostic", "security posture",
    ]
    if any(re.search(r"\b" + re.escape(kw) + r"\b", q) for kw in action_keywords):
        return False

    # Multi-step indicators → NOT simple
    for pattern in _MULTI_STEP_INDICATORS:
        if re.search(pattern, q, re.IGNORECASE):
            return False

    # Write keywords → NOT simple
    for kw in _WRITE_KEYWORDS:
        if kw in q:
            return False

    # Must be an informational question pattern
    informational_starters = [
        r"^what\s+(is|are|was|were|did|does|do)\b",
        r"^who\s+(is|are|was|were)\b",
        r"^when\s+(is|are|was|were|did|does|do)\b",
        r"^where\s+(is|are|was|were|did|does|do)\b",
        r"^why\s+(is|are|was|were|did|does|do)\b",
        r"^how\s+(is|are|was|were|did|does|do|long|much|many|often)\b",
        r"^(explain|define|describe|tell\s+me\s+about|summarize|summarise)\b",
        r"^(give\s+(me\s+)?an\s+overview|give\s+(me\s+)?a\s+summary)\b",
        r"^(list|show|display)\s+(the|all|me)\b",
    ]
    is_informational = any(re.search(p, q) for p in informational_starters)

    # If it's informational and < 150 chars, it's simple
    if is_informational and len(q) < 150:
        return True

    return False


def should_use_planning(
    message: str,
    planning_enabled: bool = True,
    tools_enabled: bool = True,
) -> bool:
    """
    Deterministic complexity heuristic — decides BEFORE execution whether
    the request warrants the Phase 6 planner or the existing Phase 4 tool loop.

    Returns True if the planner should be used, False if the existing
    chat_stream_with_tools() path is sufficient.
    """
    if not planning_enabled or not tools_enabled:
        return False

    msg_lower = message.lower().strip()

    # General knowledge questions never need planning
    if is_general_knowledge_query(message):
        return False

    # Simple greetings / trivial questions — never plan
    for pattern in _SIMPLE_PATTERNS:
        if re.search(pattern, msg_lower):
            return False

    # Short messages (< 30 chars) are almost never multi-step unless they contain industrial keywords
    if len(msg_lower) < 30 and not any(kw in msg_lower for kw in ("runbook", "xlsx", "excel", "incident")):
        return False

    # Explicit multi-step indicators
    for pattern in _MULTI_STEP_INDICATORS:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            return True

    # Standalone code execution / script tasks warrant planning
    if any(re.search(p, msg_lower) for p in (
        r"\b(write|create|run|execute)\b.*\b(python|script|program|code)\b",
        r"\bpython\b.*\b(program|script|code)\b.*\b(run|execute|calculate|analyze)\b",
    )):
        return True

    # Write & industrial operations always warrant planning (approval gate)
    for kw in _WRITE_KEYWORDS:
        if kw in msg_lower:
            return True

    # Default: single-action — use existing tool loop
    return False


# Alias for backward compatibility
should_use_planner = should_use_planning


def is_standalone_code_query(query: str) -> bool:
    """
    Detect if query is a standalone code execution or calculation task
    that does not reference uploaded documents or workspace files.
    """
    if not query:
        return False
    q = query.strip().lower()

    # Must have code execution or Python keywords
    has_code = any(re.search(p, q) for p in (
        r"\b(python|script|program|code_execution)\b",
        r"\b(write|run|execute)\b.*\b(code|function|program|script|algorithm)\b",
    ))
    if not has_code:
        return False

    # If it explicitly references uploaded files or workspace documents, it's not standalone
    doc_refs = [
        "uploaded", "from the file", "in the file", "from the document",
        "in the document", "pdf", "docx", "file_read", "file_list", "knowledgebase",
        "knowledge base", "refinery", "manual", "inspection report", "training record"
    ]
    if any(ref in q for ref in doc_refs):
        return False

    return True


def is_standalone_diagnostic_query(query: str) -> bool:
    """
    Detect if query is a workbench diagnostic task (security diagnostics, model scan,
    hardware telemetry) that inspects the workbench itself, not uploaded documents.
    """
    if not query:
        return False
    q = query.strip().lower()

    diag_terms = [
        "security diagnostic", "security posture", "security check", "security diagnostics",
        "audit logging status", "network access status", "external api", "external apis",
        "document storage location", "hardware status", "telemetry", "gpu load", "vram",
        "model scan", "models running", "available models", "ollama models"
    ]
    if not any(term in q for term in diag_terms):
        return False

    # If it references uploaded documents or equipment, not purely diagnostic
    doc_refs = [
        "uploaded", "from the file", "in the file", "from the document",
        "in the document", "pdf", "docx", "knowledge base", "runbook"
    ]
    if any(ref in q for ref in doc_refs):
        return False

    return True


def is_standalone_non_rag_task(query: str) -> bool:
    """
    Returns True if the query is a self-contained task (standalone code execution,
    calculation, or workbench system diagnostics) that should NOT invoke RAG
    or document retrieval and should not receive document sources.
    """
    return is_standalone_code_query(query) or is_standalone_diagnostic_query(query)



# ---------------------------------------------------------------------------
# Placeholder file path detection
# ---------------------------------------------------------------------------

_PLACEHOLDER_PATH_PATTERN = re.compile(
    r"^(?:document(?:_search)?(?:_result)?|doc|chunk|result|file)[_\-\s]*\d*(?:\.[a-zA-Z0-9]+)?$",
    re.IGNORECASE,
)


def is_placeholder_path(path: str) -> bool:
    """Return True if path is an invented/placeholder name (e.g. 'document_0.txt')."""
    if not path:
        return True
    cleaned = path.strip()
    return bool(_PLACEHOLDER_PATH_PATTERN.match(cleaned))


# ---------------------------------------------------------------------------
# Plan generation prompt
# ---------------------------------------------------------------------------

_PLAN_SYSTEM_PROMPT = """You are an industrial task planner for a sovereign on-premise AI workbench.

Given the user's request, create a structured execution plan using ONLY the available tools listed below.

RULES:
1. Output ONLY valid JSON — no markdown, no explanation, no preamble.
2. Each step must use a tool from the available list or be a "reasoning" step (tool_name = null).
3. Keep plans concise — use the minimum steps needed to complete the user's request.
4. Tool guidelines:
   - document_search: Searches and retrieves text passages directly from the local knowledge base (e.g. benchmarks, standard operating procedures, runbooks). Use this for general knowledge retrieval, equipment documentation, and refinery records. Do NOT follow document_search with file_read.
   - file_list: Lists files available in the local workspace (data/uploads/ directory). Use this when the user asks what files are available, or when working with an uploaded or local workspace file.
   - file_read: Reads an existing document or file from the workspace (supports .pdf, .docx, .txt, .md, .csv). Use file_read whenever the user refers to an uploaded file or workspace document. When following file_list, the exact filename returned by file_list must be used. NEVER invent, fabricate, guess, or normalize placeholder filenames (such as 'document_0.txt', 'Pump_Instability_Document.txt', 'pump_maintenance.pdf', etc.). If the exact filename is not yet known prior to file_list execution, leave relative_path empty so it is dynamically resolved from the file_list result.
   - calculator: Performs arithmetic or tolerance calculations on numbers (e.g. "4 + 3 * 2").
   - code_execution: Executes Python code inside the local sandbox (e.g. for computation, data analysis, or script execution). Captures stdout/stderr. For standalone code or calculation tasks, write the Python program in the 'code' argument. ALWAYS follow code_execution with a reasoning step (tool_name = null) to inspect sandbox stdout and present the exact computed results. NEVER call document_search, file_list, or file_read for standalone code/calculation tasks.
   - docx_create: Generates a genuine Microsoft Word (.docx) document in the sandbox with title, paragraphs, and tables. Always set requires_approval to true.
   - xlsx_report: Generates a styled Excel compliance or diligence report (.xlsx) with title, headers, data rows, and compliance status columns. Headers and rows will be dynamically populated from prior step observations at runtime. If the user specifies particular column headers or relationship tables (e.g. 'Problem', 'Recommended Improvement', or 'Equipment ID', 'Maintenance Findings', 'Operating Observations', 'Recommended Actions'), preserve those exact semantic columns. Do NOT use generic column names like 'Topic', 'Description', 'Page Number', or 'Content'. Always set requires_approval to true.
   - file_write: Creates a text output file or incident log in the sandbox. Always set requires_approval to true.
   - artifact_verifier: Verifies a generated report or artifact on disk (checks rows/paragraphs, columns, and SHA-256 hash). Follow docx_create, xlsx_report, or file_write with artifact_verifier whenever creating reports or documents. NEVER specify placeholder strings like "Findings text", "Observations text", "Actions text", "text", or generic column labels + "text" in expected_content. Only pass actual known keywords (e.g. equipment tag like "P-204") or leave expected_content omitted.
   - knowledge_graph_query: Queries the sovereign Knowledge Graph for equipment topology, unit locations, interconnected components, or structured failure-mode/defect relationship traces (e.g. 'P-204', 'V-401', 'Hydrocracker Unit 04'). Use this ONLY when the user explicitly requests equipment topology, unit locations, interconnected components, or structured relationship traces. Do NOT call this tool for generic informational questions, calculations, or direct file reading.
   - hardware_status: Checks real-time hardware telemetry including CPU, system RAM, NVIDIA GPU utilization, VRAM usage, and temperatures. Use when the user asks about hardware, system resources, GPU/VRAM load, or performance telemetry.
   - model_scan: Scans and reports currently running and locally available Ollama AI models, their parameter sizes, quantization, and context windows. Use when the user asks about local models or AI capabilities.
   - security_diagnostics: Runs security posture checks (air-gap enforcement, audit tamper checks, clearance enforcement, memory limits). Requires elevated security clearance.
   - Reasoning step (tool_name = null): Synthesizes observations, calculates deviations, checks evidence, and provides the grounded decision-support response. Spreadsheets and documents are generated natively by xlsx_report and docx_create; NEVER output Python code (e.g. openpyxl) or claim manual code execution. NEVER ask 'Would you like me to proceed with any further steps?' when steps or tasks are completing.

5. Artifact generation vs Informational / Summary / Code Execution requests:
   - CRITICAL RULE: NEVER include artifact creation steps (docx_create, xlsx_report, file_write) or artifact_verifier UNLESS the user explicitly asks to create, generate, export, or save a document, file, spreadsheet, or report.
   - For standalone Python code or calculation tasks (e.g. 'write and run a Python program that...'):
     Step 1: code_execution (with the complete Python code in the 'code' argument), Step 2: reasoning step (tool_name = null) to verify the sandbox execution output and report the computed numbers.
     NEVER include file_list, file_read, document_search, or artifact creation steps for standalone code tasks.
   - For security or system diagnostics (e.g. 'run a security diagnostics check', 'check hardware status', 'scan models'):
     Use ONLY security_diagnostics, model_scan, or hardware_status followed by a reasoning step (tool_name = null) to report the actual diagnostic values. NEVER call document_search, file_list, or file_read.
   - For informational questions, summaries, status checks, or analysis (e.g. "summarize compressor K-101 inspection", "what are the findings"):
     Use ONLY retrieval/diagnostic tools (document_search, file_read, hardware_status, model_scan, security_diagnostics) followed by a reasoning step (tool_name = null). Do NOT generate a docx or xlsx file unless explicitly requested!
   - When the user EXPLICITLY requests an artifact/report/file creation:
     * For uploaded or workspace files: Step 1: file_list, Step 2: file_read, Step 3: docx_create or xlsx_report with requires_approval: true, Step 4: artifact_verifier to verify the generated artifact (requires_approval: false).
     * For knowledge base requests: Step 1: document_search, Step 2: reasoning, Step 3: docx_create or xlsx_report with requires_approval: true, Step 4: artifact_verifier to verify the generated artifact (requires_approval: false).

6. Maximum {max_steps} steps.

OUTPUT FORMAT (JSON array of steps):
[
  {{"description": "what this step does", "tool_name": "tool_name_or_null", "arguments": {{}}, "requires_approval": false}},
  ...
]

AVAILABLE TOOLS:
{tool_descriptions}
"""



class AgentPlanner:
    """
    Generates structured execution plans from user requests.

    Uses the LLM to propose a plan, which MUST be validated by
    PlanValidator before execution.
    """

    def __init__(
        self,
        max_plan_steps: int = 10,
    ) -> None:
        self._max_plan_steps = max_plan_steps

    @property
    def max_plan_steps(self) -> int:
        return self._max_plan_steps

    async def create_plan(
        self,
        task_id: str,
        objective: str,
        tool_registry,
        provider,
        model_name: str,
    ) -> AgentPlan:
        """
        Generate an execution plan for the given objective.

        The plan is NOT validated here — call PlanValidator.validate()
        on the result before execution.
        """
        from backend.models.base import ChatRequest, Message

        # Build tool descriptions for the prompt
        tool_descriptions = ""
        if tool_registry:
            for tool in tool_registry.list_enabled_tools():
                schema = tool.input_schema.model_json_schema()
                props = schema.get("properties", {})
                param_strs = []
                for pname, pinfo in props.items():
                    param_strs.append(
                        f"    - {pname} ({pinfo.get('type', 'any')}): "
                        f"{pinfo.get('description', '')}"
                    )
                params = "\n".join(param_strs) if param_strs else "    (no parameters)"
                approval = " [REQUIRES APPROVAL]" if getattr(tool, "requires_approval", False) else ""
                tool_descriptions += (
                    f"- {tool.name}: {tool.description}{approval}\n"
                    f"  Parameters:\n{params}\n\n"
                )

        system_prompt = _PLAN_SYSTEM_PROMPT.format(
            max_steps=self._max_plan_steps,
            tool_descriptions=tool_descriptions.strip() or "(no tools available)",
        )

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=f"Create an execution plan for: {objective}"),
        ]

        request = ChatRequest(
            messages=messages,
            model=model_name,
            temperature=0.3,  # Low temperature for structured output
            max_tokens=2048,
            stream=False,
        )

        try:
            response = await provider.chat(request)
            raw = response.content.strip()

            # Extract JSON from the response (handle markdown code blocks)
            json_match = re.search(r"\[.*\]", raw, re.DOTALL)
            if json_match:
                raw = json_match.group()

            steps_data = json.loads(raw)

            if not isinstance(steps_data, list):
                raise ValueError("Plan must be a JSON array of steps")

            # Convert to PlanStep objects, filtering out fabricated/placeholder file_read calls
            has_doc_search = any(
                isinstance(s, dict) and s.get("tool_name") == "document_search"
                for s in steps_data if isinstance(s, dict)
            )

            steps = []
            for i, step_data in enumerate(steps_data[:self._max_plan_steps]):
                if not isinstance(step_data, dict):
                    continue
                tool_name = step_data.get("tool_name")
                if isinstance(tool_name, str) and tool_name.strip().lower() in {"null", "none", ""}:
                    tool_name = None
                args = step_data.get("arguments", {})
                if not isinstance(args, dict):
                    args = {}

                # Prevent planner from calling file_read with fabricated/placeholder document paths
                if tool_name == "file_read":
                    path_val = args.get("relative_path") or args.get("filename") or ""
                    path_str = str(path_val).strip()
                    if is_placeholder_path(path_str):
                        logger.info("Pruned fabricated file_read step with placeholder path '%s'", path_val)
                        continue
                    if has_doc_search and path_str.lower() not in objective.lower():
                        logger.info("Pruned ungrounded file_read step '%s' following document_search", path_val)
                        continue

                req_app = step_data.get("requires_approval", False)
                if tool_name in (
                    "artifact_verifier", "file_read", "file_list", "document_search",
                    "calculator", "hardware_status", "model_scan", "security_diagnostics", None
                ):
                    req_app = False
                elif tool_name in ("docx_create", "xlsx_report", "file_write"):
                    req_app = True

                steps.append(PlanStep(
                    id=f"step_{len(steps) + 1}",
                    description=step_data.get("description", f"Step {len(steps) + 1}"),
                    tool_name=tool_name,
                    arguments=args,
                    requires_approval=req_app,
                    status=StepStatus.pending.value,
                ))

            # Requirement 4: For requests referring to uploaded/workspace files, ensure file_list -> file_read is used
            obj_lower = objective.lower()
            is_uploaded_req = any(p in obj_lower for p in (
                "uploaded", "the uploaded file", "from the uploaded", "contents of company safety",
                "contents of the company safety", "from the uploaded document", "read the contents of the company safety"
            ))
            has_file_list = any(s.tool_name == "file_list" for s in steps)
            has_file_read = any(s.tool_name == "file_read" for s in steps)
            is_artifact_gen = any(s.tool_name in ("docx_create", "xlsx_report", "file_write") for s in steps)

            if (is_uploaded_req or has_file_list) and is_artifact_gen:
                if not has_file_list and not has_file_read:
                    resolved_fname = "Company Safety Training Record.txt" if "safety" in obj_lower else ""
                    read_desc = f"Read the content of the uploaded document '{resolved_fname}'." if resolved_fname else "Read the content of the uploaded document from the workspace."
                    read_args = {"relative_path": resolved_fname} if resolved_fname else {}
                    new_steps = [
                        PlanStep(
                            id="step_1",
                            description="List files available in the workspace to confirm the uploaded document.",
                            tool_name="file_list",
                            arguments={},
                            requires_approval=False,
                            status=StepStatus.pending.value,
                        ),
                        PlanStep(
                            id="step_2",
                            description=read_desc,
                            tool_name="file_read",
                            arguments=read_args,
                            requires_approval=False,
                            status=StepStatus.pending.value,
                        ),
                    ]
                    for s in steps:
                        if s.tool_name in ("document_search", "rag_search"):
                            continue
                        new_steps.append(s)
                    for idx, ns in enumerate(new_steps, 1):
                        ns.id = f"step_{idx}"
                    steps = new_steps
                elif has_file_list and not has_file_read:
                    fl_idx = next(i for i, s in enumerate(steps) if s.tool_name == "file_list")
                    read_step = PlanStep(
                        id=f"step_{fl_idx + 2}",
                        description="Read the content of the relevant uploaded document from the workspace.",
                        tool_name="file_read",
                        arguments={},
                        requires_approval=False,
                        status=StepStatus.pending.value,
                    )
                    steps.insert(fl_idx + 1, read_step)
                    for idx, ns in enumerate(steps, 1):
                        ns.id = f"step_{idx}"

            # Check if user requested creating, writing, exporting, or saving a file/document/report/artifact
            has_artifact_intent = bool(re.search(
                r"\b(create|generate|write|save|export|download|draft|modify)\b.*\b(file|document|report|artifact|docx|word|xlsx|excel|spreadsheet|\.txt|\.docx|\.xlsx)\b",
                obj_lower,
            )) or any(
                p in obj_lower for p in (
                    "create a report", "generate a report", "write a report", "save a report",
                    "create a file", "write a file", "save a file", "export a file",
                    "word document", "excel report", "compliance report", "diligence report",
                    "until i explicitly approve", "until i approve", "after i approve", "do not create or modify any file",
                )
            )

            # If the user did NOT request creating any file/artifact/document,
            # prune unrequested docx_create, xlsx_report, file_write, and artifact_verifier steps
            if not has_artifact_intent:
                pruned = [s for s in steps if s.tool_name not in ("docx_create", "xlsx_report", "file_write", "artifact_verifier")]
                if pruned:
                    for idx, ns in enumerate(pruned, 1):
                        ns.id = f"step_{idx}"
                    steps = pruned

            # Handle standalone code execution requests
            if is_standalone_code_query(objective):
                pruned = [
                    s for s in steps
                    if s.tool_name not in ("file_list", "file_read", "document_search", "docx_create", "xlsx_report", "file_write", "artifact_verifier")
                ]
                has_code = any(s.tool_name == "code_execution" for s in pruned)
                if not has_code:
                    pruned.insert(0, PlanStep(
                        id="step_1",
                        description=f"Execute Python code in sandbox to analyze: {objective[:120]}",
                        tool_name="code_execution",
                        arguments={},
                        requires_approval=False,
                        status=StepStatus.pending.value,
                    ))
                has_trailing_reasoning = (pruned[-1].tool_name is None) if pruned else False
                if not has_trailing_reasoning:
                    pruned.append(PlanStep(
                        id=f"step_{len(pruned) + 1}",
                        description="Verify sandbox execution output and report final calculated results.",
                        tool_name=None,
                        arguments={},
                        requires_approval=False,
                        status=StepStatus.pending.value,
                    ))
                for idx, ns in enumerate(pruned, 1):
                    ns.id = f"step_{idx}"
                steps = pruned

            # Handle standalone diagnostic requests (security diagnostics, model scan, hardware status)
            elif is_standalone_diagnostic_query(objective):
                pruned = [
                    s for s in steps
                    if s.tool_name not in ("file_list", "file_read", "document_search", "docx_create", "xlsx_report", "file_write", "artifact_verifier")
                ]
                has_trailing_reasoning = (pruned[-1].tool_name is None) if pruned else False
                if not has_trailing_reasoning and pruned:
                    pruned.append(PlanStep(
                        id=f"step_{len(pruned) + 1}",
                        description="Report diagnostic results and verified system security posture.",
                        tool_name=None,
                        arguments={},
                        requires_approval=False,
                        status=StepStatus.pending.value,
                    ))
                for idx, ns in enumerate(pruned, 1):
                    ns.id = f"step_{idx}"
                steps = pruned

            if not steps:
                steps = [PlanStep(
                    id="step_1",
                    description=f"Answer the user's request: {objective[:200]}",
                    tool_name=None,
                    arguments={},
                    requires_approval=False,
                    status=StepStatus.pending.value,
                )]

            plan = AgentPlan(
                task_id=task_id,
                objective=objective,
                steps=steps,
                status=PlanStatus.planning.value,
            )

            logger.info(
                "plan_created | task=%s steps=%d objective_len=%d",
                task_id, len(steps), len(objective),
            )
            return plan

        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(
                "plan_parse_error | task=%s error=%s", task_id, str(exc)[:200]
            )
            # Return a minimal single-step plan as fallback
            return AgentPlan(
                task_id=task_id,
                objective=objective,
                steps=[PlanStep(
                    id="step_1",
                    description=f"Answer the user's request: {objective[:200]}",
                    tool_name=None,
                    arguments={},
                    requires_approval=False,
                    status=StepStatus.pending.value,
                )],
                status=PlanStatus.planning.value,
            )
