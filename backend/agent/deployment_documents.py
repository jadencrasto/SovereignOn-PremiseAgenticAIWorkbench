"""Lifespan-owned composition for approved documents. This module registers no routes."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from starlette.applications import Starlette
from starlette.requests import Request

from backend.agent.document_workflow import _check_delivery_deadline, _delivery_failure, run_server_source_document_workflow
from backend.auth.request_context import authenticated_dispatch_request
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_source_approvals import DeploymentSourceApprovals
from backend.multimodal.document_transport import DocumentGrant, _document_deadline, _error


class DeploymentDocumentWorkflow:
    """Operate on the approval records and catalog owned by one running app."""

    def __init__(self, app: Starlette):
        state = app.state
        try:
            self._loop = asyncio.get_running_loop()
            self._settings = state.settings
            self._auth = state.auth_store
            self._sessions = state.session_manager
            self._engine = state.engine
            self._registry = state.conversation_registry
            self._configured_path = Path(self._settings.tasks_db_path)
            database = self._configured_path.resolve(strict=True)
            memory = self._engine._memory
            if (self._settings.auth_enabled is not True or self._sessions._store is not self._auth
                    or not memory.persistence_configured or memory._security_registry is not self._registry
                    or Path(self._auth._db_path).resolve(strict=True) != database
                    or Path(self._registry.path).resolve(strict=True) != database):
                raise _error('AUTHORIZATION_UNAVAILABLE')
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            raise _error('AUTHORIZATION_UNAVAILABLE') from None
        self._app = app
        self._closed = False
        self._approvals = DeploymentSourceApprovals(app)
        self._provider = self._approvals.source_provider()
        if self._approvals._db != database or self._provider._db != database:
            raise _error('AUTHORIZATION_UNAVAILABLE')

    def _check(self, request: Request | None = None) -> None:
        state = self._app.state
        try:
            active = (
                not self._closed and asyncio.get_running_loop() is self._loop
                and state.startup_complete is True and state.deployment_documents is self
                and state.settings is self._settings and self._settings.auth_enabled is True
                and self._settings.tasks_db_path == self._configured_path
                and state.auth_store is self._auth and state.session_manager is self._sessions
                and state.engine is self._engine and state.conversation_registry is self._registry
                and self._sessions._store is self._auth and self._engine._memory._security_registry is self._registry
            )
        except (AttributeError, RuntimeError):
            active = False
        if not active or (request is not None and (
            request.app is not self._app or authenticated_dispatch_request() is not request
        )):
            raise _error('AUTHORIZATION_UNAVAILABLE')
        self._approvals._restore.check()

    def approve(
        self, request: Request, *, reference: str, owner_id: str, conversation_id: str,
        generation: str, grant: DocumentGrant, expires_at: datetime,
    ) -> None:
        """Record the authenticated administrator's explicit review."""
        self._check(request)
        self._approvals.approve(
            request, reference=reference, owner_id=owner_id, conversation_id=conversation_id,
            generation=generation, grant=grant, expires_at=expires_at,
        )

    def withdraw_approval(self, request: Request, reference: str) -> bool:
        self._check(request)
        return self._approvals.withdraw(request, reference)

    def register_source(
        self, request: Request, conversation_id: str, reference: str, content: bytes, *,
        expires_at: datetime, cancelled: Callable[[], bool] = lambda: False, deadline: float | None = None,
    ) -> None:
        """Validate an already approved source without granting approval."""
        self._check(request)
        self._provider.register(
            request, conversation_id, reference, content, expires_at=expires_at,
            cancelled=self._cancel_on_close(request, cancelled), deadline=deadline,
        )

    def withdraw_source(self, request: Request, conversation_id: str, reference: str) -> bool:
        self._check(request)
        return self._provider.withdraw(request, conversation_id, reference)

    def _cancel_on_close(self, request: Request, cancelled: Callable[[], bool]) -> Callable[[], bool]:
        def stopped() -> bool:
            try:
                self._check(request)
            except ReasoningError:
                return True
            return cancelled()

        return stopped

    async def run(
        self, request: Request, conversation_id: str, objective: str, *,
        sources: tuple[tuple[str, bytes], ...], cancelled: Callable[[], bool] = lambda: False,
        deadline: float | None = None,
    ) -> tuple[dict, ...]:
        """Return the existing verified event batch only from this active app."""
        try:
            self._check(request)
            deadline = _document_deadline(conversation_id, deadline)
            stopped = self._cancel_on_close(request, cancelled)
            with self._provider.open_provider(
                request, conversation_id, cancelled=stopped, deadline=deadline,
            ) as provider:
                events = await run_server_source_document_workflow(
                    request, conversation_id, objective, sources=sources, provider=provider,
                    cancelled=stopped, deadline=deadline,
                )
            self._check(request)
            _check_delivery_deadline(events, deadline)
            return events
        except ReasoningError as exc:
            return _delivery_failure(exc.code, uuid4().hex)


@contextmanager
def install_deployment_documents(app: Starlette) -> Iterator[None]:
    """Install one composition after the application has acquired its services."""
    state = app.state
    if getattr(state, 'deployment_documents', None) is not None:
        raise _error('AUTHORIZATION_UNAVAILABLE')
    documents = DeploymentDocumentWorkflow(app)
    state.deployment_documents = documents
    try:
        yield
    finally:
        documents._closed = True
        if getattr(state, 'deployment_documents', None) is documents:
            state.deployment_documents = None


def get_deployment_documents(app: Starlette) -> DeploymentDocumentWorkflow:
    """Resolve the internal workflow only during the authenticated app lifespan."""
    documents = getattr(app.state, 'deployment_documents', None)
    if type(documents) is not DeploymentDocumentWorkflow or documents._app is not app:
        raise _error('AUTHORIZATION_UNAVAILABLE')
    documents._check()
    return documents
