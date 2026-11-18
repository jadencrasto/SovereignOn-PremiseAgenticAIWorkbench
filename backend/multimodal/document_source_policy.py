"""In-process policy for independently approved, conversation-local document sources."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from starlette.applications import Starlette
from starlette.requests import Request

from backend.auth.models import ClearanceLevel, Permission, has_permission, is_clearance_sufficient
from backend.auth.request_context import request_tools_enabled
from backend.multimodal.document_source_authority import (
    ApprovedLocalSource, LocalSourceAuthority, open_local_source_authority, valid_source_reference,
)
from backend.multimodal.document_transport import DocumentGrant, _DocumentRequestAccess, _document_deadline, _error


@dataclass(frozen=True, slots=True)
class LocalSourceRecord:
    """Approval metadata supplied by trusted server composition."""

    reference: str
    conversation_id: str
    generation: str
    approval: ApprovedLocalSource


def _source_request_access(request, conversation_id, cancelled, deadline):
    if request_tools_enabled.get() is not True:
        raise _error('AUTHORIZATION_UNAVAILABLE')
    access = _DocumentRequestAccess(request, conversation_id, cancelled, _document_deadline(conversation_id, deadline))
    access.check()
    access.agent_engine = access.engine
    return access


class LocalSourcePolicy:
    """Own one event loop's approved records. Request operations can only withdraw approvals."""

    def __init__(
        self, app: Starlette, records: tuple[LocalSourceRecord, ...], *,
        record_is_current: Callable[[Request, LocalSourceRecord], bool] | None = None,
        release_check: Callable[[Request], None] | None = None,
    ):
        if type(records) is not tuple:
            raise ValueError('Local source policy requires approved records.')
        self._app = app
        self._loop = asyncio.get_running_loop()
        self._record_is_current = record_is_current
        self._release_check = release_check
        self._records: dict[str, LocalSourceRecord] = {}
        for record in records:
            if (type(record) is not LocalSourceRecord or not valid_source_reference(record.reference)
                    or record.reference in self._records
                    or type(record.conversation_id) is not str or not 1 <= len(record.conversation_id) <= 128
                    or type(record.generation) is not str or not record.generation
                    or type(record.approval) is not ApprovedLocalSource
                    or type(record.approval.owner_id) is not str or not record.approval.owner_id
                    or type(record.approval.revision) is not str or not 1 <= len(record.approval.revision) <= 128
                    or type(record.approval.grant) is not DocumentGrant):
                raise ValueError('Invalid or duplicate local source policy record.')
            self._records[record.reference] = record

    def check_release(self, request: Request) -> None:
        if self._release_check is not None:
            self._release_check(request)

    def _request_access(self, request, conversation_id, cancelled, deadline):
        if asyncio.get_running_loop() is not self._loop or request.app is not self._app:
            raise _error('AUTHORIZATION_UNAVAILABLE')
        return _source_request_access(request, conversation_id, cancelled, deadline)

    def _approval(self, access, reference, owner, conversation, generation):
        clearance = access.check()
        if ((owner, conversation, generation) != (access.owner, access.conversation, access.identity[1])
                or not valid_source_reference(reference)):
            return None
        record = self._records.get(reference)
        if record is None or (owner, conversation, generation) != (
            record.approval.owner_id, record.conversation_id, record.generation,
        ):
            return None
        minimum = record.approval.grant.clearance
        if not isinstance(minimum, ClearanceLevel) or not is_clearance_sufficient(clearance, minimum):
            return None
        if self._record_is_current is not None and self._record_is_current(access.request, record) is not True:
            return None
        return record.approval

    @contextmanager
    def open_authority(
        self, request: Request, conversation_id: str, *, cancelled: Callable[[], bool] = lambda: False,
        deadline: float | None = None,
    ) -> Iterator[LocalSourceAuthority]:
        """Bind live policy lookup to this authenticated request and original task."""
        access = self._request_access(request, conversation_id, cancelled, deadline)

        def lookup(reference, owner, conversation, generation):
            return self._approval(access, reference, owner, conversation, generation)

        try:
            with open_local_source_authority(
                request, conversation_id, lookup=lookup, cancelled=cancelled, deadline=access.deadline,
            ) as authority:
                yield authority
        finally:
            access.closed = True

    def revoke(self, request: Request, conversation_id: str, reference: str) -> None:
        """Withdraw the current conversation owner's source approval from this policy instance."""
        access = self._request_access(request, conversation_id, lambda: False, None)
        try:
            if (not has_permission(access.account[0], Permission.EXECUTE_WRITE_TOOLS)
                    or self._approval(access, reference, access.owner, access.conversation, access.identity[1]) is None):
                raise _error('AUTHORIZATION_UNAVAILABLE')
            del self._records[reference]
        finally:
            access.closed = True
