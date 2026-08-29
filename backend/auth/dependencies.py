"""
backend/auth/dependencies.py
-----------------------------
Phase 7: FastAPI authentication & RBAC dependencies.

- Resolves session tokens from Cookie or Authorization header.
- Enforces role hierarchy (require_role) and explicit permission checks (require_permission).
- Enforces CSRF header on mutating requests when cookie-authenticated.
- Dev-mode synthesis when auth is disabled.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth.models import Permission, User, UserPublic, UserRole, has_permission, is_role_sufficient
from backend.config import settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


def _get_synthetic_dev_admin() -> User:
    """Generate synthetic admin user for development mode when auth is disabled."""
    return User(
        id="user_dev_admin",
        username="dev_admin",
        password_hash="",
        role=UserRole.ADMIN.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )


async def get_current_user(
    request: Request,
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    x_requested_with: Optional[str] = Header(default=None, alias="X-Requested-With"),
) -> User:
    """
    Resolve the authenticated user for the current request.

    Checks:
      1. If auth is disabled in config -> returns synthetic dev admin (only allowed in non-prod).
      2. Authorization: Bearer <token>
      3. Cookie: session_token=<token> (with CSRF header requirement for mutating methods)
    """
    # 1. Dev mode bypass
    auth_enabled = getattr(settings, "auth_enabled", True)
    if not auth_enabled:
        user = _get_synthetic_dev_admin()
        request.state.user = user
        return user

    # Retrieve components from app state
    session_mgr = getattr(request.app.state, "session_manager", None)
    auth_store = getattr(request.app.state, "auth_store", None)

    if not session_mgr or not auth_store:
        logger.error("Auth services not initialized on app.state")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication system uninitialized",
        )

    raw_token = None
    is_cookie_auth = False

    # 2. Check Bearer header
    if bearer and bearer.credentials:
        raw_token = bearer.credentials
    else:
        # 3. Check Cookie
        cookie_token = request.cookies.get("session_token")
        if cookie_token:
            raw_token = cookie_token
            is_cookie_auth = True

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # CSRF mitigation for cookie-based state-mutating requests
    if is_cookie_auth and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if not x_requested_with:
            logger.warning("CSRF check failed: Missing X-Requested-With header on mutating request")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF protection: X-Requested-With header is required for mutating requests",
            )

    # Validate session
    session = session_mgr.validate_session(raw_token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fetch active user
    user = auth_store.get_user_by_id(session.user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive or not found",
        )

    # Attach to request state for downstream handlers
    request.state.user = user
    return user


def require_role(min_role: UserRole) -> Callable:
    """
    FastAPI dependency that enforces a minimum user role level.
    Raises 401 if unauthenticated, 403 if insufficient role.
    """
    async def _role_checker(user: User = Depends(get_current_user)) -> User:
        if not is_role_sufficient(user.role, min_role):
            logger.warning(
                "Access denied (insufficient role): user=%s role=%s required=%s",
                user.username, user.role, min_role.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: '{min_role.value}' role or higher required",
            )
        return user

    return _role_checker


def require_permission(permission: Permission) -> Callable:
    """
    FastAPI dependency that enforces an explicit permission.
    Raises 401 if unauthenticated, 403 if permission is missing.
    """
    async def _perm_checker(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user.role, permission):
            logger.warning(
                "Access denied (missing permission): user=%s role=%s permission=%s",
                user.username, user.role, permission.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: permission '{permission.value}' required",
            )
        return user

    return _perm_checker
