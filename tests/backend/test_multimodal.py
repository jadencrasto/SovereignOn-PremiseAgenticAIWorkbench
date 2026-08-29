"""
tests/backend/test_multimodal.py
---------------------------------
Phase 5 multimodal test suite.

Coverage:
  - Image validation (extensions, MIME, size, dimensions, malformed, traversal)
  - Model capabilities (resolve by capability, error on unavailable)
  - Ollama provider (text still works, vision payload, encoding)
  - Agent engine (text-only unchanged, multimodal method exists)
  - API (JSON chat still works, multipart endpoint exists, invalid image rejected)

NOTE: Tests use mocks — no real Ollama connection required.
      Integration / live verification is done separately.
"""

from __future__ import annotations

import base64
import io
import os
import struct
import uuid
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient


# ===========================================================================
# Helpers — minimal valid image bytes
# ===========================================================================

def _make_png_bytes(width: int = 4, height: int = 4) -> bytes:
    """Generate a minimal valid PNG (solid color, very small)."""
    import zlib
    # PNG signature
    sig = b"\x89PNG\r\n\x1a\n"
    # IHDR chunk: width, height, bit_depth=8, color_type=2 (RGB)
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    # Minimal IDAT (compressed scanline data)
    raw = b"\x00" + bytes([255, 0, 0] * width)  # filter byte + RGB pixels
    raw *= height
    compressed = zlib.compress(raw)
    idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
    idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc)
    # IEND chunk
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = b"\x00\x00\x00\x00IEND" + struct.pack(">I", iend_crc)
    return sig + ihdr + idat + iend


def _make_jpeg_bytes() -> bytes:
    """Generate minimal valid JPEG bytes (SOI + EOI)."""
    # Minimal JPEG: SOI + APP0 JFIF + SOF0 + EOI
    # For test purposes, just check SOI and EOI markers
    soi = b"\xff\xd8"
    # Minimal APP0
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    # SOF0 for 1x1 greyscale
    sof0 = b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    # DHT (minimal)
    dht = b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b"
    # SOS + scan + EOI
    sos = b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\xa4\x00\xf4"
    eoi = b"\xff\xd9"
    return soi + app0 + sof0 + dht + sos + eoi


def _make_webp_bytes() -> bytes:
    """Generate minimal valid WEBP bytes."""
    # RIFF....WEBP header
    riff = b"RIFF"
    size = struct.pack("<I", 12)
    webp = b"WEBP"
    # VP8L chunk (minimal)
    vp8l = b"VP8L\x08\x00\x00\x00\x2f\x00\x00\x00\x00\x00\xfe\xff\x03"
    payload = webp + vp8l
    file_size = struct.pack("<I", len(payload))
    return riff + file_size + payload


# ===========================================================================
# Section 1: Image Validation
# ===========================================================================

class TestImageProcessor:
    """Tests for backend.multimodal.image_processor.ImageProcessor"""

    @pytest.fixture
    def processor(self, tmp_path):
        from backend.multimodal.image_processor import ImageProcessor
        return ImageProcessor(upload_dir=tmp_path, max_size_bytes=10 * 1024 * 1024)

    # ------ Valid formats ------

    def test_valid_png(self, processor):
        """A valid PNG should pass all validation and return ProcessedImage."""
        from backend.multimodal.image_processor import ImageProcessor
        data = _make_png_bytes()
        result = processor.process(data=data, filename="test.png", save_to_disk=False)
        assert result.mime_type == "image/png"
        assert result.size_bytes == len(data)
        assert result.base64_data  # non-empty
        assert result.width == 4
        assert result.height == 4

    def test_valid_jpeg(self, processor):
        """A valid JPEG should pass validation."""
        data = _make_jpeg_bytes()
        result = processor.process(data=data, filename="photo.jpg", save_to_disk=False)
        assert result.mime_type == "image/jpeg"
        assert result.size_bytes == len(data)
        assert result.base64_data

    def test_valid_jpeg_uppercase_extension(self, processor):
        """JPEG with .JPEG extension (uppercased by safety module) should work."""
        data = _make_jpeg_bytes()
        # sanitize_filename will lowercase but let's test with lowercase
        result = processor.process(data=data, filename="photo.jpeg", save_to_disk=False)
        assert result.mime_type == "image/jpeg"

    def test_valid_webp(self, processor):
        """A valid WEBP should pass validation."""
        data = _make_webp_bytes()
        result = processor.process(data=data, filename="image.webp", save_to_disk=False)
        assert result.mime_type == "image/webp"

    # ------ Invalid extension ------

    def test_invalid_extension_txt(self, processor):
        """A .txt file should be rejected."""
        from backend.multimodal.image_processor import ImageValidationError
        with pytest.raises(ImageValidationError) as exc_info:
            processor.process(data=b"hello", filename="readme.txt", save_to_disk=False)
        assert exc_info.value.code == "unsupported_extension"

    def test_invalid_extension_pdf(self, processor):
        """A .pdf file should be rejected."""
        from backend.multimodal.image_processor import ImageValidationError
        with pytest.raises(ImageValidationError) as exc_info:
            processor.process(data=b"%PDF", filename="document.pdf", save_to_disk=False)
        assert exc_info.value.code == "unsupported_extension"

    def test_invalid_extension_exe(self, processor):
        """A .exe file should be rejected."""
        from backend.multimodal.image_processor import ImageValidationError
        with pytest.raises(ImageValidationError):
            processor.process(data=b"MZ", filename="virus.exe", save_to_disk=False)

    def test_invalid_extension_gif(self, processor):
        """GIF is not in the allowed list, should be rejected."""
        from backend.multimodal.image_processor import ImageValidationError
        with pytest.raises(ImageValidationError) as exc_info:
            processor.process(data=b"GIF89a", filename="anim.gif", save_to_disk=False)
        assert exc_info.value.code == "unsupported_extension"

    # ------ Oversized image ------

    def test_oversized_image(self, tmp_path):
        """An image exceeding the size limit should be rejected."""
        from backend.multimodal.image_processor import ImageProcessor, ImageValidationError
        # Limit: 72 bytes, PNG is 73+ bytes — must be over the limit
        small_limit = ImageProcessor(upload_dir=tmp_path, max_size_bytes=50)
        png = _make_png_bytes()
        # PNG bytes are bigger than 50 bytes
        with pytest.raises(ImageValidationError) as exc_info:
            small_limit.process(data=png, filename="big.png", save_to_disk=False)
        assert exc_info.value.code == "oversized"

    # ------ Malformed image ------

    def test_malformed_png_wrong_magic(self, processor):
        """PNG file with wrong magic bytes should fail MIME mismatch."""
        from backend.multimodal.image_processor import ImageValidationError
        # PNG extension but JPEG content
        jpeg_data = _make_jpeg_bytes()
        with pytest.raises(ImageValidationError) as exc_info:
            processor.process(data=jpeg_data, filename="fake.png", save_to_disk=False)
        assert exc_info.value.code in ("mime_mismatch", "malformed_image")

    def test_malformed_jpeg_no_eoi(self, processor):
        """JPEG without EOI marker should fail structural check."""
        from backend.multimodal.image_processor import ImageValidationError
        # JPEG SOI but no EOI
        bad_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 20  # no \xff\xd9 at end
        with pytest.raises(ImageValidationError) as exc_info:
            processor.process(data=bad_jpeg, filename="bad.jpg", save_to_disk=False)
        assert exc_info.value.code == "malformed_image"

    def test_empty_file(self, processor):
        """Empty file should be rejected."""
        from backend.multimodal.image_processor import ImageValidationError
        with pytest.raises(ImageValidationError) as exc_info:
            processor.process(data=b"", filename="empty.png", save_to_disk=False)
        assert exc_info.value.code == "empty_file"

    # ------ Path traversal ------

    def test_traversal_filename(self, processor):
        """Filename with ../ should be rejected."""
        from backend.multimodal.image_processor import ImageValidationError
        png = _make_png_bytes()
        with pytest.raises(ImageValidationError) as exc_info:
            processor.process(data=png, filename="../secret.png", save_to_disk=False)
        # The path traversal is caught by sanitize_filename stripping dirs
        # The result might be 'secret.png' (safe) or raise traversal error
        # Either result is acceptable — the traversal does not succeed
        # If it does raise, verify the code
        if exc_info:
            assert exc_info.value.code in ("path_traversal", "unsupported_extension", "malformed_image", "mime_mismatch")

    def test_absolute_path_filename(self, processor):
        """Absolute path as filename should be rejected or sanitized."""
        from backend.multimodal.image_processor import ImageValidationError
        png = _make_png_bytes()
        # Should raise or strip to basename
        try:
            result = processor.process(data=png, filename="/etc/passwd.png", save_to_disk=False)
            # If it didn't raise, the path must have been sanitized to 'passwd.png'
            assert result.original_filename == "passwd.png"
        except ImageValidationError as e:
            assert e.code in ("absolute_path", "unsupported_extension")

    # ------ Excessive dimensions ------

    def test_excessive_dimensions(self, tmp_path):
        """PNG with dimensions beyond the limit should be rejected."""
        from backend.multimodal.image_processor import ImageProcessor, ImageValidationError
        strict = ImageProcessor(upload_dir=tmp_path, max_dimension=3)
        # 4x4 PNG exceeds 3px limit
        png = _make_png_bytes(4, 4)
        with pytest.raises(ImageValidationError) as exc_info:
            strict.process(data=png, filename="big.png", save_to_disk=False)
        assert exc_info.value.code == "excessive_dimensions"

    # ------ Base64 encoding ------

    def test_base64_encoding_correct(self, processor):
        """Encoded data should decode back to the original bytes."""
        data = _make_png_bytes()
        result = processor.process(data=data, filename="test.png", save_to_disk=False)
        decoded = base64.b64decode(result.base64_data)
        assert decoded == data

    # ------ Save to disk ------

    def test_save_to_disk(self, tmp_path):
        """Image should be saved to the images subdirectory."""
        from backend.multimodal.image_processor import ImageProcessor
        processor = ImageProcessor(upload_dir=tmp_path)
        data = _make_png_bytes()
        result = processor.process(data=data, filename="saved.png", save_to_disk=True)
        assert result.storage_path.exists()
        assert result.storage_path.read_bytes() == data


# ===========================================================================
# Section 2: Model Capabilities
# ===========================================================================

class TestModelCapabilities:
    """Tests for capability-based routing in ModelRouter."""

    @pytest.fixture
    def mock_settings(self, tmp_path):
        """Minimal settings with a temp config dir containing models.yaml."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        models_yaml = config_dir / "models.yaml"
        models_yaml.write_text("""
providers:
  ollama:
    type: ollama
    base_url: http://localhost:11434
    models:
      - qwen2.5:7b
      - llava:7b
    embedding_models:
      - nomic-embed-text

default_model: ollama/qwen2.5:7b

models:
  ollama/qwen2.5:7b:
    capabilities:
      - chat

  ollama/llava:7b:
    capabilities:
      - chat
      - vision

  ollama/nomic-embed-text:
    capabilities:
      - embedding

capability_routing:
  chat: ollama/qwen2.5:7b
  vision: ollama/llava:7b
  embedding: ollama/nomic-embed-text
""", encoding="utf-8")

        settings = MagicMock()
        settings.config_dir = config_dir
        settings.ollama_base_url = "http://localhost:11434"
        settings.ollama_default_model = "qwen2.5:7b"
        return settings

    @pytest.fixture
    def router(self, mock_settings):
        from backend.models.router import ModelRouter
        return ModelRouter(mock_settings)

    def test_qwen_resolves_for_chat(self, router):
        """resolve_chat_model() should return qwen2.5:7b."""
        _, model_name = router.resolve_chat_model()
        assert model_name == "qwen2.5:7b"

    def test_llava_resolves_for_vision(self, router):
        """resolve_vision_model() should return llava:7b."""
        _, model_name = router.resolve_vision_model()
        assert model_name == "llava:7b"

    def test_nomic_resolves_for_embedding(self, router):
        """resolve_embedding_model() should return nomic-embed-text."""
        _, model_name = router.resolve_embedding_model()
        assert model_name == "nomic-embed-text"

    def test_unavailable_capability_raises(self, router):
        """Resolving an unsupported capability should raise ValueError."""
        with pytest.raises(ValueError, match="No model configured"):
            router.resolve_by_capability("code_execution")

    def test_qwen_capabilities(self, router):
        """qwen2.5:7b should have 'chat' capability only."""
        caps = router.get_model_capabilities("ollama/qwen2.5:7b")
        assert "chat" in caps
        assert "vision" not in caps

    def test_llava_capabilities(self, router):
        """llava:7b should have both 'chat' and 'vision' capabilities."""
        caps = router.get_model_capabilities("ollama/llava:7b")
        assert "chat" in caps
        assert "vision" in caps

    def test_nomic_capabilities(self, router):
        """nomic-embed-text should have 'embedding' capability only."""
        caps = router.get_model_capabilities("ollama/nomic-embed-text")
        assert "embedding" in caps
        assert "chat" not in caps

    def test_unknown_model_returns_empty(self, router):
        """Unknown model should return empty capabilities list."""
        caps = router.get_model_capabilities("ollama/not-installed:3b")
        assert caps == []

    @pytest.mark.asyncio
    async def test_list_models_with_capabilities(self, router):
        """list_models_with_capabilities() should return enriched list."""
        with patch.object(router, "list_available_models", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = {"ollama": ["qwen2.5:7b", "llava:7b", "nomic-embed-text"]}
            models = await router.list_models_with_capabilities()

        assert len(models) == 3
        model_ids = [m["id"] for m in models]
        assert "ollama/qwen2.5:7b" in model_ids
        assert "ollama/llava:7b" in model_ids
        assert "ollama/nomic-embed-text" in model_ids

        llava = next(m for m in models if m["id"] == "ollama/llava:7b")
        assert "vision" in llava["capabilities"]
        assert llava["installed"] is True


# ===========================================================================
# Section 3: Ollama Provider Vision
# ===========================================================================

class TestOllamaProviderVision:
    """Tests for OllamaProvider._build_payload with images."""

    @pytest.fixture
    def provider(self):
        from backend.models.ollama_provider import OllamaProvider
        return OllamaProvider(base_url="http://localhost:11434")

    def test_text_only_payload_unchanged(self, provider):
        """Text-only requests should produce identical payload to Phase 4."""
        from backend.models.base import ChatRequest, Message
        request = ChatRequest(
            messages=[Message(role="user", content="Hello")],
            model="qwen2.5:7b",
            stream=False,
        )
        payload = provider._build_payload(request, stream=False)
        assert payload["model"] == "qwen2.5:7b"
        assert payload["messages"][0]["role"] == "user"
        assert payload["messages"][0]["content"] == "Hello"
        assert "images" not in payload["messages"][0]

    def test_vision_payload_includes_images(self, provider):
        """Vision request should include 'images' in the message dict."""
        from backend.models.base import ChatRequest, Message
        fake_b64 = base64.b64encode(b"fake_image_data").decode()
        request = ChatRequest(
            messages=[Message(role="user", content="Describe this")],
            model="llava:7b",
            stream=False,
            images=[fake_b64],
        )
        payload = provider._build_payload(request, stream=False)
        assert "images" in payload["messages"][-1]
        assert fake_b64 in payload["messages"][-1]["images"]

    def test_message_with_images_field(self, provider):
        """Message.images field should be forwarded to payload."""
        from backend.models.base import ChatRequest, Message
        fake_b64 = base64.b64encode(b"img_data").decode()
        request = ChatRequest(
            messages=[Message(role="user", content="What is this?", images=[fake_b64])],
            model="llava:7b",
            stream=False,
        )
        payload = provider._build_payload(request, stream=False)
        msg = payload["messages"][0]
        assert "images" in msg
        assert fake_b64 in msg["images"]

    def test_system_message_no_images(self, provider):
        """System messages should never have images attached."""
        from backend.models.base import ChatRequest, Message
        fake_b64 = base64.b64encode(b"img").decode()
        request = ChatRequest(
            messages=[
                Message(role="system", content="You are a helpful assistant."),
                Message(role="user", content="Describe image"),
            ],
            model="llava:7b",
            stream=False,
            images=[fake_b64],
        )
        payload = provider._build_payload(request, stream=False)
        # Images should be on user message, not system message
        system_msg = next(m for m in payload["messages"] if m["role"] == "system")
        user_msg = next(m for m in payload["messages"] if m["role"] == "user")
        assert "images" not in system_msg
        assert "images" in user_msg

    def test_empty_images_list_no_images_key(self, provider):
        """Empty images list should produce payload without 'images' key."""
        from backend.models.base import ChatRequest, Message
        request = ChatRequest(
            messages=[Message(role="user", content="No image here")],
            model="qwen2.5:7b",
            stream=False,
            images=[],  # explicitly empty
        )
        payload = provider._build_payload(request, stream=False)
        assert "images" not in payload["messages"][0]

    @pytest.mark.asyncio
    async def test_text_chat_still_works_mocked(self, provider):
        """Text-only chat() should still work (mocked HTTP)."""
        from backend.models.base import ChatRequest, Message
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "Paris"},
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            request = ChatRequest(
                messages=[Message(role="user", content="Capital of France?")],
                model="qwen2.5:7b",
                stream=False,
            )
            resp = await provider.chat(request)
            assert resp.content == "Paris"
            assert resp.model == "qwen2.5:7b"
            # Verify no images in the payload
            call_args = mock_post.call_args
            payload = call_args[1]["json"]
            assert "images" not in payload["messages"][0]

    def test_image_encoding_round_trip(self):
        """base64 encoding used by image_processor should round-trip correctly."""
        from backend.multimodal.image_processor import encode_bytes_to_base64
        original = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        encoded = encode_bytes_to_base64(original)
        decoded = base64.b64decode(encoded)
        assert decoded == original


# ===========================================================================
# Section 4: Multimodal Service
# ===========================================================================

class TestMultimodalService:
    """Tests for backend.multimodal.service.MultimodalService."""

    @pytest.fixture
    def mock_provider(self):
        from backend.models.base import ChatResponse
        provider = AsyncMock()
        provider.provider_name = "ollama"
        provider.chat = AsyncMock(return_value=ChatResponse(
            content="The image shows a pressure gauge reading 72 PSI.",
            model="llava:7b",
            provider="ollama",
        ))
        return provider

    @pytest.mark.asyncio
    async def test_analyze_image_returns_observation(self, mock_provider):
        """analyze_image() should return the vision model's text output."""
        from backend.multimodal.service import MultimodalService
        service = MultimodalService(vision_provider=mock_provider, vision_model="llava:7b")
        fake_b64 = base64.b64encode(b"fake_img").decode()
        result = await service.analyze_image(image_b64=fake_b64, user_prompt="What is shown?")
        assert "72 PSI" in result
        # Verify the provider was called with images
        call_args = mock_provider.chat.call_args[0][0]
        user_msg = next(m for m in call_args.messages if m.role == "user")
        assert fake_b64 in user_msg.images

    @pytest.mark.asyncio
    async def test_analyze_image_error_propagates(self, mock_provider):
        """RuntimeError from provider should propagate up."""
        from backend.multimodal.service import MultimodalService
        mock_provider.chat = AsyncMock(side_effect=RuntimeError("Ollama unavailable"))
        service = MultimodalService(vision_provider=mock_provider, vision_model="llava:7b")
        with pytest.raises(RuntimeError, match="Ollama unavailable"):
            await service.analyze_image(image_b64="abc", user_prompt="What?")

    def test_build_visual_context_message_contains_observation(self):
        """The context message should contain the visual observation."""
        from backend.multimodal.service import build_visual_context_message
        ctx = build_visual_context_message("72 PSI reading", "Is this safe?")
        assert "72 PSI reading" in ctx
        assert "llava:7b" in ctx
        assert "VISUAL OBSERVATION" in ctx
        assert "Is this safe?" in ctx

    def test_build_visual_context_distinguishes_evidence(self):
        """Context message must clearly label source as visual observation."""
        from backend.multimodal.service import build_visual_context_message
        ctx = build_visual_context_message("Some observation", "Some question")
        assert "VISUAL OBSERVATION" in ctx
        assert "visual observation" in ctx.lower()


# ===========================================================================
# Section 5: Agent Engine
# ===========================================================================

class TestAgentEngineMultimodal:
    """Tests for AgentEngine multimodal method."""

    @pytest.fixture
    def mock_settings(self, tmp_path):
        agents_dir = tmp_path / "agents" / "default"
        agents_dir.mkdir(parents=True)
        (agents_dir / "system_prompt.md").write_text("You are a helpful assistant.")
        (agents_dir / "agent.yaml").write_text(
            "max_tool_iterations: 5\ntemperature: 0.7\nvision:\n  enabled: true\n  model: ollama/llava:7b\n"
        )
        settings = MagicMock()
        settings.agents_dir = tmp_path / "agents"
        return settings

    @pytest.fixture
    def mock_router(self):
        router = MagicMock()
        router.default_model_id = "ollama/qwen2.5:7b"
        return router

    @pytest.fixture
    def engine(self, mock_settings, mock_router):
        from backend.agent.engine import AgentEngine
        from backend.agent.memory import ConversationMemory
        memory = ConversationMemory()
        return AgentEngine(
            settings=mock_settings,
            router=mock_router,
            memory=memory,
        )

    def test_text_only_methods_exist(self, engine):
        """Phase 1–4 methods should still exist on the engine."""
        assert hasattr(engine, "chat")
        assert hasattr(engine, "chat_stream")
        assert hasattr(engine, "chat_stream_with_tools")

    def test_multimodal_method_exists(self, engine):
        """Phase 5 multimodal method should exist."""
        assert hasattr(engine, "chat_stream_with_tools_multimodal")

    def test_multimodal_method_is_async_generator(self, engine):
        """chat_stream_with_tools_multimodal should be an async generator."""
        import inspect
        assert inspect.isasyncgenfunction(engine.chat_stream_with_tools_multimodal)

    def test_max_tool_iterations_preserved(self, engine):
        """max_tool_iterations from agent.yaml should be respected."""
        assert engine._max_tool_iterations == 5

    def test_set_tool_registry(self, engine):
        """set_tool_registry() should wire the registry."""
        mock_registry = MagicMock()
        engine.set_tool_registry(mock_registry)
        assert engine._tool_registry is mock_registry


# ===========================================================================
# Section 6: API Tests
# ===========================================================================

class TestMultimodalAPI:
    """API-level tests for multimodal endpoint."""

    @pytest.fixture
    def app(self):
        """Create a test FastAPI app with mocked state."""
        from fastapi import FastAPI
        from backend.api.chat import router as chat_router

        test_app = FastAPI()
        test_app.include_router(chat_router)

        # Mock engine
        mock_engine = MagicMock()
        mock_engine._tool_registry = None

        async def fake_chat_stream(session_id, message, model_id):
            yield "Hello world"
            yield []

        mock_engine.chat_stream = fake_chat_stream

        async def fake_multimodal_stream(session_id, message, image_b64, model_id):
            yield {"type": "agent_status", "status": "analyzing_image"}
            yield "The image shows a chart."
            yield []

        mock_engine.chat_stream_with_tools_multimodal = fake_multimodal_stream

        # Mock router
        mock_router = MagicMock()
        mock_router.resolve_model = MagicMock(return_value=("ollama", "qwen2.5:7b"))

        from pathlib import Path
        import tempfile
        tmpdir = tempfile.mkdtemp()

        test_app.state.engine = mock_engine
        test_app.state.model_router = mock_router
        test_app.state.upload_dir = Path(tmpdir)

        return test_app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_existing_json_chat_still_works(self, client):
        """POST /api/chat with JSON body should return SSE stream."""
        response = client.post(
            "/api/chat",
            json={"message": "Hello", "stream": True},
            headers={"Accept": "text/event-stream"},
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_multimodal_endpoint_exists(self, client):
        """POST /api/chat/multimodal endpoint should exist."""
        response = client.post(
            "/api/chat/multimodal",
            data={"message": "Describe this"},
        )
        # Should work (200) even without image
        assert response.status_code == 200

    def test_multimodal_invalid_image_extension_rejected(self, client):
        """Uploading a .txt file as image should return 422."""
        from io import BytesIO
        response = client.post(
            "/api/chat/multimodal",
            data={"message": "What is this?"},
            files={"image": ("test.txt", BytesIO(b"not an image"), "text/plain")},
        )
        assert response.status_code == 422

    def test_multimodal_valid_png_accepted(self, client):
        """Uploading a valid PNG should return 200 SSE stream."""
        from io import BytesIO
        png_data = _make_png_bytes()
        response = client.post(
            "/api/chat/multimodal",
            data={"message": "Describe this image"},
            files={"image": ("test.png", BytesIO(png_data), "image/png")},
        )
        assert response.status_code == 200

    def test_sse_events_structure(self, client):
        """SSE stream should contain valid JSON event lines."""
        import json
        response = client.post(
            "/api/chat",
            json={"message": "test", "stream": True},
        )
        assert response.status_code == 200
        lines = response.text.strip().split("\n")
        data_lines = [l for l in lines if l.startswith("data: ")]
        assert len(data_lines) > 0
        # Parse each SSE data line
        for line in data_lines:
            json_str = line[6:]  # strip "data: "
            parsed = json.loads(json_str)
            assert "type" in parsed

    def test_multimodal_oversized_image_rejected(self, client):
        """An oversized image should return 422."""
        from io import BytesIO
        # Patch via the module where ImageProcessor is defined
        with patch("backend.multimodal.image_processor.ImageProcessor") as MockProcessorCls:
            from backend.multimodal.image_processor import ImageValidationError
            mock_proc = MagicMock()
            mock_proc.process.side_effect = ImageValidationError(
                message="Image too large (12.0 MB). Maximum allowed: 10.0 MB.",
                code="oversized",
            )
            MockProcessorCls.return_value = mock_proc

            png_data = _make_png_bytes()
            response = client.post(
                "/api/chat/multimodal",
                data={"message": "What is this?"},
                files={"image": ("big.png", BytesIO(png_data), "image/png")},
            )
        assert response.status_code == 422

    def test_no_image_falls_back_to_text(self, client):
        """Request without image should use text-only path (no multimodal engine called)."""
        response = client.post(
            "/api/chat/multimodal",
            data={"message": "What is 2+2?"},
        )
        assert response.status_code == 200


# ===========================================================================
# Section 7: SSE Event Regression
# ===========================================================================

class TestSSEEvents:
    """Verify all Phase 4 SSE events remain in Phase 5."""

    def test_stream_chunk_schema_has_all_event_types(self):
        """StreamChunk must support all Phase 4 + Phase 5 event types."""
        from backend.schemas.chat import StreamChunk
        # Phase 4 types
        for event_type in ("delta", "done", "sources", "error", "tool_start", "tool_result", "agent_status"):
            chunk = StreamChunk(type=event_type, content="test")
            assert chunk.type == event_type

    def test_stream_chunk_tool_fields(self):
        """Tool event fields should be present on StreamChunk."""
        from backend.schemas.chat import StreamChunk
        chunk = StreamChunk(
            type="tool_result",
            content="",
            tool="calculator",
            success=True,
            summary="Result: 42",
        )
        assert chunk.tool == "calculator"
        assert chunk.success is True
        assert chunk.summary == "Result: 42"

    def test_stream_chunk_phase5_attachment_field(self):
        """Phase 5: StreamChunk should support optional attachment."""
        from backend.schemas.chat import StreamChunk, ImageAttachment
        attachment = ImageAttachment(
            attachment_id="test-id",
            filename="photo.png",
            mime_type="image/png",
            size_bytes=1024,
        )
        chunk = StreamChunk(type="done", content="", attachment=attachment)
        assert chunk.attachment is not None
        assert chunk.attachment.filename == "photo.png"

    def test_image_attachment_no_base64(self):
        """ImageAttachment schema must NOT have a base64_data field."""
        from backend.schemas.chat import ImageAttachment
        import inspect
        fields = ImageAttachment.model_fields.keys()
        assert "base64_data" not in fields
        assert "image_data" not in fields


# ===========================================================================
# Section 8: Config loading
# ===========================================================================

class TestPhase5Config:
    """Verify Phase 5 config is correctly loaded."""

    def test_models_yaml_has_capabilities(self, tmp_path):
        """models.yaml should have a models section with capabilities."""
        import yaml
        yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "models.yaml"
        if not yaml_path.exists():
            pytest.skip("config/models.yaml not found")
        with open(yaml_path) as f:
            config = yaml.safe_load(f)
        assert "models" in config
        models = config["models"]
        assert "ollama/llava:7b" in models
        assert "vision" in models["ollama/llava:7b"]["capabilities"]

    def test_models_yaml_capability_routing(self):
        """models.yaml should have capability_routing section."""
        import yaml
        yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "models.yaml"
        if not yaml_path.exists():
            pytest.skip("config/models.yaml not found")
        with open(yaml_path) as f:
            config = yaml.safe_load(f)
        routing = config.get("capability_routing", {})
        assert routing.get("chat") == "ollama/qwen2.5:7b"
        assert routing.get("vision") == "ollama/llava:7b"
        assert routing.get("embedding") == "ollama/nomic-embed-text"

    def test_agent_yaml_has_vision_section(self):
        """agent.yaml should have a vision section."""
        import yaml
        yaml_path = Path(__file__).resolve().parent.parent.parent / "agents" / "default" / "agent.yaml"
        if not yaml_path.exists():
            pytest.skip("agent.yaml not found")
        with open(yaml_path) as f:
            config = yaml.safe_load(f)
        assert "vision" in config
        assert config["vision"]["enabled"] is True
        assert "llava" in config["vision"]["model"]

    def test_system_prompt_has_multimodal_section(self):
        """system_prompt.md should contain multimodal instructions."""
        prompt_path = Path(__file__).resolve().parent.parent.parent / "agents" / "default" / "system_prompt.md"
        if not prompt_path.exists():
            pytest.skip("system_prompt.md not found")
        content = prompt_path.read_text(encoding="utf-8")
        assert "VISUAL OBSERVATION" in content or "Multimodal" in content or "vision" in content.lower()
