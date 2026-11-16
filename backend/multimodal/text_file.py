"""Bounded UTF-8 input for transient preview and local chat."""
import re
import unicodedata
from io import StringIO
from uuid import UUID

MAX_TEXT_FILE_BYTES = 2048
MAX_TEXT_FILE_CHARS = 1200
TEXT_PREVIEW_CHARS = 800
MAX_TEXT_FORM_BYTES = MAX_TEXT_FILE_BYTES + 8192
MAX_TEXT_QUESTION_BYTES = 1024


class TextFileError(ValueError):
    def __init__(self, message: str, *, status: int = 422, code: str = "TEXT_FILE_INVALID"):
        super().__init__(message)
        self.status = status
        self.code = code


def text_filename(filename: str, extension: str = '.txt') -> str:
    if not filename.lower().endswith(extension):
        raise TextFileError(f"Choose a {extension} file.", status=415, code="TEXT_FILE_TYPE")
    if (len(filename) > 128 or not filename[:-4].strip()
            or any(char in '<>:"/\\|?*' or unicodedata.category(char).startswith("C") for char in filename)):
        raise TextFileError("The text filename is invalid.")
    return filename


def decode_text_file(data: bytes, media_type: str) -> str:
    kind, _, parameters = media_type.lower().partition(";")
    charset = parameters.strip().replace(" ", "")
    if (kind.strip() not in ("text/plain", "application/octet-stream")
            or charset not in ("", "charset=utf-8", 'charset="utf-8"')):
        raise TextFileError("The upload must use a plain-text file type.", status=415, code="TEXT_FILE_TYPE")
    if len(data) > MAX_TEXT_FILE_BYTES:
        raise TextFileError("The text file exceeds 2 KiB.", status=413, code="TEXT_FILE_TOO_LARGE")
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        raise TextFileError("The file must be valid UTF-8.") from None
    if not text.strip():
        raise TextFileError("The text file is empty.")
    if len(text) > MAX_TEXT_FILE_CHARS:
        raise TextFileError("The text file exceeds 1,200 characters.", status=413, code="TEXT_FILE_TOO_LARGE")
    if (text.startswith(("%PDF-", "GIF87a", "GIF89a"))
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cn")
                   and char not in "\t\r\n\u200c\u200d" for char in text)):
        raise TextFileError("The file contains binary data or unsupported control characters.")
    return text


def select_text_lines(text: str, start: str | None, end: str | None) -> tuple[str, int]:
    """Select inclusive, one-based lines from validated text without rewriting line endings."""
    with StringIO(text, newline="") as source:
        lines = source.readlines()
    if start is None and end is None:
        return text, len(lines)
    if start is not None and end is not None and all(re.fullmatch(r"[1-9][0-9]{0,3}", bound) for bound in (start, end)):
        first, last = int(start), int(end)
        if first <= last <= len(lines):
            selected = "".join(lines[first - 1:last])
            if selected.strip():
                return selected, len(lines)
    raise TextFileError("Choose a valid line range containing text.", code="TEXT_FILE_RANGE_INVALID")


def text_understanding_enabled(settings) -> bool:
    """Use only the existing authenticated, local development chat profile."""
    return bool(
        getattr(settings, "local_chat_only", False)
        and getattr(settings, "auth_enabled", False)
        and getattr(settings, "app_env", "").lower() in ("development", "dev")
        and not getattr(settings, "auth_dev_override", False)
        and not getattr(settings, "allow_dev_clearance_simulation", False)
    )


def safe_text_attachment(item):
    """Project only validated text attachment metadata into history."""
    if not isinstance(item, dict) or item.get("mime_type") not in ("text/plain", "text/csv"):
        return None
    filename, size = item.get("filename"), item.get("size_bytes")
    if not isinstance(filename, str) or type(size) is not int or not 0 < size <= MAX_TEXT_FILE_BYTES:
        return None
    try:
        mime = item["mime_type"]
        return {
            "attachment_id": str(UUID(str(item.get("attachment_id")))),
            "filename": text_filename(filename, ".csv" if mime == "text/csv" else ".txt"),
            "mime_type": mime,
            "size_bytes": size,
        }
    except ValueError:
        return None
