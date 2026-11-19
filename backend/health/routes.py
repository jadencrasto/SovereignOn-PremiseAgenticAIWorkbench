"""Passive application readiness and explicitly requested Ollama observation."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import secrets
import time
from contextlib import aclosing

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.auth.dependencies import bearer_scheme, get_current_user, request_session_credentials, require_permission
from backend.auth.request_context import dispatch_identity_resolver
from backend.auth.models import Permission
from backend.config import settings
from backend.health.capabilities import ModelObservation, capability_report, loopback_tags_url, parse_tags, parse_tag_digests, model_key
from backend.schemas.chat import HealthResponse

router = APIRouter(prefix="/api/health", tags=["health"])
MODEL_CHECK_TTL = 60
MODEL_CHECK_TIMEOUT = 2.5
MAX_TAGS_BYTES = 1024 * 1024
INFERENCE_TTL = 60
INFERENCE_TIMEOUT = 30
MAX_INFERENCE_BYTES = 16384


class InferenceCheck(BaseModel):
    model: str = Field(min_length=1, max_length=256)


def _identity(state, model: str, capability: str = 'chat'):
    """Only configured Ollama models with the requested capability can be checked."""
    target = _tags_url(state)
    router_obj = getattr(state, 'model_router', None)
    if not target or not router_obj or not model.startswith('ollama/'):
        return None
    cached = getattr(router_obj, '_provider_cache', {}).get('ollama')
    if cached is not None and getattr(cached, '_base_url', None) != target.removesuffix('/api/tags'):
        return None
    name = model.removeprefix('ollama/')
    if not name:
        return None
    try:
        # Reuse inventory name validation rather than accepting arbitrary provider paths.
        parse_tags({'models': [{'name': name}]})
        key = model_key(name)
        matches = [(mid, conf) for mid, conf in router_obj._config.get('models', {}).items()
                   if mid.startswith('ollama/') and model_key(mid.removeprefix('ollama/')) == key]
        if len(matches) != 1 or capability not in matches[0][1].get('capabilities', []):
            return None
        # Changes to the configured model metadata invalidate evidence.
        config = json.dumps(matches[0][1], sort_keys=True, separators=(',', ':'))
        return (target, key, config)
    except (ValueError, TypeError, AttributeError):
        return None


def _settings(state):
    return getattr(state, "settings", settings)


def _tags_url(state) -> str | None:
    try:
        return loopback_tags_url(_settings(state).ollama_base_url)
    except ValueError:
        return None


def _observed_digest(observation, key):
    return dict(observation.digests).get(key) if observation else None


def _current_inventory(state):
    recorded = getattr(state, "model_observation", None)
    if recorded and recorded[0] == _tags_url(state) and (
            getattr(_settings(state), 'is_local_dev', False) or time.monotonic() - recorded[1] < MODEL_CHECK_TTL):
        return recorded
    return None


def model_readiness(state, selected: str, capability: str = 'chat') -> dict:
    recorded = _current_inventory(state)
    observation = recorded[2] if recorded else None
    identity = _identity(state, selected, capability)
    digest = _observed_digest(observation, identity[1]) if identity else None
    status = {'state': 'unknown', 'reason': 'inference_not_checked'}
    if getattr(_settings(state), 'is_local_dev', False):
        if capability != 'chat':
            status = {'state': 'blocked', 'reason': 'local_text_chat_only'}
        elif identity is None:
            status = {'state': 'unavailable', 'reason': 'unsupported_chat_model'}
        elif observation is None:
            status = {'state': 'unknown', 'reason': 'model_check_required'}
        elif observation.state != 'available':
            status = {'state': 'unavailable', 'reason': observation.reason}
        elif not digest:
            status = {'state': 'unavailable', 'reason': 'configured_model_missing'}
        else:
            status = {'state': 'available', 'reason': 'model_listed'}
    elif identity is None:
        status = {'state': 'unavailable', 'reason': f'unsupported_{capability}_model'}
    elif observation is None or observation.state != 'available':
        status = {'state': 'unknown', 'reason': 'model_check_required'}
    elif identity[1] not in {model_key(n) for n in observation.models}:
        status = {'state': 'unavailable', 'reason': 'configured_model_missing'}
    elif capability == 'vision' and not digest:
        status = {'state': 'unknown', 'reason': 'vision_model_digest_required'}
    elif (getattr(state, 'inference_check_in_progress', None) == identity
          and getattr(state, 'inference_check_capability', 'chat') == capability):
        status = {'state': 'unknown', 'reason': 'inference_check_in_progress'}
    else:
        record = getattr(state, 'vision_observation' if capability == 'vision' else 'inference_observation', None)
        if record and record[0] == identity:
            recorded_digest = record[3] if len(record) > 3 else None
            if recorded_digest != digest:
                status = {'state': 'unknown', 'reason': 'model_identity_changed'}
            elif time.monotonic() - record[1] >= INFERENCE_TTL:
                status = {'state': 'unknown', 'reason': 'inference_check_expired'}
            else:
                status = {'state': 'available' if record[2] else 'unavailable',
                          'reason': 'inference_verified' if record[2] else 'inference_check_failed'}
                if record[2]:
                    remaining = min(INFERENCE_TTL - (time.monotonic() - record[1]),
                                    MODEL_CHECK_TTL - (time.monotonic() - recorded[1]))
                    status['expires_at'] = time.time() + max(0, remaining)
    if not getattr(state, 'startup_complete', False):
        status = {'state': 'unavailable', 'reason': 'component_not_initialized'}
    return {'id': selected, **status, 'identity_evidence': 'provider_digest' if digest else 'unavailable'}


def _report(state, selected_model: str | None = None, vision_model: str | None = None) -> dict:
    from backend.multimodal.text_file import text_understanding_enabled

    cfg = _settings(state)
    model_router = getattr(state, "model_router", None)
    default = model_router.default_model_id if model_router else f"ollama/{cfg.ollama_default_model}"
    default_vision = model_router.configured_model_id("vision") if model_router else None
    recorded = _current_inventory(state)
    observation = recorded[2] if recorded else None
    report = capability_report(
        environment=cfg.app_env,
        ready=getattr(state, "startup_complete", False),
        planning=getattr(state, "planner", None) is not None,
        persistence=getattr(state, "conversation_persistence_initialized", False),
        configured_models={
            "default_chat": default,
            "image_chat": model_router.configured_model_id("chat") if model_router else None,
            "vision": default_vision,
            "embedding": f"ollama/{cfg.embedding_model}" if cfg.embedding_model else None,
        },
        probe_allowed=_tags_url(state) is not None,
        observation=observation,
    )
    chat = model_readiness(state, selected_model or default)
    vision = model_readiness(state, vision_model or default_vision or '', 'vision')
    report['selected_chat_model'] = chat
    report['text_file_understanding'] = text_understanding_enabled(cfg)
    report['selected_vision_model'] = vision
    installed = observation.models if observation and observation.state == 'available' else ()
    for capability in ('chat', 'vision'):
        report[f'{capability}_models'] = [f'ollama/{name}' for name in installed
                                         if _identity(state, f'ollama/{name}', capability) is not None]
    status = {key: chat[key] for key in ('state', 'reason', 'expires_at') if key in chat}
    report['capabilities']['chat'] = status.copy()
    report['capabilities']['planning'] = status.copy() if getattr(state, 'planner', None) else {
        'state': 'unavailable', 'reason': 'component_not_initialized'}
    image_stage = chat if vision['state'] == 'available' else vision
    report['capabilities']['multimodal'] = {'state': image_stage['state'], 'reason': image_stage['reason']}
    if getattr(cfg, 'is_local_dev', False):
        report['run_profile'] = 'local_dev'
        report['history_retention'] = getattr(getattr(state, 'local_retention', None), 'mode', 'disposable')
        report['vision_models'] = []
        del report['document_intelligence']
        report['text_file_understanding'] = False
    return report


def _with_local_tools(report, state):
    if report.get('run_profile') != 'local_dev':
        return report
    resolve = dispatch_identity_resolver.get()
    user = resolve() if resolve else None
    if user is None or user.id != getattr(state, 'local_viewer_id', None):
        return report

    from backend.tools.registry import LOCAL_CHAT_TOOLS

    registry = getattr(getattr(state, 'engine', None), '_tool_registry', None)
    tools = {
        name: registry.local_tool_status(name, user.id, user.role)
        if registry and getattr(state, 'startup_complete', False)
        else {'available': False, 'reason': 'unavailable', 'requires_approval': False}
        for name in LOCAL_CHAT_TOOLS
    }
    report['local_tools'] = {'enabled': any(tool['available'] for tool in tools.values()), 'tools': tools}
    from backend.rag.local_knowledge import RETRIEVAL_MODE
    report['local_knowledge'] = {
        'available': bool(getattr(state, 'local_knowledge', None) and getattr(state, 'startup_complete', False)),
        'mode': RETRIEVAL_MODE,
    }
    return report


async def probe_inference(target: str, model: str, *, vision: bool = False) -> bool:
    """Bounded synthetic inference; never follows redirects or environment proxies."""
    prompt = {'role': 'user', 'content': 'Reply with OK.'}
    expected_color = None
    if vision:
        from PIL import Image

        expected_color = secrets.choice(('red', 'green', 'blue'))
        # This image is generated locally. The expected color is never in the prompt.
        with io.BytesIO() as buffer, Image.new('RGB', (224, 224), expected_color) as sample:
            sample.save(buffer, format='PNG')
            prompt = {'role': 'user', 'content': 'What color is this image? Reply with one English color word.',
                      'images': [base64.b64encode(buffer.getvalue()).decode('ascii')]}
    try:
        async with asyncio.timeout(INFERENCE_TIMEOUT):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(float(INFERENCE_TIMEOUT)), trust_env=False, follow_redirects=False,
            ) as client:
                async with client.stream('POST', target.removesuffix('/api/tags') + '/api/chat',
                    headers={'Accept-Encoding': 'identity'}, json={'model': model, 'stream': False,
                    'messages': [prompt],
                    'options': {'num_predict': 8}, 'keep_alive': 0}) as reply:
                    reply.raise_for_status()
                    if reply.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                        return False
                    body = bytearray()
                    async with aclosing(reply.aiter_raw(chunk_size=4096)) as chunks:
                        async for chunk in chunks:
                            if len(body) + len(chunk) > MAX_INFERENCE_BYTES:
                                return False
                            body.extend(chunk)
                    data = json.loads(body)
                    if (not isinstance(data, dict) or data.get('done') is not True or data.get('error')
                            or data.get('done_reason', 'stop') != 'stop'):
                        return False
                    returned = data.get('model')
                    message = data.get('message')
                    return (isinstance(returned, str) and model_key(returned) == model_key(model)
                            and isinstance(message, dict) and message.get('role') == 'assistant'
                            and isinstance(message.get('content'), str) and bool(message['content'].strip())
                            and not message.get('tool_calls')
                            and (expected_color is None or message['content'].strip().rstrip('.').lower() == expected_color))
    except (httpx.HTTPError, TimeoutError, ValueError, TypeError, RecursionError):
        return False


async def observe_ollama(target: str, *, local_only: bool = False) -> ModelObservation:
    """Fetch a bounded inventory without redirects, proxies, DNS or inference."""
    try:
        async with asyncio.timeout(MODEL_CHECK_TIMEOUT):
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                async with client.stream("GET", target, headers={"Accept-Encoding": "identity"}) as reply:
                    reply.raise_for_status()
                    if reply.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise ValueError("unsupported response encoding")
                    body = bytearray()
                    async with aclosing(reply.aiter_raw(chunk_size=8192)) as chunks:
                        async for chunk in chunks:
                            if len(body) + len(chunk) > MAX_TAGS_BYTES:
                                raise ValueError("inventory too large")
                            body.extend(chunk)
                    payload = json.loads(body)
                    if local_only:
                        # Discard cloud entries and require a locally stored model
                        # reported by the bounded inventory before accepting a prompt.
                        parse_tag_digests(payload)
                        payload = {'models': [
                            item for item in payload['models']
                            if not (item.get('remote_host') or item.get('remote_model'))
                            and not item['name'].lower().endswith('-cloud')
                            and type(item.get('size')) is int and item['size'] > 0
                            and item.get('digest')
                        ]}
                    return ModelObservation("available", "inventory_observed", parse_tags(payload), parse_tag_digests(payload))
    except (ValueError, RecursionError):
        return ModelObservation("unavailable", "invalid_model_inventory")
    except (TimeoutError, httpx.HTTPError):
        return ModelObservation("unavailable", "ollama_check_failed")


async def refresh_local_inventory(state) -> None:
    async with state.local_inventory_lock:
        target = _tags_url(state)
        observation = await observe_ollama(target, local_only=True)
        state.model_observation = (target, time.monotonic(), observation)


@router.get("/live", summary="Liveness probe")
async def liveness():
    return {"status": "alive", "timestamp": time.time()}


@router.get("/capabilities", summary="Passive development capability status")
async def capabilities(request: Request, response: Response, model: str | None = None,
                       vision_model: str | None = None, bearer=Depends(bearer_scheme)):
    response.headers["Cache-Control"] = "no-store"
    state = request.app.state
    report = _report(state, model, vision_model)
    if (report.get('run_profile') == 'local_dev' and getattr(state, 'startup_complete', False)
            and request_session_credentials(request, bearer)[0]):
        try:
            await get_current_user(request, bearer=bearer)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            return report
        return _with_local_tools(report, state)
    return report


@router.post('/capabilities/inference-check', summary='Explicit local chat inference check')
async def check_inference(body: InferenceCheck, request: Request, response: Response,
                          _user=Depends(require_permission(Permission.VIEW_DATA)), vision_model: str | None = None):
    await _check_model(request.app.state, response, body.model, 'chat')
    return _with_local_tools(_report(request.app.state, body.model, vision_model), request.app.state)


@router.post('/capabilities/vision-check', summary='Explicit local vision inference check')
async def check_vision(body: InferenceCheck, request: Request, response: Response,
                       _user=Depends(require_permission(Permission.VIEW_DATA)), model: str | None = None):
    await _check_model(request.app.state, response, body.model, 'vision')
    return _with_local_tools(_report(request.app.state, model, body.model), request.app.state)


async def _check_model(state, response: Response, model: str, capability: str) -> None:
    response.headers['Cache-Control'] = 'no-store'
    identity = _identity(state, model, capability)
    status = model_readiness(state, model, capability)
    if identity is None or status['reason'] in (
            'model_check_required', 'configured_model_missing', 'component_not_initialized',
            'vision_model_digest_required'):
        response.status_code = 409
        return
    if getattr(state, 'inference_check_in_progress', None) is not None or getattr(state, 'model_check_in_progress', False):
        response.status_code = 409
        return
    observation_attr = 'vision_observation' if capability == 'vision' else 'inference_observation'
    setattr(state, observation_attr, None)
    state.inference_check_in_progress = identity
    state.inference_check_capability = capability
    token = object()
    state.inference_check_token = token
    generation = getattr(state, 'model_observation_generation', 0)
    inventory = getattr(state, 'model_observation', None)
    digest = _observed_digest(inventory[2] if inventory else None, identity[1])
    try:
        success = (await probe_inference(identity[0], identity[1], vision=True) if capability == 'vision'
                   else await probe_inference(identity[0], identity[1]))
        # Configuration, inventory and lifespan must still match after the await.
        if (getattr(state, 'inference_check_token', None) is token
                and _identity(state, model, capability) == identity and getattr(state, 'startup_complete', False)
                and generation == getattr(state, 'model_observation_generation', 0)
                and inventory == getattr(state, 'model_observation', None)):
            setattr(state, observation_attr, (identity, time.monotonic(), success, digest))
    finally:
        if getattr(state, 'inference_check_token', None) is token:
            state.inference_check_in_progress = None
            state.inference_check_capability = None
            state.inference_check_token = None


@router.post("/capabilities/ollama-check", summary="Explicit local Ollama inventory check")
async def check_ollama(
    request: Request,
    response: Response,
    model: str | None = None,
    _user=Depends(require_permission(Permission.VIEW_DATA)),
    vision_model: str | None = None,
):
    state = request.app.state
    response.headers["Cache-Control"] = "no-store"
    if getattr(_settings(state), 'is_local_dev', False):
        await refresh_local_inventory(state)
        return _with_local_tools(_report(state, model, vision_model), state)
    target = _tags_url(state)
    if target and getattr(state, "startup_complete", False):
        if getattr(state, "model_check_in_progress", False):
            response.status_code = 409
        else:
            state.model_check_in_progress = True
            token = object()
            state.model_check_token = token
            state.inference_observation = None
            state.vision_observation = None
            state.model_observation_generation = getattr(state, 'model_observation_generation', 0) + 1
            generation = state.model_observation_generation
            try:
                observation = await observe_ollama(target)
                if (getattr(state, 'model_check_token', None) is token
                        and getattr(state, 'startup_complete', False)
                        and _tags_url(state) == target
                        and state.model_observation_generation == generation):
                    state.model_observation = (target, time.monotonic(), observation)
                    state.inference_observation = None
                    state.vision_observation = None
                    state.model_observation_generation += 1
            finally:
                if getattr(state, 'model_check_token', None) is token:
                    state.model_check_in_progress = False
                    state.model_check_token = None
    return _report(state, model, vision_model)


@router.get("/ready", summary="Backend lifecycle readiness")
async def readiness(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    backend = _report(request.app.state)["capabilities"]["backend"]
    ready = backend["state"] == "available"
    if not ready:
        response.status_code = 503
    state = "healthy" if ready else "unhealthy"
    return {"ready": ready, "status": state, "cached": False, "components": [
        {"name": "backend_lifecycle", "status": state, "details": backend["reason"]},
    ]}


@router.get("", response_model=HealthResponse, summary="Legacy backend status")
async def consolidated_health(request: Request):
    state = request.app.state
    cfg = _settings(state)
    model_router = getattr(state, "model_router", None)
    return HealthResponse(
        status="ok" if getattr(state, "startup_complete", False) else "unavailable",
        service="sovereign-workbench",
        version=cfg.app_version,
        environment=cfg.app_env,
        model_provider="ollama",
        default_model=model_router.default_model_id if model_router else cfg.ollama_default_model,
        ollama_url=cfg.ollama_base_url,
    )
