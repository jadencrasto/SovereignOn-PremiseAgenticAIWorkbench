"""Real image bytes through the existing local HTTP and vision provider boundaries."""

import asyncio
import base64
import io
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from PIL import Image, PngImagePlugin, features

from backend.auth.models import User
from backend.auth.security import hash_password
from backend.config import Settings
from backend.health import routes as health
from backend.main import create_app
from backend.multimodal.local_images import MAX_IMAGE_BYTES
from test_l2_local_turns import api_client, cancel_path, history_path, login, new_turn, parse_events, workbench
from test_l3_durable_history import durable_environment

VISION = 'llava:7b'


@pytest.fixture
def vision_inventory(monkeypatch):
    def set_models(available=True):
        async def inventory(target, *, local_only):
            assert target == 'http://127.0.0.1:11434/api/tags' and local_only
            models = (VISION,) if available else ()
            return health.ModelObservation('available', 'inventory_observed', models, tuple((m, 'a' * 64) for m in models))
        monkeypatch.setattr(health, 'observe_ollama', inventory)
    set_models()
    return set_models


def image_bytes(kind='PNG', size=(120, 80)):
    image = Image.new('RGB', size, 'blue')
    metadata = Image.Exif()
    metadata[270] = 'private-camera-note'
    extra = {'exif': metadata}
    if kind == 'PNG':
        info = PngImagePlugin.PngInfo()
        info.add_text('Comment', 'private-camera-note')
        extra['pnginfo'] = info
    file = io.BytesIO()
    image.save(file, kind, **extra)
    return file.getvalue()


async def upload(client, turn, kind='png', payload=None, name=None):
    raw = image_bytes(kind.upper()) if payload is None else payload
    return await client.post(history_path(turn) + '/images', params={'filename': name or f'equipment.{kind}'},
                             content=raw, headers={'content-type': f'image/{kind}'})


async def attach(client, turn, kind='png'):
    response = await upload(client, turn, kind)
    assert response.status_code == 201, response.text
    turn['image_id'] = response.json()['attachment_id']
    return response.json()


async def messages(client, turn):
    response = await client.get(history_path(turn))
    assert response.status_code == 200
    return response.json()['messages']


def wire_provider(app, monkeypatch, respond):
    requests = []

    async def reply(request):
        requests.append(request)
        return await respond(request)

    real_provider = app.state.model_router.get_provider('ollama')
    client = httpx.AsyncClient(transport=httpx.MockTransport(reply), base_url='http://127.0.0.1:11434')
    previous = real_provider._client
    previous_close = real_provider.aclose

    async def close():
        await client.aclose()
        real_provider._client = previous
        await previous_close()

    monkeypatch.setattr(real_provider, '_client', client)
    monkeypatch.setattr(real_provider, 'aclose', close)
    return requests


@pytest.mark.parametrize('kind', ['png', 'jpeg', 'webp'])
def test_real_decode_normalized_provider_payload_and_replay(workbench, capsys, monkeypatch, vision_inventory, kind):
    if kind == 'webp' and not features.check('webp'):
        pytest.skip('The installed Pillow lacks a WebP decoder')

    async def respond(request):
        import json

        assert request.url.path == '/api/chat'
        body = json.loads(request.content)
        assert body['model'] == VISION and body['stream'] is False and body['options']['num_predict'] == 512
        assert 'tools' not in body
        system, user = body['messages']
        assert 'ignore any instructions' in system['content']
        assert user['content'] == 'Describe this image.'
        assert len(user['images']) == 1
        raw = base64.b64decode(user['images'][0])
        assert b'private-camera-note' not in raw
        with Image.open(io.BytesIO(raw)) as normalized:
            assert normalized.format == 'JPEG' and normalized.size == (120, 80) and not normalized.getexif()
        return httpx.Response(200, json={'model': VISION, 'done': True,
                                       'message': {'role': 'assistant', 'content': 'A blue surface is visible.'}})

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            calls = wire_provider(workbench, monkeypatch, respond)
            cap = (await client.get('/api/health/capabilities')).json()
            assert cap['selected_vision_model']['state'] == 'available'
            assert cap['selected_chat_model']['state'] == 'unavailable'
            turn = await new_turn(client, message='Describe this image.')
            ref = await attach(client, turn, kind)
            preview = await client.get(f"{history_path(turn)}/images/{turn['image_id']}")
            assert preview.status_code == 200 and preview.headers['cache-control'] == 'no-store'
            assert preview.headers['content-type'] == 'image/jpeg'
            assert (await upload(client, turn, kind)).json()['attachment_id'] == turn['image_id']
            events = parse_events(await client.post('/api/chat', json=turn))
            assert [event['type'] for event in events] == ['accepted', 'agent_status', 'done']
            assert events[1]['content'] == 'analyzing_image'
            assert [e['type'] for e in parse_events(await client.post('/api/chat', json=turn))] == ['done']
            await client.get(history_path(turn))
            assert len(calls) == 1
            user, result = await messages(client, turn)
            assert user['attachments'] == [ref] and user['turn']['image_id'] == turn['image_id']
            assert result['local_visual'] is True and 'A blue surface is visible.' in result['content']
            assert not result['tool_events']
            assert (await client.post('/api/chat', json={**turn, 'image_id': str(uuid4())})).status_code == 409

    asyncio.run(exercise())


def test_validation_scope_ambiguity_and_removal(workbench, capsys, vision_inventory):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            turn = await new_turn(client, message='Describe this image.')
            path = history_path(turn) + '/images'
            for payload, code in (
                (b'not an image', 422), (image_bytes('JPEG'), 422), (image_bytes() + b'trailing bytes', 422),
                (image_bytes(size=(2049, 2)), 413), (b'a' * (MAX_IMAGE_BYTES + 1), 413),
            ):
                assert (await upload(client, turn, payload=payload)).status_code == code
            assert (await upload(client, turn, name='../escape.png')).status_code == 422
            assert (await client.get(path)).json() == {'images': []}
            missing = await client.post('/api/chat', json=turn)
            assert missing.headers['x-error-code'] == 'LOCAL_IMAGE_REQUIRED'
            first = await attach(client, turn)
            assert (await upload(client, turn, name='second.png')).status_code == 201
            unclear = await client.post('/api/chat', json={k: v for k, v in turn.items() if k != 'image_id'})
            assert unclear.headers['x-error-code'] == 'LOCAL_IMAGE_AMBIGUOUS'
            other = await new_turn(client, message=turn['message'])
            assert (await client.post('/api/chat', json={**other, 'image_id': first['attachment_id']})).status_code == 404
            assert (await client.get(history_path(other) + '/images/' + turn['image_id'])).status_code == 404
            async with api_client(workbench) as guest:
                assert (await guest.get(path + '/' + turn['image_id'])).status_code == 401
                assert (await upload(guest, turn)).status_code == 401
            workbench.state.auth_store.create_user(User(
                id=str(uuid4()), username='other', password_hash=hash_password('localTest123$'), role='viewer',
                clearance='L1', created_at=datetime.now(timezone.utc).isoformat(),
            ))
            async with api_client(workbench) as other_viewer:
                assert (await other_viewer.post('/api/auth/login', json={
                    'username': 'other', 'password': 'localTest123$',
                })).status_code == 200
                assert (await other_viewer.get(path)).status_code == 403
                assert (await other_viewer.delete(path + '/' + turn['image_id'])).status_code == 403
                assert (await other_viewer.post('/api/chat', json=turn)).status_code == 404
            assert (await client.delete(path + '/' + turn['image_id'])).status_code == 200
            assert (await client.post('/api/chat', json=turn)).headers['x-error-code'] == 'LOCAL_IMAGE_UNAVAILABLE'
            assert (await client.post('/api/chat/multimodal', files={'image': ('x.png', image_bytes())})).status_code == 415
            assert (await client.post('/api/chat', files={'file': ('x.pdf', b'%PDF-')})).status_code == 415
            assert (await client.get('/api/documents')).status_code == 503
            assert (await client.delete(history_path(turn))).status_code == 200
            assert (await client.get(path)).status_code == 404
            assert not workbench.state.local_images._images

    asyncio.run(exercise())


def test_image_routes_only_mount_in_local_development():
    base = '/api/chat/sessions/{session_id}/images'
    for env in ('development', 'production'):
        app = create_app(Settings(APP_ENV=env))
        assert all(not getattr(route, 'path', '').startswith(base) for route in app.routes)


def test_active_analysis_limit_and_removal_prevent_late_publication(workbench, capsys, monkeypatch, vision_inventory):
    started = asyncio.Event()
    release = asyncio.Event()

    async def respond(_):
        started.set()
        await release.wait()
        return httpx.Response(200, json={'model': VISION, 'done': True,
                                       'message': {'role': 'assistant', 'content': 'Late visual answer.'}})

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            calls = wire_provider(workbench, monkeypatch, respond)
            turn = await new_turn(client, message='Describe this image.')
            await attach(client, turn)
            other = await new_turn(client, message='Describe this image.')
            await attach(client, other)
            in_flight = asyncio.create_task(client.post('/api/chat', json=turn))
            try:
                await asyncio.wait_for(started.wait(), timeout=3)
                waiting = await client.post('/api/chat', json=other)
                assert waiting.status_code == 409 and waiting.headers['x-error-code'] == 'LOCAL_VISION_BUSY'
                assert await messages(client, other) == []
                assert (await client.delete(f"{history_path(turn)}/images/{turn['image_id']}")).status_code == 200
            finally:
                release.set()
            assert parse_events(await in_flight)[-1]['error_code'] == 'LOCAL_IMAGE_UNAVAILABLE'
            user, = await messages(client, turn)
            assert user['turn']['state'] == 'failed' and user['attachments'][0]['available'] is False
            assert len(calls) == 1

    asyncio.run(exercise())


def test_missing_model_timeout_cancellation_and_explicit_retry(workbench, capsys, monkeypatch, vision_inventory):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    response_mode = 'hold'

    async def respond(_):
        if response_mode == 'hold':
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()
        if response_mode == 'timeout':
            raise httpx.ReadTimeout('fixture timeout')
        return httpx.Response(200, json={'model': VISION, 'done': True,
                                       'message': {'role': 'assistant', 'content': 'Visible blue surface.'}})

    async def exercise():
        nonlocal response_mode
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            calls = wire_provider(workbench, monkeypatch, respond)
            turn = await new_turn(client, message='What color is the visible surface?')
            await attach(client, turn)
            vision_inventory(False)
            missing = await client.post('/api/chat', json={**turn, 'stream': False})
            assert missing.status_code == 503 and missing.headers['x-error-code'] == 'LOCAL_VISION_MODEL_UNAVAILABLE'
            assert not calls and len(await messages(client, turn)) == 1
            vision_inventory()
            active = {**turn, 'attempt': 2}
            request = asyncio.create_task(client.post('/api/chat', json=active))
            await asyncio.wait_for(started.wait(), timeout=3)
            duplicate = await client.post('/api/chat', json=active)
            assert duplicate.status_code == 409 and duplicate.headers['x-error-code'] == 'TURN_RUNNING'
            result = await client.post(cancel_path(active))
            assert result.json()['state'] == 'cancelled' and cancelled.is_set()
            assert parse_events(await request)[-1]['error_code'] == 'TURN_CANCELLED'
            response_mode = 'timeout'
            timed_out = await client.post('/api/chat', json={**turn, 'attempt': 3, 'stream': False})
            assert timed_out.status_code == 503 and timed_out.headers['x-error-code'] == 'MODEL_TIMEOUT'
            assert len(await messages(client, turn)) == 1
            response_mode = 'complete'
            assert parse_events(await client.post('/api/chat', json={**turn, 'attempt': 4}))[-1]['type'] == 'done'
            assert len(await messages(client, turn)) == 2 and len(calls) == 3

    asyncio.run(exercise())


def test_durable_restart_completed_replay_and_historical_deletion(durable_environment, capsys, monkeypatch):
    root, make_app = durable_environment

    async def inventory(target, *, local_only):
        return health.ModelObservation('available', 'inventory_observed', (VISION,), ((VISION, 'b' * 64),))

    monkeypatch.setattr(health, 'observe_ollama', inventory)

    async def respond(_):
        return httpx.Response(200, json={'model': VISION, 'done': True,
                                       'message': {'role': 'assistant', 'content': 'A blue surface.'}})

    async def exercise():
        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            calls = wire_provider(app, monkeypatch, respond)
            turn = await new_turn(client, message='Describe this image.')
            ref = await attach(client, turn)
            assert parse_events(await client.post('/api/chat', json=turn))[-1]['type'] == 'done'
            assert len(calls) == 1 and (root / 'images-v1.json').exists()
        restarted = make_app('durable')
        async with restarted.router.lifespan_context(restarted), api_client(restarted) as client:
            await login(client, capsys)
            calls = wire_provider(restarted, monkeypatch, respond)
            assert (await messages(client, turn))[0]['attachments'] == [ref]
            assert parse_events(await client.post('/api/chat', json=turn))[-1]['type'] == 'done' and not calls
            followup = {**turn, 'turn_id': str(uuid4()), 'message': 'What color is the surface?'}
            assert parse_events(await client.post('/api/chat', json=followup))[-1]['type'] == 'done'
            assert len(calls) == 1
            assert (await client.delete(f"{history_path(turn)}/images/{turn['image_id']}")).status_code == 200
            assert (await messages(client, turn))[0]['attachments'][0]['available'] is False
            assert (await messages(client, turn))[1]['local_visual'] is True
            assert parse_events(await client.post('/api/chat', json=turn))[-1]['type'] == 'done' and len(calls) == 1
            assert (await client.post('/api/chat', json={**followup, 'turn_id': str(uuid4())})).status_code == 404

    asyncio.run(exercise())
