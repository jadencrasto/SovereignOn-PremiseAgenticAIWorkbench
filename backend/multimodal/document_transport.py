"""Internal transport for independently authorized byte sources. No route or storage."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import warnings
from collections.abc import Callable
from contextlib import ExitStack, aclosing, asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import PurePath
from threading import get_ident
from uuid import uuid4

from python_multipart.exceptions import MultipartParseError
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect, Request

from backend.auth.models import (
    ClearanceLevel, Permission, has_permission, is_clearance_sufficient, parse_clearance,
    resolve_user_clearance,
)
from backend.auth.request_context import cap_clearance, dispatch_identity_resolver, effective_owner, request_tools_enabled
from backend.multimodal import spreadsheet
from backend.multimodal.document_evidence import ExtractionBudget, ExtractionLimit, valid_source_filename
from backend.multimodal.document_reasoning import ReasoningError, Source, open_document_reasoning
from backend.multimodal.openxml_limits import validate_openxml
from backend.multimodal.pdf_limits import MAX_PDF_BYTES, open_bounded_pdf
from backend.multimodal.workflow import MAX_INPUTS, MAX_TOTAL_BYTES
from backend.rag.ingest import DocumentParser

_SECONDS = 15
_active_transport = ContextVar('active_document_transport', default=None)
_FRAMING = 16 * 1024
_TYPES = {
    '.pdf': ('application/pdf', MAX_PDF_BYTES),
    '.docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              DocumentParser.MAX_INPUT_BYTES),
    '.xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', spreadsheet.MAX_BYTES),
}
_CONTENT_TYPE = re.compile(r'multipart/form-data;\s*boundary=(?:"([A-Za-z0-9._-]{1,70})"|([A-Za-z0-9._-]{1,70}))', re.I)


@dataclass(frozen=True, slots=True)
class DocumentGrant:
    """Ordered server policy decision for exact source bytes. Never take grants from the form."""
    filename: str
    size: int
    sha256: str = field(repr=False)
    clearance: ClearanceLevel
    _issuer: object | None = field(default=None, repr=False)
    _handle: DocumentHandle | None = field(default=None, repr=False)


SourceAuthority = Callable[[str, str], tuple[DocumentGrant, ...]]


def has_active_transport() -> bool:
    return _active_transport.get() is not None


def _error(code: str) -> ReasoningError:
    messages = {
        'AUTHORIZATION_UNAVAILABLE': 'Current document access could not be verified.',
        'INVALID_FORM': 'Supply the authorized files in a complete multipart request.',
        'INPUT_LIMIT': 'Document transport limits were exceeded.',
        'SOURCE_UNAVAILABLE': 'The supplied source did not match its authorization.',
        'EXTRACTION_FAILED': 'The supplied document structure could not be verified.',
        'REQUEST_CLOSED': 'The document request has closed.',
        'CANCELLED': 'The document request was cancelled.',
        'TIMEOUT': 'The document request exceeded its deadline.',
        'PARSER_DIAGNOSTICS_UNAVAILABLE': 'Protected decoding requires an isolated warning-safe parser context.',
    }
    return ReasoningError(code, messages[code])


@contextmanager
def _private_decoder():
    """Protect synchronous library diagnostics without changing host or other threads' log levels."""
    thread = get_ident()
    packages = {'pypdf', 'pdfplumber', 'pdfminer', 'openpyxl', 'PIL', 'docx', 'fontTools'}

    def safe_record(record):
        if get_ident() == thread and record.name.partition('.')[0] in packages:
            # Python 3.13 handler filters may replace the record. Keep the severity
            # but never serialize parser arguments, exception text or custom extras.
            return logging.LogRecord(record.name, record.levelno, '', 0,
                                     'Protected document decoder diagnostic.', (), None)
        return True

    sinks = set(logging.getLogger().handlers)
    for name, logger in logging.root.manager.loggerDict.copy().items():
        if name.partition('.')[0] in packages and isinstance(logger, logging.Logger):
            sinks.update(logger.handlers)
    if logging.lastResort:
        sinks.add(logging.lastResort)
    with ExitStack() as filters:
        for sink in sinks:
            sink.addFilter(safe_record)
            filters.callback(sink.removeFilter, safe_record)
        yield


def _document_deadline(conversation_id, deadline):
    if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 128:
        raise _error('AUTHORIZATION_UNAVAILABLE')
    maximum = asyncio.get_running_loop().time() + _SECONDS
    if deadline is not None and (type(deadline) not in (float, int)
                                 or (type(deadline) is float and not math.isfinite(deadline))):
        raise _error('TIMEOUT')
    return maximum if deadline is None else min(maximum, deadline)


class _DocumentRequestAccess:
    def __init__(self, request, conversation, cancelled, deadline):
        self.request = request
        self.conversation = conversation
        self.cancelled = cancelled
        self.deadline = deadline
        self.task = asyncio.current_task()
        self.owner = effective_owner.get()
        self.binding = getattr(request.state, 'authenticated_session', None)
        self.identity = None
        self.engine = None
        self.account = None
        self.agent_engine = None
        self.closed = False

    def lifetime(self):
        if self.closed or asyncio.current_task() is not self.task:
            raise _error('REQUEST_CLOSED')
        try:
            cancelled = self.task.cancelling() or self.cancelled() is not False
        except Exception:
            cancelled = True
        if self.closed:
            raise _error('REQUEST_CLOSED')
        if cancelled or asyncio.get_running_loop().time() >= self.deadline:
            self.closed = True
            raise _error('CANCELLED' if cancelled else 'TIMEOUT')

    def check(self):
        self.lifetime()
        try:
            state = self.request.app.state
            if (state.settings.auth_enabled is not True or not self.binding
                    or (self.engine is not None and self.engine is not state.engine)
                    or self.binding is not self.request.state.authenticated_session
                    or self.binding[0]() is not self.request
                    or self.binding[1] is not dispatch_identity_resolver.get()
                    or (self.agent_engine is not None and
                        (self.agent_engine is not state.engine or request_tools_enabled.get() is not True))
                    or not state.engine._memory.persistence_configured):
                raise PermissionError
            user, identity = state.engine._current_dispatch_identity(self.conversation, self.owner)
            declared = parse_clearance(user.clearance)
            if (not has_permission(user.role, Permission.EXECUTE_READ_TOOLS) or not declared
                    or (self.identity is not None and identity != self.identity)):
                raise PermissionError
            clearance = cap_clearance(resolve_user_clearance(user))
            account = (user.role, declared, clearance)
            if self.account is not None and self.account != account:
                raise PermissionError
            self.identity, self.account, self.engine = identity, account, state.engine
        except Exception:
            self.closed = True
            raise _error('AUTHORIZATION_UNAVAILABLE') from None
        self.lifetime()
        return clearance


class _Access(_DocumentRequestAccess):
    def __init__(self, request, conversation, authority, cancelled, deadline, issuer=None):
        super().__init__(request, conversation, cancelled, deadline)
        self.authority = authority
        self.issuer = issuer
        self.grants = ()
        self.check()

    def check(self):
        clearance = super().check()
        try:
            grants = self.authority(self.owner, self.conversation)
            if type(grants) is not tuple or not 1 <= len(grants) <= MAX_INPUTS:
                raise PermissionError
            total = 0
            for grant in grants:
                if (type(grant) is not DocumentGrant or not valid_source_filename(grant.filename)
                        or grant._issuer is not self.issuer
                        or (grant._handle is None) != (self.issuer is None)
                        or not isinstance(grant.clearance, ClearanceLevel)
                        or not is_clearance_sufficient(clearance, grant.clearance)
                        or not isinstance(grant.sha256, str) or not re.fullmatch('[0-9a-f]{64}', grant.sha256)
                        or type(grant.size) is not int or grant.size <= 0):
                    raise PermissionError
                spec = _TYPES.get(PurePath(grant.filename).suffix.lower())
                if spec is None or grant.size > spec[1]:
                    raise PermissionError
                total += grant.size
            if total > MAX_TOTAL_BYTES or (self.grants and self.grants != grants):
                raise PermissionError
            self.grants = grants
        except Exception:
            self.closed = True
            raise _error('AUTHORIZATION_UNAVAILABLE') from None
        self.lifetime()


class _Parts(MultiPartParser):
    """Use Starlette's streaming multipart decoder, with bounded memory-only file writes."""
    spool_max_size = MAX_TOTAL_BYTES

    def __init__(self, request, stream, grants):
        super().__init__(request.headers, stream, max_files=len(grants), max_fields=0)
        self.grants = grants
        self.total = self.part_size = 0
        self.complete = False

    def on_headers_finished(self):
        i = self._current_files
        if i >= len(self.grants):
            raise _error('INPUT_LIMIT')
        grant = self.grants[i]
        headers = self._current_part.item_headers
        if sorted(key for key, _ in headers) != [b'content-disposition', b'content-type']:
            raise _error('INVALID_FORM')
        # Verify the original wire filename before the multipart library can strip a path.
        disposition = re.fullmatch(rb'form-data;\s*name="file";\s*filename="([^"\\\r\n]+)"',
                                   self._current_part.content_disposition)
        mime = _TYPES[PurePath(grant.filename).suffix.lower()][0]
        if (not disposition or disposition[1] != grant.filename.encode('utf-8')
                or dict(headers)[b'content-type'].lower() != mime.encode('ascii')):
            raise _error('SOURCE_UNAVAILABLE')
        super().on_headers_finished()
        if self._current_part.file is None or self._current_part.file.filename != grant.filename:
            raise _error('SOURCE_UNAVAILABLE')
        self.part_size = 0

    def on_part_data(self, data, start, end):
        size = end - start
        self.part_size += size
        self.total += size
        if self.part_size > self.grants[self._current_files - 1].size or self.total > MAX_TOTAL_BYTES:
            raise _error('INPUT_LIMIT')
        super().on_part_data(data, start, end)

    def on_part_end(self):
        if self.part_size != self.grants[self._current_files - 1].size:
            raise _error('SOURCE_UNAVAILABLE')
        super().on_part_end()

    def on_end(self):
        self.complete = True

    def close(self):
        try:
            with ExitStack() as spools:
                for upload in reversed(self._files_to_close_on_error):
                    spools.callback(upload.close)
        finally:
            self._files_to_close_on_error.clear()
            self.items.clear()
            self._file_parts_to_write.clear()
            self._file_parts_to_finish.clear()


async def _read(request, access):
    types = request.headers.getlist('content-type')
    match = _CONTENT_TYPE.fullmatch(types[0]) if len(types) == 1 else None
    if not match or hasattr(request, '_body') or getattr(request, '_form', None) is not None:
        raise _error('INVALID_FORM')
    boundary = (match[1] or match[2]).encode('ascii')
    closing = b'\r\n--' + boundary + b'--'
    limit = sum(g.size for g in access.grants) + _FRAMING
    lengths = request.headers.getlist('content-length')
    if (lengths and 'transfer-encoding' in request.headers) or 'content-encoding' in request.headers:
        raise _error('INVALID_FORM')
    if lengths and (len(lengths) != 1 or not re.fullmatch('[0-9]{1,9}', lengths[0]) or int(lengths[0]) > limit):
        raise _error('INPUT_LIMIT')

    async def chunks():
        total = consumed = 0
        tail = b''
        suffix = None
        next_check = asyncio.get_running_loop().time() + 0.25
        async with aclosing(request.stream()) as body:
            async for chunk in body:
                access.lifetime()
                now = asyncio.get_running_loop().time()
                if now >= next_check:
                    access.check()
                    next_check = now + 0.25
                total += len(chunk)
                if total > limit:
                    raise _error('INPUT_LIMIT')
                for offset in range(0, len(chunk), 16384):
                    piece = chunk[offset:offset + 16384]
                    # The decoder allows epilogues. This transport requires one final delimiter.
                    if suffix is None:
                        window = tail + piece
                        pos = window.find(closing)
                        if pos >= 0:
                            suffix = window[pos + len(closing):]
                        tail = window[-(len(closing) - 1):]
                    else:
                        suffix += piece
                    if suffix is not None and suffix not in (b'', b'\r', b'\r\n'):
                        raise _error('INVALID_FORM')
                    yield piece
                    consumed += len(piece)
                    if consumed - parser.total > _FRAMING:
                        raise _error('INPUT_LIMIT')
        if suffix not in (b'', b'\r\n') or (lengths and int(lengths[0]) != total):
            raise _error('INVALID_FORM')

    async with aclosing(chunks()) as stream:
        parser = _Parts(request, stream, access.grants)
        try:
            form = await parser.parse()
            items = form.multi_items()
            if not parser.complete or len(items) != len(access.grants):
                raise _error('INVALID_FORM')
            access.check()
            files = []
            for grant, (_, upload) in zip(access.grants, items, strict=True):
                data = await upload.read(grant.size + 1)
                if len(data) != grant.size or hashlib.sha256(data).hexdigest() != grant.sha256:
                    raise _error('SOURCE_UNAVAILABLE')
                files.append((grant.filename, data))
            return tuple(files)
        except ClientDisconnect:
            raise _error('CANCELLED') from None
        except (MultiPartException, MultipartParseError):
            raise _error('INVALID_FORM') from None
        finally:
            parser.close()


def _preflight(files, access):
    for name, content in files:
        access.check()
        remaining = access.deadline - asyncio.get_running_loop().time()
        budget = ExtractionBudget(min(DocumentParser.EXTRACTION_SECONDS, remaining), DocumentParser.MAX_OUTPUT_CHARS)
        suffix = PurePath(name).suffix.lower()
        try:
            if suffix == '.pdf':
                if not content.startswith(b'%PDF-'):
                    raise ValueError
                with _private_decoder():
                    open_bounded_pdf(content, budget).stream.close()
            elif suffix == '.docx':
                validate_openxml(content, 'word/document.xml', DocumentParser.MAX_DOCX_UNCOMPRESSED_BYTES, budget)
            else:
                validate_openxml(content, 'xl/workbook.xml', spreadsheet.MAX_EXPANDED_BYTES, budget,
                                 sheet_limit=spreadsheet.MAX_SHEETS)
        except ExtractionLimit:
            access.lifetime()
            raise _error('INPUT_LIMIT') from None
        except Exception:
            # Leave the exception handler before raising. Even __context__ must
            # not carry the third-party diagnostic across the transport boundary.
            pass
        else:
            continue
        raise _error('EXTRACTION_FAILED')


@dataclass(frozen=True, slots=True, eq=False)
class DocumentHandle:
    request_id: str
    source_id: str
    filename: str
    format: str


class DocumentTransport:
    """A single-task lease. Ingress and decoder activation have separate lifetimes."""
    def __init__(self, access, files):
        handles = tuple(grant._handle for grant in access.grants) if access.issuer is not None else ()
        self.request_id = handles[0].request_id if handles else uuid4().hex
        self._access = access
        self._files = files
        self._stack = ExitStack()
        self._scope = None
        self._agent_claimed = False
        self._evidence_released = False
        self._sources = handles or tuple(
            DocumentHandle(self.request_id, uuid4().hex, name, PurePath(name).suffix[1:].lower()) for name, _ in files
        )
        self._extracted = {}
        if len({s.source_id for s in self._sources}) != len(files):
            raise _error('SOURCE_UNAVAILABLE')

    def sources(self) -> tuple[DocumentHandle, ...]:
        self.check()
        return self._sources

    def source(self, source: DocumentHandle) -> Source:
        if not any(item is source for item in self.sources()):
            raise _error('SOURCE_UNAVAILABLE')
        self.reasoning()
        return self._extracted[source]

    def resolve(self, source: DocumentHandle) -> str:
        return self.source(source).source_id

    @contextmanager
    def agent_scope(self, engine, handles=None):
        """Consume this lease once. Optional handles assert the exact plan source order."""
        if _active_transport.get() is not self or self._agent_claimed:
            raise _error('REQUEST_CLOSED')
        self._agent_claimed = True
        try:
            self.check()
            self._access.agent_engine = engine
            self.check()
            if handles is not None and (type(handles) is not tuple or len(handles) != len(self._sources)
                    or any(actual is not issued for actual, issued in zip(handles, self._sources))):
                raise _error('SOURCE_UNAVAILABLE')
            yield self
        finally:
            self._close()

    def check(self):
        """Revalidate immediately before handing evidence to its authorized consumer."""
        if self._access is None:
            raise _error('REQUEST_CLOSED')
        try:
            if self._agent_claimed and _active_transport.get() is not self:
                raise _error('REQUEST_CLOSED')
            if self._scope is None:
                self._access.check()
            else:
                # Recheck the transport deadline before the reasoner's own guard,
                # which intentionally collapses authorization callback failures.
                self._access.lifetime()
                self._scope._check()
        except ReasoningError:
            self._close()
            raise

    def check_agent_release(self):
        self.check()
        if not self._agent_claimed:
            raise _error('REQUEST_CLOSED')
        self._evidence_released = True

    def require_buffered_agent_result(self):
        """Check that the closed reader prepared evidence for this transport's final handoff."""
        if _active_transport.get() is not self or not self._evidence_released or self._access is not None:
            raise _error('REQUEST_CLOSED')

    def reasoning(self):
        """Open the existing scoped reader once, only in a warning-safe decoder runtime."""
        self.check()
        if self._scope is not None:
            return self._scope
        access = self._access

        def allowed(owner, conversation):
            access.check()
            return (owner, conversation) == (access.owner, access.conversation)

        try:
            # openpyxl can include cell contents in Python warnings (and their module
            # warning cache). Python 3.13 warning filters are shared across threads.
            # Never change host filters in a request. Fail closed before rich decoding.
            if any(source.format == 'xlsx' for source in self._sources) and not _safe_workbook_warnings():
                raise _error('PARSER_DIAGNOSTICS_UNAVAILABLE')
            scope = self._decode(access, allowed)
            self._files = ()
            sources = scope.sources()
            if len(sources) != len(self._sources) or any(
                (s.filename, s.format) != (h.filename, h.format) for h, s in zip(self._sources, sources)
            ):
                raise _error('SOURCE_UNAVAILABLE')
            self._extracted = dict(zip(self._sources, sources, strict=True))
            self._scope = scope
            self.check()
        except ReasoningError:
            self._close()
            if asyncio.get_running_loop().time() >= access.deadline:
                raise _error('TIMEOUT') from None
            raise
        except Exception:
            self._close()
            raise _error('EXTRACTION_FAILED') from None
        return self._scope

    def _decode(self, access, allowed):
        # Python 3.13 uses process-wide warning filters. Require the verified
        # warning-as-error policy above for XLSX, then drop the exception chain
        # locally; a decoder diagnostic never becomes a successful finding.
        try:
            with _private_decoder():
                return self._stack.enter_context(open_document_reasoning(
                    self._files, owner_id=access.owner, conversation_id=access.conversation,
                    allowed=allowed, cancelled=access.cancelled,
                ))
        except ReasoningError as exc:
            code = exc.code
        except Exception:
            code = 'EXTRACTION_FAILED'
        if code == 'EXTRACTION_LIMIT':
            raise ReasoningError(code, 'The document exceeded an extraction limit.')
        raise _error(code if code in {'AUTHORIZATION_UNAVAILABLE', 'CANCELLED', 'TIMEOUT', 'SOURCE_UNAVAILABLE'}
                     else 'EXTRACTION_FAILED')

    def _close(self):
        if self._access is not None and not self._evidence_released:
            self._access.closed = True
        self._sources = ()
        self._files = ()
        self._extracted.clear()
        try:
            self._stack.close()
        finally:
            self._scope = self._access = None


def _safe_workbook_warnings() -> bool:
    # Recognize an unshadowed error policy covering every warning from the decoder.
    if not warnings.filters:
        return False
    action, message, category, module, line = warnings.filters[0]
    return (action == 'error' and message is None and category is Warning and line == 0
            and (module is None or getattr(module, 'pattern', None) == r'^openpyxl(?:\.|$)'))


@asynccontextmanager
async def open_document_transport(request: Request, conversation_id: str, *, source_authority: SourceAuthority,
                                  cancelled: Callable[[], bool] = lambda: False, deadline: float | None = None):
    """After get_current_user, admit a fresh authenticated multipart stream, once, in this task.

    source_authority is trusted server policy for the owner and conversation, independent of this
    upload. An absent authority, changed grant or mismatched byte sequence never admits a source.
    """
    from backend.multimodal.document_source_authority import LocalSourceAuthority

    with ExitStack() as cleanup:
        issuer = None
        deadline = _document_deadline(conversation_id, deadline)
        if type(source_authority) is LocalSourceAuthority:
            issuer, authority_deadline, authority_cancelled = source_authority._claim(request, conversation_id)
            cleanup.callback(source_authority.close)
            deadline = min(deadline, authority_deadline)
            transport_cancelled = cancelled

            def cancelled():
                return transport_cancelled() is not False or authority_cancelled() is not False

        elif getattr(request.state, 'local_source_authority_used', False):
            raise _error('AUTHORIZATION_UNAVAILABLE')

        async with _open_transport(request, conversation_id, source_authority, cancelled, deadline, issuer) as lease:
            yield lease


@asynccontextmanager
async def _open_transport(request, conversation_id, source_authority, cancelled, deadline, issuer):
    access = _Access(request, conversation_id, source_authority, cancelled, deadline, issuer)
    if getattr(request.state, 'document_transport_used', False):
        raise _error('REQUEST_CLOSED')
    request.state.document_transport_used = True
    timeout = asyncio.timeout_at(deadline)
    try:
        async with timeout:
            files = await _read(request, access)
            _preflight(files, access)

            transport = DocumentTransport(access, files)
            del files
            token = _active_transport.set(transport)
            try:
                transport.check()
                yield transport
                if not transport._agent_claimed:
                    transport.check()
            finally:
                try:
                    transport._close()
                finally:
                    _active_transport.reset(token)
            # The reader is closed. Recheck policy after cleanup and before the
            # internal caller can release a buffered evidence-bearing result.
            if transport._evidence_released:
                access.check()
    except TimeoutError:
        if timeout.expired():
            raise _error('TIMEOUT') from None
        raise
    finally:
        access.closed = True
