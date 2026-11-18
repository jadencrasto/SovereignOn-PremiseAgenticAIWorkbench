"""Deployment-owned document metadata. This catalog does not approve content."""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from time import time_ns

from starlette.applications import Starlette
from starlette.requests import Request

from backend.agent.sqlite_transaction import immediate_transaction
from backend.auth.models import ClearanceLevel, Permission, has_permission, is_clearance_sufficient
from backend.multimodal.document_evidence import valid_source_filename
from backend.multimodal.document_source_authority import (
    ApprovedLocalSource, LocalSourceLookup, valid_source_reference,
)
from backend.multimodal.document_source_policy import LocalSourceRecord, _source_request_access
from backend.multimodal.document_source_records import ServerSourceProvider, ServerSourceRecord
from backend.multimodal.document_source_recovery import SourceRestoreBinding
from backend.multimodal.document_transport import (
    DocumentGrant, _DocumentRequestAccess, _TYPES, _error, _preflight,
)

_CREATE_CATALOG = """
CREATE TABLE IF NOT EXISTS approved_document_sources (
    reference TEXT PRIMARY KEY NOT NULL,
    owner_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    revision TEXT NOT NULL,
    filename TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK (byte_length > 0),
    sha256 TEXT NOT NULL,
    clearance TEXT NOT NULL,
    registered_us INTEGER NOT NULL,
    expires_us INTEGER NOT NULL CHECK (expires_us > registered_us),
    withdrawn_us INTEGER
)
"""
_SELECT_SOURCE = """
SELECT revision, filename, byte_length, sha256, clearance, registered_us, expires_us
FROM approved_document_sources
WHERE reference = ? AND owner_id = ? AND conversation_id = ? AND generation = ?
  AND withdrawn_us IS NULL
"""
_MICROSECONDS = 1_000_000
_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


def _utc_us() -> int:
    return time_ns() // 1_000


@dataclass(slots=True)
class _RequestSources:
    _provider: DeploymentSourceProvider
    _access: _DocumentRequestAccess | None
    _clock_origin: tuple[float, int]

    def get_approved_source(
        self, reference: str, owner_id: str, conversation_id: str, generation: str,
    ) -> ServerSourceRecord | None:
        if self._access is None:
            raise _error('REQUEST_CLOSED')
        return self._provider._lookup(
            self._access, self._clock_origin, reference, (owner_id, conversation_id, generation),
        )

    def _check_release(self, records: tuple[ServerSourceRecord, ...]) -> None:
        access = self._access
        if access is None:
            raise _error('REQUEST_CLOSED')
        self._provider._restore.check()
        access.lifetime()
        scope = self._provider._scope(access)
        if any((item.record.approval.owner_id, item.record.conversation_id, item.record.generation) != scope
               for item in records):
            raise _error('AUTHORIZATION_UNAVAILABLE')
        check_batch = self._provider._release_check
        if check_batch is None:
            current = all(self.get_approved_source(item.record.reference, *scope) == item for item in records)
        else:
            try:
                current = check_batch(tuple(item.record for item in records))
            except Exception:
                access.lifetime()
                raise _error('AUTHORIZATION_UNAVAILABLE') from None
        access.lifetime()
        self._provider._restore.check()
        if current is not True or any(item.expires_at <= asyncio.get_running_loop().time() for item in records):
            raise _error('AUTHORIZATION_UNAVAILABLE')


class DeploymentSourceProvider:
    """Catalog independently approved sources in the existing local application DB.

    Trusted deployment code supplies a fresh approval lookup and opaque references
    from that authority. Catalog mutations never create or replace an approval.
    """

    def __init__(
        self, app: Starlette, *, approval_lookup: LocalSourceLookup,
        release_check: Callable[[tuple[LocalSourceRecord, ...]], bool] | None = None,
    ):
        if not callable(approval_lookup):
            raise _error('AUTHORIZATION_UNAVAILABLE')
        try:
            self._app = app
            self._db = Path(app.state.settings.tasks_db_path).resolve(strict=True)
            self._restore = SourceRestoreBinding(app, self._db)
            self._read_uri = f'{self._db.as_uri()}?mode=ro'
            with immediate_transaction(self._db, require_existing=True) as connection:
                self._restore.check()
                connection.execute(_CREATE_CATALOG)
        except (AttributeError, OSError, TypeError, ValueError, sqlite3.Error):
            raise _error('AUTHORIZATION_UNAVAILABLE') from None
        self._approval_lookup = approval_lookup
        self._release_check = release_check

    @contextmanager
    def open_provider(
        self, request: Request, conversation_id: str, *,
        cancelled: Callable[[], bool] = lambda: False, deadline: float | None = None,
    ) -> Iterator[ServerSourceProvider]:
        """Bind fresh catalog reads to the original authenticated request and task."""
        with self._access(request, conversation_id, cancelled, deadline) as access:
            # The policy compares records for exact equality. Sample the clock
            # offset once so later wall-clock adjustments cannot extend this scope.
            bound = _RequestSources(self, access, (asyncio.get_running_loop().time(), _utc_us()))
            try:
                yield bound
            finally:
                bound._access = None

    @contextmanager
    def _access(self, request, conversation_id, cancelled, deadline):
        self._restore.check()
        if request.app is not self._app:
            raise _error('AUTHORIZATION_UNAVAILABLE')
        access = _source_request_access(request, conversation_id, cancelled, deadline)
        try:
            yield access
        finally:
            access.closed = True

    @staticmethod
    def _scope(access):
        return access.owner, access.conversation, access.identity[1]

    @staticmethod
    def _check_write(access):
        clearance = access.check()
        if not has_permission(access.account[0], Permission.EXECUTE_WRITE_TOOLS):
            raise _error('AUTHORIZATION_UNAVAILABLE')
        return clearance

    def _approval(self, access, reference, scope) -> ApprovedLocalSource | None:
        clearance = access.check()
        try:
            approval = self._approval_lookup(reference, *scope)
        except Exception:
            access.lifetime()
            raise _error('AUTHORIZATION_UNAVAILABLE') from None
        access.check()
        if (type(approval) is not ApprovedLocalSource or type(approval.owner_id) is not str
                or approval.owner_id != scope[0]
                or type(approval.revision) is not str or not 1 <= len(approval.revision) <= 128):
            return None
        if not approval.is_unexpired():
            return None
        grant = approval.grant
        if (type(grant) is not DocumentGrant or grant._issuer is not None or grant._handle is not None
                or not isinstance(grant.clearance, ClearanceLevel)
                or not is_clearance_sufficient(clearance, grant.clearance)):
            return None
        return approval

    @staticmethod
    def _active_expiration(clock_origin: tuple[float, int], registered_us: int, expires_us: int) -> float | None:
        loop_origin, utc_origin = clock_origin
        expires_on_loop = loop_origin + (expires_us - utc_origin) / _MICROSECONDS
        if registered_us <= _utc_us() < expires_us and asyncio.get_running_loop().time() < expires_on_loop:
            return expires_on_loop
        return None

    def _lookup(
        self, access: _DocumentRequestAccess, clock_origin: tuple[float, int],
        reference: str, scope: tuple[str, str, str],
    ) -> ServerSourceRecord | None:
        self._restore.check()
        access.check()
        if scope != self._scope(access) or not valid_source_reference(reference):
            return None
        approval = self._approval(access, reference, scope)
        if approval is None:
            return None
        try:
            with closing(sqlite3.connect(self._read_uri, timeout=1.0, uri=True)) as connection:
                row = connection.execute(_SELECT_SOURCE, (reference, *scope)).fetchone()
        except sqlite3.Error:
            access.lifetime()
            raise _error('AUTHORIZATION_UNAVAILABLE') from None
        access.check()
        if row is None:
            return None
        revision, filename, size, digest, clearance, registered_us, expires_us = row
        grant = approval.grant
        if (revision, filename, size, digest, clearance) != (
            approval.revision, grant.filename, grant.size, grant.sha256, grant.clearance.value,
        ):
            return None
        if type(registered_us) is not int or type(expires_us) is not int:
            return None
        if approval.expires_at is not None:
            expires_us = min(expires_us, (approval.expires_at - _UTC_EPOCH) // _MICROSECOND)
        expires = self._active_expiration(clock_origin, registered_us, expires_us)
        if expires is None:
            return None
        self._restore.check()
        return ServerSourceRecord(LocalSourceRecord(reference, scope[1], scope[2], approval), expires, False)

    def register(
        self, request: Request, conversation_id: str, reference: str, content: bytes, *,
        expires_at: datetime, cancelled: Callable[[], bool] = lambda: False, deadline: float | None = None,
    ) -> None:
        """Bind approved bytes and a server-supplied UTC expiry to a new reference."""
        with self._access(request, conversation_id, cancelled, deadline) as access:
            self._check_write(access)
            if not valid_source_reference(reference):
                raise _error('SOURCE_UNAVAILABLE')
            scope = self._scope(access)
            approval = self._approval(access, reference, scope)
            if approval is None:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            if type(expires_at) is not datetime or expires_at.utcoffset() is None:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            expires_us = (expires_at - _UTC_EPOCH) // _MICROSECOND
            if approval.expires_at is not None:
                expires_us = min(expires_us, (approval.expires_at - _UTC_EPOCH) // _MICROSECOND)
            clock_origin = (asyncio.get_running_loop().time(), _utc_us())
            if self._active_expiration(clock_origin, clock_origin[1], expires_us) is None:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            grant = approval.grant
            suffix = PurePath(grant.filename).suffix.lower() if valid_source_filename(grant.filename) else ''
            if suffix not in _TYPES or type(content) is not bytes:
                raise _error('SOURCE_UNAVAILABLE')
            if not 1 <= len(content) <= _TYPES[suffix][1]:
                raise _error('INPUT_LIMIT')
            if (type(grant.size) is not int or grant.size != len(content)
                    or type(grant.sha256) is not str or grant.sha256 != hashlib.sha256(content).hexdigest()):
                raise _error('SOURCE_UNAVAILABLE')
            _preflight(((grant.filename, content),), access)
            # Query independent policy outside the write transaction to avoid holding
            # the shared SQLite lock across deployment-owned code.
            if self._approval(access, reference, scope) != approval:
                raise _error('AUTHORIZATION_UNAVAILABLE')
            self._check_write(access)
            try:
                with immediate_transaction(self._db, require_existing=True) as connection:
                    # Session revalidation writes its sliding expiry to this same
                    # database. Only check the request lifetime while holding its lock.
                    self._restore.check()
                    access.lifetime()
                    registered_us = _utc_us()
                    if self._active_expiration(clock_origin, registered_us, expires_us) is None:
                        raise _error('AUTHORIZATION_UNAVAILABLE')
                    connection.execute(
                        """INSERT INTO approved_document_sources
                           (reference, owner_id, conversation_id, generation, revision, filename,
                            byte_length, sha256, clearance, registered_us, expires_us)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (reference, *scope, approval.revision, grant.filename, grant.size, grant.sha256,
                         grant.clearance.value, registered_us, expires_us),
                    )
                    self._restore.check()
            except sqlite3.IntegrityError:
                raise _error('SOURCE_UNAVAILABLE') from None
            except sqlite3.Error:
                raise _error('AUTHORIZATION_UNAVAILABLE') from None

    def withdraw(
        self, request: Request, conversation_id: str, reference: str, *,
        cancelled: Callable[[], bool] = lambda: False, deadline: float | None = None,
    ) -> bool:
        """Tombstone one current, owned reference, including an expired approval."""
        with self._access(request, conversation_id, cancelled, deadline) as access:
            clearance = self._check_write(access)
            if not valid_source_reference(reference):
                return False
            key = reference, *self._scope(access)
            try:
                with immediate_transaction(self._db, require_existing=True) as connection:
                    self._restore.check()
                    access.lifetime()
                    row = connection.execute(_SELECT_SOURCE, key).fetchone()
                    if row is None or not is_clearance_sufficient(clearance, row[4]):
                        return False
                    connection.execute(
                        """UPDATE approved_document_sources SET withdrawn_us = ?
                           WHERE reference = ? AND owner_id = ? AND conversation_id = ? AND generation = ?
                             AND withdrawn_us IS NULL""",
                        (_utc_us(), *key),
                    )
                    self._restore.check()
            except sqlite3.Error:
                raise _error('AUTHORIZATION_UNAVAILABLE') from None
            return True
