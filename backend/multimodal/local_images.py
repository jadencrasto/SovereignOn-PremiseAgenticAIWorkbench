"""Bounded image normalization and attachment storage for local development."""

import base64
import hashlib
import hmac
import io
import json
import re
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.local_history import _safe_path, _strict_json, write_local_snapshot
from backend.multimodal.text_file import TextFileError, text_filename

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_MODEL_BYTES = 512 * 1024
MAX_IMAGES = 8
MAX_DIMENSION = 2048
_MAX_SNAPSHOT = 6 * 1024 * 1024
_FORMATS = {'.png': 'png', '.jpeg': 'jpeg', '.jpg': 'jpeg', '.webp': 'webp'}


def image_format(filename):
    extension = Path(filename).suffix.lower()
    if extension not in _FORMATS:
        raise TextFileError('Choose a PNG, JPEG or WebP image.', status=415)
    text_filename(filename, extension)
    return _FORMATS[extension]


def _complete_container(kind, data):
    if kind == 'webp':
        return (len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP'
                and int.from_bytes(data[4:8], 'little') + 8 == len(data))
    if kind == 'png':
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):
            return False
        pos = 8
        while pos + 12 <= len(data):
            size = int.from_bytes(data[pos:pos + 4], 'big')
            end = pos + 12 + size
            if data[pos + 4:pos + 8] == b'IEND':
                return not size and end == len(data)
            pos = end
        return False
    if not data.startswith(b'\xff\xd8'):
        return False
    pos, scanning = 2, False
    while pos < len(data):
        marker = data.find(b'\xff', pos) if scanning else pos
        if marker < 0 or data[marker] != 255:
            return False
        pos = marker + 1
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos >= len(data):
            return False
        code = data[pos]
        pos += 1
        if code == 0xd9:
            return pos == len(data)
        if scanning and (code == 0 or 0xd0 <= code <= 0xd7):
            continue
        if code == 0xd8 or pos + 2 > len(data):
            return False
        size = int.from_bytes(data[pos:pos + 2], 'big')
        if size < 2:
            return False
        scanning = code == 0xda
        pos += size
    return False


def normalize_image(kind, raw):
    """Child-only decode. Store a single new RGB JPEG with fresh metadata."""
    from PIL import Image, ImageOps
    from backend.multimodal.image_processor import ImageProcessor, ImageValidationError

    if not _complete_container(kind, raw):
        raise ImageValidationError('Invalid image container.', 'malformed_image')
    ImageProcessor(Path('.')).process(raw, f'upload.{kind}', max_size_bytes=MAX_IMAGE_BYTES,
                                      max_dimension=MAX_DIMENSION, save_to_disk=False)
    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        pixels = ImageOps.exif_transpose(image).convert('RGBA')
        pixels.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        clean = Image.new('RGB', pixels.size, 'white')
        clean.paste(pixels, mask=pixels.getchannel('A'))
        output = io.BytesIO()
        clean.save(output, 'JPEG', quality=80)
    jpeg = output.getvalue()
    if len(jpeg) > MAX_MODEL_BYTES:
        raise ImageValidationError('Normalized image exceeds the input limit.', 'oversized')
    return width, height, base64.b64encode(jpeg).decode('ascii')


class LocalImageRef(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    image_id: str
    filename: str = Field(max_length=128)
    format: str
    size_bytes: int = Field(ge=1, le=MAX_IMAGE_BYTES)
    width: int = Field(ge=1, le=MAX_DIMENSION)
    height: int = Field(ge=1, le=MAX_DIMENSION)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    normalized_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

    @field_validator('image_id')
    @classmethod
    def image_uuid(cls, value):
        parsed = UUID(value)
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError('Invalid image ID')
        return value

    def attachment(self, available):
        return {'attachment_id': self.image_id, 'filename': self.filename, 'mime_type': f'image/{self.format}',
                'size_bytes': self.size_bytes, 'width': self.width, 'height': self.height, 'sha256': self.sha256,
                'available': available}


def _validated_jpeg(encoded):
    if not isinstance(encoded, str) or len(encoded) > (MAX_MODEL_BYTES + 2) // 3 * 4:
        raise ValueError('Invalid normalized image')
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > MAX_MODEL_BYTES or not _complete_container('jpeg', raw):
        raise ValueError('Invalid normalized image')
    return raw


def prepare_image(filename, kind, raw):
    from backend.rag.local_documents import run_local_parser

    try:
        code, output = run_local_parser(f'image-{kind}', raw)
    except TextFileError as exc:
        raise TextFileError('Local image preprocessing timed out.' if exc.status == 422
                            else 'Local image preprocessing is unavailable.', status=exc.status) from None
    if code == 4:
        raise TextFileError('The image exceeds a local pixel or model input limit.', status=413)
    if code == 5:
        raise TextFileError('The local image decoder is unavailable.', status=503)
    if code:
        raise TextFileError('The image is malformed or contains unsupported content.', status=422)
    try:
        if len(output) > _MAX_SNAPSHOT:
            raise ValueError
        width, height, encoded = json.loads(output)
        ref = LocalImageRef(
            image_id=str(uuid4()), filename=filename, format=kind, size_bytes=len(raw), width=width, height=height,
            sha256=hashlib.sha256(raw).hexdigest(), normalized_sha256=hashlib.sha256(_validated_jpeg(encoded)).hexdigest(),
        )
    except (TypeError, ValueError, RecursionError):
        raise TextFileError('Local image preprocessing returned an invalid result.', status=503) from None
    return ref, encoded


class LocalImages:
    """Owned immutable image bytes; the conversation stores only signed references."""

    def __init__(self, owner, path=None, keys=None):
        self.owner, self.path, self.keys = owner, path, keys
        self._images = {}
        if path is not None and _safe_path(path):
            with path.open('rb') as source:
                raw = source.read(_MAX_SNAPSHOT + 1)
            try:
                if len(raw) > _MAX_SNAPSHOT:
                    raise ValueError
                envelope = _strict_json(raw)
                payload = envelope['payload']
                key = keys.verification_key(envelope['key'])
                if (key is None or not hmac.compare_digest(envelope['mac'], self._mac(key, payload))
                        or payload['owner'] != owner or len(payload['images']) > MAX_IMAGES):
                    raise ValueError
                for session, generation, data, encoded in payload['images']:
                    ref = LocalImageRef.model_validate(data)
                    if (not isinstance(session, str) or not isinstance(generation, str)
                            or ref.image_id in self._images or image_format(ref.filename) != ref.format
                            or hashlib.sha256(_validated_jpeg(encoded)).hexdigest() != ref.normalized_sha256):
                        raise ValueError
                    self._images[ref.image_id] = (session, generation, ref, encoded)
            except (KeyError, TypeError, ValueError, RecursionError, TextFileError):
                raise ValueError('Invalid durable local image storage') from None

    @staticmethod
    def _encode(value):
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')

    @classmethod
    def _mac(cls, key, payload):
        return hmac.new(key, b'sovereignai-local-images-v1\0' + cls._encode(payload), hashlib.sha256).hexdigest()

    def _save(self, images):
        if self.path is not None:
            payload = {'owner': self.owner, 'images': [
                [session, generation, ref.model_dump(), content]
                for session, generation, ref, content in images.values()
            ]}
            key_id, key = self.keys.signing_key()
            write_local_snapshot(self.path, self._encode({'payload': payload, 'key': key_id, 'mac': self._mac(key, payload)}))
        self._images = images

    def list(self, session, identity):
        if identity[0] != self.owner:
            raise PermissionError('Image unavailable')
        return [ref for sid, generation, ref, _ in self._images.values() if (sid, generation) == (session, identity[1])]

    def register(self, session, identity, ref, encoded):
        for previous in self.list(session, identity):
            if (previous.filename, previous.sha256) == (ref.filename, ref.sha256):
                return previous
        if len(self._images) >= MAX_IMAGES:
            raise TextFileError('Local image storage is full. Remove an attachment and try again.', status=413)
        self._save({**self._images, ref.image_id: (session, identity[1], ref, encoded)})
        return ref

    def get(self, session, identity, image_id):
        entry = self._images.get(image_id)
        if entry is None or identity[0] != self.owner or entry[:2] != (session, identity[1]):
            raise KeyError(image_id)
        return entry[2:]

    def remove(self, session, identity, image_id):
        self.get(session, identity, image_id)
        self._save({key: entry for key, entry in self._images.items() if key != image_id})

    def remove_session(self, session):
        images = {key: entry for key, entry in self._images.items() if entry[0] != session}
        if len(images) != len(self._images):
            self._save(images)

    def prune_revoked(self, records):
        active = {(row['conversation_id'], row['generation']) for row in records
                  if row['state'] == 'active' and row['owner_id'] == self.owner}
        images = {key: entry for key, entry in self._images.items() if entry[:2] in active}
        if len(images) != len(self._images):
            self._save(images)


def refers_to_image(prompt):
    return re.search(
        r'\b(?:this|that|attached|earlier|previous|same|above)\s+(?:image|photo|picture)\b'
        r'|^\s*(?:describe|analy[sz]e)\s+(?:the\s+)?(?:image|photo|picture)\b', prompt, re.I,
    ) is not None
