"""Request-scoped adapter for independently authorized server source records."""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from starlette.requests import Request

from backend.auth.models import ClearanceLevel, is_clearance_sufficient
from backend.multimodal.document_evidence import valid_source_filename
from backend.multimodal.document_source_authority import ApprovedLocalSource, valid_source_reference
from backend.multimodal.document_source_policy import LocalSourcePolicy, LocalSourceRecord, _source_request_access
from backend.multimodal.document_transport import DocumentGrant, _DocumentRequestAccess, _error
from backend.multimodal.workflow import MAX_INPUTS


@dataclass(frozen=True, slots=True)
class ServerSourceRecord:
    """Metadata only. Expires on the serving event loop's monotonic clock."""

    record: LocalSourceRecord
    expires_at: float
    revoked: bool


class ServerSourceProvider(Protocol):
    """Explicitly injected local authority for current, independently approved metadata."""

    def get_approved_source(
        self, reference: str, owner_id: str, conversation_id: str, generation: str,
    ) -> ServerSourceRecord | None: ...


def _read_record(provider: ServerSourceProvider, access: _DocumentRequestAccess, reference: str) -> ServerSourceRecord:
    access.lifetime()
    owner, generation = access.identity
    try:
        result = provider.get_approved_source(reference, owner, access.conversation, generation)
    except Exception:
        access.lifetime()
        raise _error('AUTHORIZATION_UNAVAILABLE') from None
    access.lifetime()
    if (type(result) is not ServerSourceRecord or result.revoked is not False
            or type(result.expires_at) is not float or not math.isfinite(result.expires_at)
            or result.expires_at <= asyncio.get_running_loop().time()):
        raise _error('AUTHORIZATION_UNAVAILABLE')
    record = result.record
    if (type(record) is not LocalSourceRecord or type(record.reference) is not str or record.reference != reference
            or type(record.conversation_id) is not str or record.conversation_id != access.conversation
            or type(record.generation) is not str or record.generation != generation
            or type(record.approval) is not ApprovedLocalSource or type(record.approval.owner_id) is not str
            or record.approval.owner_id != owner or type(record.approval.grant) is not DocumentGrant):
        raise _error('AUTHORIZATION_UNAVAILABLE')
    grant = record.approval.grant
    if (type(grant.filename) is not str or not valid_source_filename(grant.filename)
            or type(grant.sha256) is not str or type(grant.size) is not int
            or grant._issuer is not None or grant._handle is not None
            or not isinstance(grant.clearance, ClearanceLevel)
            or not is_clearance_sufficient(access.account[2], grant.clearance)):
        raise _error('AUTHORIZATION_UNAVAILABLE')
    return result


@contextmanager
def open_server_source_policy(
    request: Request,
    conversation_id: str,
    references: tuple[str, ...],
    *,
    provider: ServerSourceProvider | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    deadline: float | None = None,
) -> Iterator[LocalSourcePolicy]:
    """Create an authenticated, single-request policy with live provider rechecks."""
    if provider is None:
        raise _error('AUTHORIZATION_UNAVAILABLE')
    if type(references) is not tuple or not 1 <= len(references) <= MAX_INPUTS:
        raise _error('INPUT_LIMIT')
    if not all(valid_source_reference(reference) for reference in references) or len(set(references)) != len(references):
        raise _error('SOURCE_UNAVAILABLE')
    access = _source_request_access(request, conversation_id, cancelled, deadline)
    try:
        selected = {reference: _read_record(provider, access, reference) for reference in references}
        access.check()
        now = asyncio.get_running_loop().time()
        if any(item.expires_at <= now for item in selected.values()):
            raise _error('AUTHORIZATION_UNAVAILABLE')

        def record_is_current(active_request: Request, record: LocalSourceRecord) -> bool:
            return (access is not None and active_request is access.request
                    and _read_record(provider, access, record.reference) == selected[record.reference])

        yield LocalSourcePolicy(
            request.app, tuple(item.record for item in selected.values()), record_is_current=record_is_current,
        )
    finally:
        access.closed = True
        access = provider = None
