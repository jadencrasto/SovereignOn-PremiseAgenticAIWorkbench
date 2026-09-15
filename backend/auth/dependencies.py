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

from backend.auth.models import (
    ClearanceLevel,
    DEFAULT_CLEARANCE_MAP,
    Permission,
    User,
    UserPublic,
    UserRole,
    check_authorization,
    has_permission,
    is_clearance_sufficient,
    is_role_sufficient,
    parse_clearance,
)
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
        clearance=ClearanceLevel.L3.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )


def _get_synthetic_dev_user(role_name: str, clearance_name: Optional[str] = None) -> User:
    """Generate synthetic user for development mode role simulation."""
    parsed_role = UserRole(role_name)
    if clearance_name is not None:
        clr = parse_clearance(clearance_name)
        if clr is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid X-Clearance-Level header: '{clearance_name}'",
            )
    else:
        clr = DEFAULT_CLEARANCE_MAP.get(parsed_role, ClearanceLevel.L1)

    return User(
        id=f"user_dev_{parsed_role.value}",
        username=f"dev_{parsed_role.value}",
        password_hash="",
        role=parsed_role.value,
        clearance=clr.value,
        is_active=True,
        must_change_password=False,
        created_at="2026-01-01T00:00:00Z",
    )


async def get_current_user(
    request: Request,
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    x_requested_with: Optional[str] = Header(default=None, alias="X-Requested-With"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_clearance: Optional[str] = Header(default=None, alias="X-Clearance-Level"),
) -> User:
    """
    Resolve the authenticated user for the current request.

    Checks:
      1. If auth is disabled in config:
         - Validates session token if one was explicitly passed.
         - Otherwise checks X-User-Role (and optional X-Clearance-Level) for testing/simulation.
         - If none provided, defaults to synthetic dev admin.
      2. If auth is enabled (production mode):
         - Strictly enforces Authorization: Bearer <token> or Cookie: session_token=<token>.
         - Enforces CSRF header on mutating requests.
    """
    active_settings = getattr(request.app.state, "settings", settings)
    auth_enabled = getattr(active_settings, "auth_enabled", False)

    # 1. Non-production / dev-mode resolution
    if not auth_enabled:
        session_mgr = getattr(request.app.state, "session_manager", None)
        auth_store = getattr(request.app.state, "auth_store", None)
        raw_token = None
        is_cookie_auth = False
        if bearer and bearer.credentials:
            raw_token = bearer.credentials
        elif request.cookies.get("session_token"):
            raw_token = request.cookies.get("session_token")
            is_cookie_auth = True

        if session_mgr and auth_store and raw_token:
            # CSRF mitigation for cookie-based state-mutating requests
            if is_cookie_auth and request.method in ("POST", "PUT", "PATCH", "DELETE"):
                if not x_requested_with:
                    logger.warning("CSRF check failed: Missing X-Requested-With header on mutating request")
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="CSRF protection: X-Requested-With header is required for mutating requests",
                    )
            session = session_mgr.validate_session(raw_token)
            if session:
                user = auth_store.get_user_by_id(session.user_id)
                if user and user.is_active:
                    request.state.user = user
                    return user
                elif user and not user.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="User account is inactive or not found",
                    )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired or invalid",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check explicit dev-mode role header for simulation & automated RBAC tests
        if x_user_role is not None:
            clean_role = x_user_role.strip().lower()
            try:
                role_enum = UserRole(clean_role)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid X-User-Role header: '{x_user_role}'",
                )
            user = _get_synthetic_dev_user(role_enum.value, x_clearance)
            request.state.user = user
            return user

        # Fallback to synthetic dev admin in dev mode (preserves existing developer workflows)
        user = _get_synthetic_dev_admin()
        request.state.user = user
        return user

    # 2. Production mode resolution (strictly authenticated, client role headers ignored)
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

    # Check Bearer header
    if bearer and bearer.credentials:
        raw_token = bearer.credentials
    else:
        # Check Cookie
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
    async def _role_checker(request: Request, user: User = Depends(get_current_user)) -> User:
        authorized, reason = check_authorization(
            user_role=user.role,
            user_clearance=getattr(user, "clearance", None),
            min_role=min_role,
        )
        if not authorized:
            logger.warning(
                "Access denied (insufficient role): user=%s role=%s required=%s reason=%s",
                user.username, user.role, min_role.value, reason,
            )
            audit_logger = getattr(request.app.state, "audit_logger", None)
            if audit_logger:
                audit_logger.log(
                    event_type="auth.access_denied",
                    user_id=user.id,
                    role=user.role,
                    action=f"require_role:{min_role.value}",
                    resource=request.url.path,
                    success=False,
                    failure_reason=reason,
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=reason,
            )
        return user

    return _role_checker


def require_permission(permission: Permission) -> Callable:
    """
    FastAPI dependency that enforces an explicit permission.
    Raises 401 if unauthenticated, 403 if permission is missing.
    """
    async def _perm_checker(request: Request, user: User = Depends(get_current_user)) -> User:
        authorized, reason = check_authorization(
            user_role=user.role,
            user_clearance=getattr(user, "clearance", None),
            required_permission=permission,
        )
        if not authorized:
            logger.warning(
                "Access denied (missing permission): user=%s role=%s permission=%s reason=%s",
                user.username, user.role, permission.value, reason,
            )
            audit_logger = getattr(request.app.state, "audit_logger", None)
            if audit_logger:
                audit_logger.log(
                    event_type="auth.access_denied",
                    user_id=user.id,
                    role=user.role,
                    action=f"require_permission:{permission.value}",
                    resource=request.url.path,
                    success=False,
                    failure_reason=reason,
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=reason,
            )
        return user

    return _perm_checker


def require_clearance(min_clearance: ClearanceLevel) -> Callable:
    """
    FastAPI dependency that enforces a minimum clearance level.
    Raises 401 if unauthenticated, 403 if insufficient clearance.
    """
    async def _clearance_checker(request: Request, user: User = Depends(get_current_user)) -> User:
        authorized, reason = check_authorization(
            user_role=user.role,
            user_clearance=getattr(user, "clearance", None),
            min_clearance=min_clearance,
        )
        if not authorized:
            logger.warning(
                "Access denied (insufficient clearance): user=%s role=%s clearance=%s required=%s reason=%s",
                user.username, user.role, getattr(user, "clearance", None), min_clearance.value, reason,
            )
            audit_logger = getattr(request.app.state, "audit_logger", None)
            if audit_logger:
                audit_logger.log(
                    event_type="auth.access_denied",
                    user_id=user.id,
                    role=user.role,
                    action=f"require_clearance:{min_clearance.value}",
                    resource=request.url.path,
                    success=False,
                    failure_reason=reason,
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=reason,
            )
        return user

    return _clearance_checker

