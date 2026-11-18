"""Deployment review records for the internal verified document workflow."""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePath
from uuid import uuid4

from starlette.applications import Starlette
from starlette.requests import Request

from backend.agent.sqlite_transaction import immediate_transaction
from backend.auth.models import (
    ClearanceLevel, Permission, User, has_permission, is_clearance_sufficient,
    parse_clearance, resolve_user_clearance,
)
from backend.auth.request_context import (
    authenticated_dispatch_request, cap_clearance, dispatch_identity_resolver,
    effective_owner, request_tools_enabled,
)
from backend.multimodal.document_evidence import valid_source_filename
from backend.multimodal.document_source_authority import ApprovedLocalSource, valid_source_reference
from backend.multimodal.document_source_provider import DeploymentSourceProvider
from backend.multimodal.document_transport import DocumentGrant, _TYPES, _error

_USE = 'verified_document_workflow'
_CREATE = """
CREATE TABLE IF NOT EXISTS document_source_approvals (
    reference TEXT PRIMARY KEY NOT NULL,
    owner_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    approver_id TEXT NOT NULL CHECK (approver_id <> owner_id),
    revision TEXT NOT NULL UNIQUE,
    permitted_use TEXT NOT NULL CHECK (permitted_use = 'verified_document_workflow'),
    filename TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK (byte_length > 0),
    sha256 TEXT NOT NULL,
    clearance TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL CHECK (expires_at > approved_at),
    withdrawn_at TEXT,
    UNIQUE (owner_id, conversation_id, generation, sha256)
)
"""
_CURRENT = """
SELECT approver_id, revision, filename, byte_length, sha256, clearance, approved_at, expires_at
FROM document_source_approvals
WHERE reference = ? AND owner_id = ? AND conversation_id = ? AND generation = ?
  AND permitted_use = ? AND withdrawn_at IS NULL
"""


def _valid_scope(owner_id: str, conversation_id: str, generation: str) -> bool:
    return all(type(part) is str and 1 <= len(part) <= 128 for part in (owner_id, conversation_id, generation))


def _valid_grant(grant: DocumentGrant) -> bool:
    if (type(grant) is not DocumentGrant or grant._issuer is not None or grant._handle is not None
            or not valid_source_filename(grant.filename) or not isinstance(grant.clearance, ClearanceLevel)):
        return False
    suffix = PurePath(grant.filename).suffix.lower()
    return (suffix in _TYPES and type(grant.size) is int and 1 <= grant.size <= _TYPES[suffix][1]
            and type(grant.sha256) is str and re.fullmatch(r'[a-f0-9]{64}', grant.sha256) is not None)


def _can_review(user: User | None, minimum: ClearanceLevel) -> bool:
    return bool(user and user.is_active and parse_clearance(user.clearance)
                and has_permission(user.role, Permission.MANAGE_CONFIG)
                and is_clearance_sufficient(resolve_user_clearance(user), minimum))


class DeploymentSourceApprovals:
    """Trusted in-process entry point for explicitly reviewed document metadata."""

    def __init__(self, app: Starlette):
        self._app = app
        try:
            self._db = Path(app.state.settings.tasks_db_path).resolve(strict=True)
            self._read_uri = f'{self._db.as_uri()}?mode=ro'
            with immediate_transaction(self._db, require_existing=True) as connection:
                connection.execute(_CREATE)
        except (AttributeError, OSError, TypeError, ValueError, sqlite3.Error):
            raise _error('AUTHORIZATION_UNAVAILABLE') from None

    def source_provider(self) -> DeploymentSourceProvider:
        """Compose the existing source catalog with this live approval authority."""
        return DeploymentSourceProvider(self._app, approval_lookup=self.lookup)

    def _reviewer(self, request: Request) -> User:
        try:
            if (request.app is not self._app or self._app.state.settings.auth_enabled is not True
                    or authenticated_dispatch_request() is not request):
                raise PermissionError
            user = dispatch_identity_resolver.get()()
            if not _can_review(user, ClearanceLevel.L1) or user.id != effective_owner.get():
                raise PermissionError
        except Exception:
            raise _error('AUTHORIZATION_UNAVAILABLE') from None
        return user

    def approve(
        self, request: Request, *, reference: str, owner_id: str, conversation_id: str,
        generation: str, grant: DocumentGrant, expires_at: datetime,
    ) -> None:
        """Record an explicit review of exact metadata for another authenticated owner."""
        reviewer = self._reviewer(request)
        if (not valid_source_reference(reference) or not _valid_scope(owner_id, conversation_id, generation)
                or not _valid_grant(grant) or type(expires_at) is not datetime or expires_at.utcoffset() is None):
            raise _error('SOURCE_UNAVAILABLE')
        if (reviewer.id == owner_id
                or not is_clearance_sufficient(cap_clearance(resolve_user_clearance(reviewer)), grant.clearance)):
            raise _error('AUTHORIZATION_UNAVAILABLE')
        expires_at = expires_at.astimezone(UTC)
        # AuthStore uses its own locks and session resolution writes sliding expiry.
        # Keep those calls outside SQLite's writer lock.
        try:
            state = self._app.state
            owner = state.auth_store.get_user_by_id(owner_id)
            registry = state.engine._memory._security_registry
            if (not owner or not owner.is_active or not parse_clearance(owner.clearance)
                    or not has_permission(owner.role, Permission.EXECUTE_READ_TOOLS)
                    or not is_clearance_sufficient(resolve_user_clearance(owner), grant.clearance)
                    or registry is None or not registry.has_active_binding(conversation_id, generation, owner_id)
                    or self._reviewer(request) != reviewer):
                raise _error('AUTHORIZATION_UNAVAILABLE')
            with immediate_transaction(self._db, require_existing=True) as connection:
                now = datetime.now(UTC)
                if expires_at <= now:
                    raise _error('AUTHORIZATION_UNAVAILABLE')
                connection.execute(
                    """INSERT INTO document_source_approvals
                       (reference, owner_id, conversation_id, generation, approver_id, revision,
                        permitted_use, filename, byte_length, sha256, clearance, approved_at, expires_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (reference, owner_id, conversation_id, generation, reviewer.id, uuid4().hex, _USE,
                     grant.filename, grant.size, grant.sha256, grant.clearance.value,
                     now.isoformat(), expires_at.isoformat()),
                )
        except sqlite3.IntegrityError:
            raise _error('SOURCE_UNAVAILABLE') from None
        except (AttributeError, OSError, sqlite3.Error):
            raise _error('AUTHORIZATION_UNAVAILABLE') from None

    def lookup(self, reference: str, owner_id: str, conversation_id: str, generation: str) -> ApprovedLocalSource | None:
        """Resolve current approval for the already-authenticated document owner."""
        if (not valid_source_reference(reference) or not _valid_scope(owner_id, conversation_id, generation)
                or request_tools_enabled.get() is not True):
            return None
        try:
            state = self._app.state
            request = authenticated_dispatch_request()
            if request is None or request.app is not self._app or state.settings.auth_enabled is not True:
                return None
            owner, identity = state.engine._current_dispatch_identity(conversation_id, owner_id)
            if (identity != (owner_id, generation) or not parse_clearance(owner.clearance)
                    or not has_permission(owner.role, Permission.EXECUTE_READ_TOOLS)):
                return None
            with closing(sqlite3.connect(self._read_uri, timeout=1.0, uri=True)) as connection:
                row = connection.execute(_CURRENT, (reference, owner_id, conversation_id, generation, _USE)).fetchone()
            if row is None:
                return None
            approver, revision, filename, size, digest, minimum, approved_at, expires_at = row
            grant = DocumentGrant(filename, size, digest, ClearanceLevel(minimum))
            approved_at = datetime.fromisoformat(approved_at)
            expires_at = datetime.fromisoformat(expires_at)
            if (approver == owner_id or not _valid_grant(grant)
                    or not (approved_at <= datetime.now(UTC) < expires_at)
                    or not is_clearance_sufficient(cap_clearance(resolve_user_clearance(owner)), grant.clearance)
                    or not _can_review(state.auth_store.get_user_by_id(approver), grant.clearance)):
                return None
            return ApprovedLocalSource(owner_id, revision, grant, expires_at)
        except (AttributeError, OSError, PermissionError, TypeError, ValueError, sqlite3.Error):
            return None

    def withdraw(self, request: Request, reference: str) -> bool:
        """Permanently withdraw a review, including an expired or stale review."""
        reviewer = self._reviewer(request)
        if not valid_source_reference(reference):
            return False
        try:
            with immediate_transaction(self._db, require_existing=True) as connection:
                row = connection.execute(
                    'SELECT clearance FROM document_source_approvals WHERE reference = ? AND withdrawn_at IS NULL',
                    (reference,),
                ).fetchone()
                if row is None:
                    return False
                clearance = ClearanceLevel(row[0])
                if not is_clearance_sufficient(cap_clearance(resolve_user_clearance(reviewer)), clearance):
                    raise _error('AUTHORIZATION_UNAVAILABLE')
                connection.execute(
                    'UPDATE document_source_approvals SET withdrawn_at = ? WHERE reference = ? AND withdrawn_at IS NULL',
                    (datetime.now(UTC).isoformat(), reference),
                )
        except (AttributeError, OSError, ValueError, sqlite3.Error):
            raise _error('AUTHORIZATION_UNAVAILABLE') from None
        return True
