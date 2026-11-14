"""Isolated multimodal transformations; not an authorization or public API.

Callers must supply an independently authenticated scope and revalidation.
There is no production route using this module. Do not persist these results as
trusted provenance or use caller-controlled scope assertions for authorization.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from backend.multimodal.image_processor import ImageProcessor
from backend.multimodal.service import MultimodalService
from backend.multimodal.spreadsheet import parse_spreadsheet, numeric_summary
from backend.rag.ingest import DocumentParser

MAX_INPUTS = 4
MAX_TOTAL_BYTES = 12 * 1024 * 1024
MAX_CONTEXT_CHARS = 20000


@dataclass(frozen=True)
class InputRecord:
    input_id: str
    owner_id: str
    conversation_id: str
    filename: str
    kind: str
    sha256: str
    status: str
    error: str | None = None


@dataclass(frozen=True)
class InputResult:
    record: InputRecord
    value: object | None


@dataclass(frozen=True)
class WorkflowResult:
    status: str
    inputs: tuple[InputResult, ...]
    dependencies: tuple[str, ...]


def _kind(filename):
    suffix = Path(filename).suffix.lower()
    if suffix in ('.png', '.jpg', '.jpeg', '.webp'): return 'image'
    if suffix in ('.pdf', '.docx', '.txt', '.md'): return 'document'
    if suffix in ('.csv', '.xlsx'): return 'spreadsheet'
    return 'unsupported'


async def process_inputs(question: str, files: Sequence[tuple[str, bytes]], *, owner_id: str,
                         conversation_id: str, allowed: Callable[[str, str], bool],
                         image_processor: ImageProcessor, router=None, spreadsheet_column: int = 1) -> WorkflowResult:
    """Process one request; no storage, routes, model authorization or persistence.

    `allowed` represents a hypothetical authoritative current conversation check.
    Synthetic callbacks do not provide real document authorization. This helper
    intentionally has no production wiring until that boundary exists.
    """
    if (not isinstance(question, str) or not question.strip() or len(question) > 2000
        or not isinstance(owner_id, str) or not owner_id.strip()
        or not isinstance(conversation_id, str) or not conversation_id.strip()
        or not callable(allowed) or allowed(owner_id, conversation_id) is not True):
        raise PermissionError('Conversation authorization unavailable')
    if not isinstance(files, (list, tuple)) or not 1 <= len(files) <= MAX_INPUTS:
        raise ValueError('Invalid input count')
    for item in files:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
            or not isinstance(item[0], str) or not item[0] or len(item[0]) > 255
            or '/' in item[0] or '\\' in item[0] or not isinstance(item[1], bytes)):
            raise ValueError('Invalid input structure')
    if sum(len(item[1]) for item in files) > MAX_TOTAL_BYTES:
        raise ValueError('Combined input limit exceeded')

    results = []
    context_size = 0
    for filename, data in files:
        if allowed(owner_id, conversation_id) is not True:
            raise PermissionError('Conversation authorization unavailable')
        kind = _kind(filename)
        record_args = dict(input_id=uuid.uuid4().hex, owner_id=owner_id,
                           conversation_id=conversation_id, filename=filename, kind=kind,
                           sha256=hashlib.sha256(data).hexdigest())
        try:
            if kind == 'image':
                if router is None: raise RuntimeError('vision_unavailable')
                image = image_processor.process(data, filename, save_to_disk=False)
                provider, model = router.resolve_vision_model()
                value = await MultimodalService(provider, model).analyze_image(image.base64_data, question)
                if len(value) > MAX_CONTEXT_CHARS: raise ValueError('output_limit')
                value = {'interpretation': value, 'classification': 'model_generated',
                         'width': image.width, 'height': image.height}
                context_size += len(value['interpretation'])
            elif kind == 'document':
                document = DocumentParser.parse(filename, data)
                context_size += len(document.text)
                value = {'text': document.text, 'parser_document_id': document.document_id}
            elif kind == 'spreadsheet':
                spreadsheet = parse_spreadsheet(filename, data)
                value = {'sheets': tuple({'name': sheet.name, 'summary': numeric_summary(sheet, spreadsheet_column)}
                                          for sheet in spreadsheet.sheets)}
                context_size += len(str(value))
            else:
                raise ValueError('unsupported')
            if context_size > MAX_CONTEXT_CHARS: raise ValueError('context_limit')
            results.append(InputResult(InputRecord(**record_args, status='completed'), value))
        except asyncio.CancelledError:
            raise
        except Exception:
            results.append(InputResult(InputRecord(**record_args, status='failed', error='processing_unavailable'), None))
    if allowed(owner_id, conversation_id) is not True:
        raise PermissionError('Conversation authorization unavailable')
    if any(item.record.status != 'completed' for item in results):
        # Prevent accidental use of partial content in an apparent success.
        return WorkflowResult('failed', tuple(InputResult(item.record, None) for item in results), ())
    return WorkflowResult('completed', tuple(results), tuple(item.record.input_id for item in results))
