"""Internal, process-local grants for independently approved document bytes."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import PurePath
from uuid import uuid4

from starlette.requests import Request

from backend.auth.models import ClearanceLevel, is_clearance_sufficient
from backend.auth.request_context import request_tools_enabled
from backend.multimodal.document_evidence import valid_source_filename
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_transport import (
    DocumentGrant, DocumentHandle, _DocumentRequestAccess, _TYPES, _document_deadline, _error, _preflight,
)
from backend.multimodal.workflow import MAX_INPUTS, MAX_TOTAL_BYTES


@dataclass(frozen=True, slots=True)
class ApprovedLocalSource:
    """Current server policy record for a source already approved outside the upload."""

    owner_id: str
    revision: str
    grant: DocumentGrant


# Lookup by server reference, principal, conversation and conversation generation.
# The server policy provider changes revision on replacement or reinstatement.
LocalSourceLookup = Callable[[str, str, str, str], ApprovedLocalSource | None]


def valid_source_reference(reference: str) -> bool:
    return type(reference) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', reference) is not None


@dataclass(frozen=True, slots=True)
class _Registration:
    approval: ApprovedLocalSource
    grant: DocumentGrant


class LocalSourceAuthority:
    """One authenticated request's ordered source registrations and live grant lookup."""

    def __init__(self, request: Request, conversation_id: str, lookup: LocalSourceLookup,
                 cancelled: Callable[[], bool], deadline: float | None):
        if not callable(lookup) or request_tools_enabled.get() is not True:
            raise _error('AUTHORIZATION_UNAVAILABLE')
        access = _DocumentRequestAccess(request, conversation_id, cancelled, _document_deadline(conversation_id, deadline))
        access.check()
        access.agent_engine = access.engine
        if getattr(request.state, 'local_source_authority_used', False) or getattr(request.state, 'document_transport_used', False):
            raise _error('REQUEST_CLOSED')
        request.state.local_source_authority_used = True
        self._access = access
        self._lookup = lookup
        self._issuer = object()
        self._request_id = uuid4().hex
        self._registrations: dict[str, _Registration] = {}
        self._claimed = False

    def _approved(self, access: _DocumentRequestAccess, reference: str) -> ApprovedLocalSource:
        try:
            approval = self._lookup(reference, access.owner, access.conversation, access.identity[1])
        except Exception:
            raise _error('AUTHORIZATION_UNAVAILABLE') from None
        if (type(approval) is not ApprovedLocalSource or type(approval.owner_id) is not str
                or approval.owner_id != access.owner
                or type(approval.revision) is not str or not 1 <= len(approval.revision) <= 128
                or type(approval.grant) is not DocumentGrant or approval.grant._issuer is not None
                or approval.grant._handle is not None):
            raise _error('AUTHORIZATION_UNAVAILABLE')
        return approval

    def _current(self) -> _DocumentRequestAccess:
        access = self._access
        if access is None:
            raise _error('REQUEST_CLOSED')
        try:
            access.check()
            registered = tuple(self._registrations.items())
            for reference, record in registered:
                if self._approved(access, reference) != record.approval:
                    raise _error('AUTHORIZATION_UNAVAILABLE')
            if registered:
                access.check()
        except ReasoningError:
            self.close()
            raise
        return access

    def register(self, reference: str, content: bytes) -> DocumentHandle:
        """Register immutable bytes only when current independent policy matches them."""
        access = self._current()
        if self._claimed:
            raise _error('REQUEST_CLOSED')
        if not valid_source_reference(reference):
            raise _error('SOURCE_UNAVAILABLE')
        if reference in self._registrations:
            raise _error('SOURCE_UNAVAILABLE')
        approval = self._approved(access, reference)
        grant = approval.grant
        if (not isinstance(grant.clearance, ClearanceLevel)
                or not is_clearance_sufficient(access.account[2], grant.clearance)):
            raise _error('AUTHORIZATION_UNAVAILABLE')
        suffix = PurePath(grant.filename).suffix.lower() if valid_source_filename(grant.filename) else ''
        if not suffix or suffix not in _TYPES or type(content) is not bytes:
            raise _error('SOURCE_UNAVAILABLE')
        if (len(self._registrations) >= MAX_INPUTS or not 1 <= len(content) <= _TYPES[suffix][1]
                or sum(item.grant.size for item in self._registrations.values()) + len(content) > MAX_TOTAL_BYTES):
            raise _error('INPUT_LIMIT')
        digest = hashlib.sha256(content).hexdigest()
        if (type(grant.size) is not int or grant.size != len(content)
                or type(grant.sha256) is not str or grant.sha256 != digest):
            raise _error('SOURCE_UNAVAILABLE')
        if any(grant.filename.casefold() == item.grant.filename.casefold() or digest == item.grant.sha256
               for item in self._registrations.values()):
            raise _error('SOURCE_UNAVAILABLE')
        _preflight(((grant.filename, content),), access)
        self._current()
        if self._approved(access, reference) != approval:
            raise _error('AUTHORIZATION_UNAVAILABLE')
        access.check()
        source_id = uuid4().hex
        if any(record.grant._handle.source_id == source_id for record in self._registrations.values()):
            raise _error('SOURCE_UNAVAILABLE')
        handle = DocumentHandle(self._request_id, source_id, grant.filename, suffix[1:])
        self._registrations[reference] = _Registration(
            approval, replace(grant, _issuer=self._issuer, _handle=handle),
        )
        return handle

    def __call__(self, owner_id: str, conversation_id: str) -> tuple[DocumentGrant, ...]:
        access = self._access
        if access is None or (owner_id, conversation_id) != (access.owner, access.conversation):
            return ()
        self._current()
        return tuple(record.grant for record in self._registrations.values())

    def _claim(self, request: Request, conversation_id: str):
        access = self._access
        if (access is None or self._claimed or access.request is not request or access.conversation != conversation_id):
            raise _error('REQUEST_CLOSED')
        self._current()
        self._claimed = True
        return self._issuer, access.deadline, access.cancelled

    def revoke(self, handle: DocumentHandle) -> None:
        """Invalidate the entire ordered batch when one of its sources is revoked."""
        if not any(record.grant._handle is handle for record in self._registrations.values()):
            raise _error('SOURCE_UNAVAILABLE')
        self.close()

    def close(self) -> None:
        if self._access is not None:
            self._access.closed = True
        self._registrations.clear()
        self._lookup = self._access = None


@contextmanager
def open_local_source_authority(
    request: Request,
    conversation_id: str,
    *,
    lookup: LocalSourceLookup,
    cancelled: Callable[[], bool] = lambda: False,
    deadline: float | None = None,
) -> Iterator[LocalSourceAuthority]:
    """Open once after server authentication, with an independent local policy provider."""
    authority = LocalSourceAuthority(request, conversation_id, lookup, cancelled, deadline)
    try:
        yield authority
    finally:
        authority.close()
