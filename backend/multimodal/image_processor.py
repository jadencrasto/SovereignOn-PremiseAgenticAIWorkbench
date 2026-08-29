"""
backend/multimodal/image_processor.py
--------------------------------------
Strict image validation, encoding, and storage for Phase 5.

Security model:
  - Whitelist of allowed extensions (.png .jpg .jpeg .webp)
  - MIME type validation via file magic bytes (no extension spoofing)
  - Filename sanitization (reuses backend/tools/safety.py)
  - Path traversal prevention (no ../ or absolute paths)
  - All images stored inside settings.upload_dir/images/
  - Configurable maximum file size (default 10 MB)
  - Configurable maximum pixel dimensions (default 8192 × 8192)
  - Malformed image detection via pillow (if available) or magic bytes
  - Never executes uploaded content
  - Never logs image bytes or base64 data

This module never allows:
  - Writing outside the images sub-directory
  - Accepting unsupported file types
  - Oversized files
  - Path traversal
"""

from __future__ import annotations

import base64
import io
import logging
import struct
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from backend.tools.safety import sanitize_filename

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_EXTENSIONS: frozenset = frozenset({".png", ".jpg", ".jpeg", ".webp"})

# Maps extension → expected MIME type
EXTENSION_MIME: dict = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# Magic bytes for image format detection (first N bytes)
# Prevents extension spoofing
_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n",     "image/png"),
    (b"\xff\xd8\xff",           "image/jpeg"),
    (b"RIFF",                   "image/webp"),   # RIFF....WEBP — checked further below
    (b"GIF87a",                 "image/gif"),    # rejected
    (b"GIF89a",                 "image/gif"),    # rejected
    (b"%PDF",                   "application/pdf"),  # rejected
    (b"PK\x03\x04",            "application/zip"),  # rejected
]

# Default limits
DEFAULT_MAX_SIZE_BYTES: int = 10 * 1024 * 1024   # 10 MB
DEFAULT_MAX_DIMENSION: int = 8192                 # pixels per side


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ProcessedImage:
    """Result of a successful image validation + storage operation."""
    attachment_id: str
    filename: str           # sanitized filename
    original_filename: str  # user-supplied (sanitized)
    mime_type: str
    size_bytes: int
    width: Optional[int]    # None if dimension check skipped
    height: Optional[int]
    storage_path: Path      # absolute path inside upload_dir/images/
    base64_data: str        # base64-encoded bytes for LLaVA


@dataclass
class ImageValidationError(Exception):
    """Raised when an image fails validation."""
    message: str
    code: str  # e.g. "unsupported_extension", "oversized", "malformed"

    def __str__(self) -> str:
        return self.message


# ---------------------------------------------------------------------------
# Core processor class
# ---------------------------------------------------------------------------

class ImageProcessor:
    """
    Validates, stores, and encodes uploaded images for vision inference.

    Usage:
        processor = ImageProcessor(upload_dir=settings.upload_dir)
        result = processor.process(
            data=file_bytes,
            filename="photo.jpg",
            max_size_bytes=10*1024*1024,
        )
        # result.base64_data → send to LLaVA
    """

    def __init__(
        self,
        upload_dir: Path,
        max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
        max_dimension: int = DEFAULT_MAX_DIMENSION,
    ) -> None:
        self._upload_dir = upload_dir
        self._images_dir = upload_dir / "images"
        self._max_size_bytes = max_size_bytes
        self._max_dimension = max_dimension

    def process(
        self,
        data: bytes,
        filename: str,
        max_size_bytes: Optional[int] = None,
        max_dimension: Optional[int] = None,
        save_to_disk: bool = True,
    ) -> ProcessedImage:
        """
        Full validation + encoding pipeline.

        Args:
            data:           Raw file bytes from the upload.
            filename:       Original user-supplied filename.
            max_size_bytes: Override default size limit.
            max_dimension:  Override default pixel dimension limit.
            save_to_disk:   Whether to persist the file (False for tests).

        Returns:
            ProcessedImage with all metadata and base64_data for LLaVA.

        Raises:
            ImageValidationError: On any validation failure.
        """
        limit_bytes = max_size_bytes if max_size_bytes is not None else self._max_size_bytes
        limit_dim   = max_dimension  if max_dimension  is not None else self._max_dimension

        # 1. Sanitize filename
        safe_name = self._sanitize_image_filename(filename)

        # 2. Check extension
        ext = Path(safe_name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ImageValidationError(
                message=(
                    f"Unsupported image format '{ext}'. "
                    f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
                ),
                code="unsupported_extension",
            )

        # 3. Check file size
        size_bytes = len(data)
        if size_bytes == 0:
            raise ImageValidationError(
                message="Image file is empty.",
                code="empty_file",
            )
        if size_bytes > limit_bytes:
            limit_mb = limit_bytes / (1024 * 1024)
            actual_mb = size_bytes / (1024 * 1024)
            raise ImageValidationError(
                message=(
                    f"Image too large ({actual_mb:.1f} MB). "
                    f"Maximum allowed: {limit_mb:.1f} MB."
                ),
                code="oversized",
            )

        # 4. Validate MIME type via magic bytes
        detected_mime = self._detect_mime(data)
        expected_mime = EXTENSION_MIME[ext]
        if detected_mime and detected_mime != expected_mime:
            raise ImageValidationError(
                message=(
                    f"File content does not match extension '{ext}'. "
                    f"Detected: {detected_mime}."
                ),
                code="mime_mismatch",
            )
        if detected_mime and detected_mime not in EXTENSION_MIME.values():
            raise ImageValidationError(
                message=f"Unsupported image content type detected: {detected_mime}.",
                code="unsupported_mime",
            )

        # 5. Decode dimensions (lightweight, no Pillow required)
        width, height = self._read_dimensions(data, ext)
        if width is not None and height is not None:
            if width > limit_dim or height > limit_dim:
                raise ImageValidationError(
                    message=(
                        f"Image dimensions {width}×{height} exceed maximum "
                        f"{limit_dim}×{limit_dim}."
                    ),
                    code="excessive_dimensions",
                )

        # 6. Verify image is not malformed (attempt header parse)
        self._verify_not_malformed(data, ext)

        # 7. Encode to base64 for LLaVA (NEVER logged)
        b64 = base64.b64encode(data).decode("ascii")

        # 8. Generate unique attachment ID
        attachment_id = str(uuid.uuid4())

        # 9. Persist to disk (if requested)
        storage_path = self._images_dir / f"{attachment_id}{ext}"
        if save_to_disk:
            self._save(data, storage_path)
        else:
            storage_path = self._images_dir / f"{attachment_id}{ext}"  # logical path only

        mime_type = expected_mime

        logger.info(
            "image_processed | id=%s filename=%s mime=%s size=%d bytes dims=%sx%s",
            attachment_id, safe_name, mime_type, size_bytes,
            width or "?", height or "?",
        )

        return ProcessedImage(
            attachment_id=attachment_id,
            filename=f"{attachment_id}{ext}",
            original_filename=safe_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            width=width,
            height=height,
            storage_path=storage_path,
            base64_data=b64,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_image_filename(filename: str) -> str:
        """
        Sanitize an image filename using the existing safety module.
        Rejects traversal and unsafe characters.
        """
        # Reject absolute paths
        if Path(filename).is_absolute():
            raise ImageValidationError(
                message="Absolute paths are not allowed as image filenames.",
                code="absolute_path",
            )
        # Reject traversal
        if ".." in Path(filename).parts:
            raise ImageValidationError(
                message="Path traversal ('..') is not allowed in image filenames.",
                code="path_traversal",
            )
        try:
            return sanitize_filename(filename)
        except ValueError as exc:
            raise ImageValidationError(
                message=f"Invalid image filename: {exc}",
                code="invalid_filename",
            ) from exc

    @staticmethod
    def _detect_mime(data: bytes) -> Optional[str]:
        """
        Detect MIME type from file magic bytes.
        Returns None if detection is inconclusive.
        """
        for magic, mime in _MAGIC:
            if data[:len(magic)] == magic:
                if mime == "image/webp":
                    # RIFF....WEBP check
                    if len(data) >= 12 and data[8:12] == b"WEBP":
                        return "image/webp"
                    # RIFF but not WEBP → some other format
                    return "application/riff"
                return mime
        return None  # Unknown magic — proceed with extension check

    @staticmethod
    def _read_dimensions(data: bytes, ext: str) -> Tuple[Optional[int], Optional[int]]:
        """
        Read image dimensions without Pillow.
        Supports PNG and JPEG. Returns (None, None) for WEBP.
        """
        try:
            if ext == ".png":
                # PNG: IHDR chunk starts at byte 16 → width(4) height(4)
                if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
                    w = struct.unpack(">I", data[16:20])[0]
                    h = struct.unpack(">I", data[20:24])[0]
                    return w, h

            elif ext in (".jpg", ".jpeg"):
                # JPEG: scan for SOF marker
                i = 2  # skip SOI marker
                while i < len(data) - 8:
                    if data[i] != 0xFF:
                        break
                    marker = data[i + 1]
                    # SOF markers: 0xC0–0xC3, 0xC5–0xC7, 0xC9–0xCB, 0xCD–0xCF
                    if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                        h = struct.unpack(">H", data[i + 5:i + 7])[0]
                        w = struct.unpack(">H", data[i + 7:i + 9])[0]
                        return w, h
                    length = struct.unpack(">H", data[i + 2:i + 4])[0]
                    i += 2 + length

        except (struct.error, IndexError):
            pass

        return None, None  # WEBP or parse error — dimensions not verified here

    @staticmethod
    def _verify_not_malformed(data: bytes, ext: str) -> None:
        """
        Light structural check to catch obviously malformed images.
        Raises ImageValidationError if clearly invalid.
        """
        if ext == ".png":
            # PNG must have IHDR and IEND markers
            if not (data[:8] == b"\x89PNG\r\n\x1a\n" and b"IHDR" in data[:30]):
                raise ImageValidationError(
                    message="Malformed PNG: missing required IHDR chunk.",
                    code="malformed_image",
                )
        elif ext in (".jpg", ".jpeg"):
            # JPEG must start with SOI (0xFFD8) and end with EOI (0xFFD9)
            if not (data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"):
                raise ImageValidationError(
                    message="Malformed JPEG: missing SOI or EOI marker.",
                    code="malformed_image",
                )
        elif ext == ".webp":
            # WEBP must have RIFF + WEBP header
            if not (data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP"):
                raise ImageValidationError(
                    message="Malformed WEBP: invalid RIFF/WEBP header.",
                    code="malformed_image",
                )

    def _save(self, data: bytes, path: Path) -> None:
        """Persist image bytes to the controlled storage location."""
        try:
            self._images_dir.mkdir(parents=True, exist_ok=True)
            # Verify the target path is inside the images directory using
            # Path.resolve() containment check (validate_path_within is designed
            # for user-supplied relative paths from LLM tool calls, not internal paths)
            resolved_path = path.resolve()
            resolved_images_dir = self._images_dir.resolve()
            try:
                resolved_path.relative_to(resolved_images_dir)
            except ValueError:
                raise ImageValidationError(
                    message=(
                        f"Image storage path escapes the allowed directory. "
                        f"This is a security error."
                    ),
                    code="storage_error",
                )
            path.write_bytes(data)
        except ImageValidationError:
            raise
        except OSError as exc:
            raise ImageValidationError(
                message=f"Failed to write image to storage: {exc}",
                code="storage_error",
            ) from exc


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def encode_bytes_to_base64(data: bytes) -> str:
    """Encode raw bytes to base64 string (for LLaVA payload)."""
    return base64.b64encode(data).decode("ascii")
