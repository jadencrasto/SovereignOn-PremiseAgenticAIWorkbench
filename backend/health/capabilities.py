"""Passive capability derivation and validation for the explicit Ollama check."""

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Literal
import re
from urllib.parse import urlsplit

State = Literal["available", "unavailable", "blocked", "unknown"]


@dataclass(frozen=True)
class ModelObservation:
    state: State
    reason: str
    models: tuple[str, ...] = ()
    digests: tuple[tuple[str, str], ...] = ()


def loopback_tags_url(base_url: str) -> str:
    if any(ord(char) <= 32 or ord(char) == 127 for char in base_url) or "\\" in base_url:
        raise ValueError("unsafe Ollama endpoint")
    url = urlsplit(base_url)
    host = url.hostname
    if (url.scheme != "http" or not host or url.username is not None
            or url.path not in ("", "/") or "?" in base_url or "#" in base_url):
        raise ValueError("unsafe Ollama endpoint")
    # Avoid DNS entirely, including hosts-file overrides of localhost.
    address = ip_address("127.0.0.1" if host == "localhost" else host)
    if not address.is_loopback or "%" in host or url.port == 0:
        raise ValueError("unsafe Ollama endpoint")
    authority = f"[{address}]" if address.version == 6 else str(address)
    return f"http://{authority}:{url.port or 80}/api/tags"


def parse_tags(payload: object) -> tuple[str, ...]:
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list) or len(models) > 4096:
        raise ValueError("invalid model inventory")
    names = set()
    for model in models:
        name = model.get("name") if isinstance(model, dict) else None
        if (not isinstance(name, str) or not name or len(name) > 256
                or any(not char.isascii() or not (char.isalnum() or char in "._:/-") for char in name)):
            raise ValueError("invalid model name")
        names.add(name)
    return tuple(sorted(names))


def model_key(name: str) -> str:
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def parse_tag_digests(payload: object) -> tuple[tuple[str, str], ...]:
    """Capture only provider-reported SHA-256 identifiers; absence is not proof."""
    parse_tags(payload)
    observed = {}
    seen = set()
    for item in payload['models']:
        key = model_key(item['name'])
        if key in seen:
            raise ValueError('ambiguous model inventory')
        seen.add(key)
        digest = item.get('digest')
        if digest is None or digest == '':
            continue
        if not isinstance(digest, str) or not re.fullmatch(r'(?:sha256:)?[0-9a-fA-F]{64}', digest):
            raise ValueError('invalid model digest')
        observed[key] = digest.removeprefix('sha256:').lower()
    return tuple(sorted(observed.items()))


def capability_report(
    *, environment: str, ready: bool, planning: bool, persistence: bool,
    configured_models: dict[str, str | None], probe_allowed: bool,
    observation: ModelObservation | None,
) -> dict:
    def entry(state: State, reason: str) -> dict:
        return {"state": state, "reason": reason}

    if not probe_allowed:
        observation = ModelObservation("blocked", "loopback_required")
    elif observation is None:
        observation = ModelObservation("unknown", "model_check_required")

    installed = {model_key(name) for name in observation.models}
    models = {}
    for role, model in configured_models.items():
        if not model:
            availability = entry("unavailable", "model_not_configured")
        elif not model.startswith("ollama/"):
            availability = entry("unknown", "provider_not_checked")
        elif observation.state != "available":
            availability = entry(observation.state, observation.reason)
        elif model_key(model.removeprefix("ollama/")) in installed:
            availability = entry("available", "model_listed")
        else:
            availability = entry("unavailable", "configured_model_missing")
        models[role] = {"id": model, **availability}

    def workflow(initialized: bool, *roles: str) -> dict:
        if not ready or not initialized:
            return entry("unavailable", "component_not_initialized")
        for state in ("blocked", "unavailable", "unknown"):
            for role in roles:
                if models[role]["state"] == state:
                    return entry(state, models[role]["reason"])
        return entry("unknown", "prerequisites_observed")

    return {
        "schema_version": 1,
        "environment": environment,
        "deployment_verified": False,
        "document_intelligence": {
            "formats": ["PDF", "DOCX", "XLSX"],
            "extraction": "offline_verified",
            "reasoning": "offline_verified",
            "planning": "offline_verified",
            "direct_chat": "blocked",
            "artifact_publication": "blocked",
            "external_browser": "unverified",
        },
        "capabilities": {
            "backend": entry("available", "startup_complete") if ready else entry("unavailable", "startup_incomplete"),
            "ollama": entry(observation.state, observation.reason),
            "chat": workflow(True, "default_chat"),
            "planning": workflow(planning, "default_chat"),
            "conversation_persistence": (
                entry("unknown", "persistence_initialized_unverified") if ready and persistence
                else entry("unavailable", "component_not_initialized")
            ),
            "multimodal": workflow(True, "vision", "image_chat"),
            "document_rag": entry("blocked", "protected_document_storage_unverified"),
            "approval_resume": entry("blocked", "task_revalidation_unavailable"),
            "artifact_access": entry("blocked", "artifact_provenance_unverified"),
            "code_execution": entry("blocked", "execution_isolation_unverified"),
        },
        "models": models,
        "installed_models": list(observation.models),
    }
