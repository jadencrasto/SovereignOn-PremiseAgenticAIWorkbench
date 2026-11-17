"""Bounded UTF-8 input for transient preview and local chat."""
import re
import unicodedata
from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from backend.multimodal.json_file import JsonTable

MAX_TEXT_FILE_BYTES = 2048
MAX_TEXT_FILE_CHARS = 1200
TEXT_PREVIEW_CHARS = 800
MAX_TEXT_FORM_BYTES = MAX_TEXT_FILE_BYTES + 8192
MAX_TEXT_QUESTION_BYTES = 1024
MAX_TEXT_SOURCES = 3
MAX_TEXT_SOURCE_BYTES = 2 * MAX_TEXT_FILE_BYTES
TEXT_ATTACHMENT_EXTENSIONS = {'text/plain': '.txt', 'text/csv': '.csv', 'application/json': '.json'}


class TextFileError(ValueError):
    def __init__(self, message: str, *, status: int = 422, code: str = "TEXT_FILE_INVALID"):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class TextSource:
    """One validated input, scoped to an authorized request."""

    number: int
    filename: str
    size_bytes: int
    csv: bool
    content: 'str | tuple[list[str], list[list[str]]] | JsonTable'

    @property
    def mime_type(self) -> str:
        if self.filename.lower().endswith('.json'):
            return 'application/json'
        return 'text/csv' if self.csv else 'text/plain'

    @property
    def tabular(self) -> bool:
        return self.mime_type != 'text/plain'


_SOURCE_PREFIX = re.compile(r'^use[ \t]+([^:\r\n]+):\s*(.*)$', re.IGNORECASE | re.DOTALL)
_SOURCE_REFERENCE = re.compile(
    r'\.(?:csv|txt|json)\b|\bcross[- ]file\b|\bsource\s+[0-9]+\b|'
    r'\b(?:other|another|both|all|each|multiple|two|three|second|first)\s+(?:files?|sources?|attachments?|documents?)\b|'
    r'\b(?:files|attachments|sources|spreadsheets|csvs)\b', re.IGNORECASE,
)


def resolve_text_source(sources: list[TextSource], value: str) -> TextSource:
    """Resolve an explicit ordinal or unique original name within this upload."""
    value = value.strip()
    if ordinal := re.fullmatch(r'(?:source\s+)?([1-9][0-9]?)', value, re.IGNORECASE):
        matches = [source for source in sources if source.number == int(ordinal[1])]
    else:
        matches = [source for source in sources if source.filename.casefold() == value.casefold()]
    if len(matches) > 1:
        raise TextFileError('More than one source has that filename. Choose its source number.',
                            code='TEXT_SOURCE_AMBIGUOUS')
    if not matches:
        raise TextFileError('Choose a source from the files attached to this request.', code='TEXT_SOURCE_INVALID')
    return matches[0]


def select_text_source(sources: list[TextSource], selector: str | None, question: str) -> tuple[TextSource, str]:
    selected = resolve_text_source(sources, selector) if selector is not None else None
    if prefix := _SOURCE_PREFIX.fullmatch(question):
        requested = resolve_text_source(sources, prefix[1].strip().strip('"'))
        if selected is not None and selected is not requested:
            raise TextFileError('The selected source differs from the one named in the question.',
                                code='TEXT_SOURCE_CONFLICT')
        selected, question = requested, prefix[2].strip()
    if _SOURCE_REFERENCE.search(question):
        raise TextFileError('Ask to compare the CSV or JSON against the TXT file, or ask about one selected source.',
                            code='TEXT_CROSS_SOURCE_UNSUPPORTED')
    if selected is None:
        if len(sources) != 1:
            raise TextFileError('Choose one source for this question or begin with Use filename: question.',
                                code='TEXT_SOURCE_REQUIRED')
        selected = sources[0]
    if not question:
        raise TextFileError('Enter a question for the selected source.', code='TEXT_QUESTION_INVALID')
    return selected, question


def text_filename(filename: str, extension: str = '.txt') -> str:
    if not filename.lower().endswith(extension):
        raise TextFileError(f"Choose a {extension} file.", status=415, code="TEXT_FILE_TYPE")
    if (len(filename) > 128 or not filename[:-len(extension)].strip()
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
    if not isinstance(item, dict) or item.get("mime_type") not in tuple(TEXT_ATTACHMENT_EXTENSIONS):
        return None
    filename, size = item.get("filename"), item.get("size_bytes")
    if not isinstance(filename, str) or type(size) is not int or not 0 < size <= MAX_TEXT_FILE_BYTES:
        return None
    try:
        mime = item["mime_type"]
        return {
            "attachment_id": str(UUID(str(item.get("attachment_id")))),
            "filename": text_filename(filename, TEXT_ATTACHMENT_EXTENSIONS[mime]),
            "mime_type": mime,
            "size_bytes": size,
        }
    except ValueError:
        return None
