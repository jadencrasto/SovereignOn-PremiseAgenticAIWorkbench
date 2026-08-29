"""
backend/auth/routes.py
-----------------------
Phase 7: Authentication and user management REST API endpoints.

Endpoints:
  POST  /api/auth/login            — Authenticate and issue session token
  POST  /api/auth/logout           — Revoke session token
  GET   /api/auth/me               — Get current user profile
  POST  /api/auth/change-password  — Update own password
  GET   /api/auth/users            — List users (admin only)
  POST  /api/auth/users            — Create user (admin only)
  PATCH /api/auth/users/{user_id}  — Update user role / status / password (admin only)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from backend.auth.dependencies import get_current_user, require_role
from backend.auth.models import AuthStore, User, UserPublic, UserRole
from backend.auth.security import (
    BruteForceProtector,
    SessionManager,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    token: str
    user: UserPublic


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole = UserRole.OPERATOR


class UpdateUserRequest(BaseModel):
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_audit_logger(request: Request):
    return getattr(request.app.state, "audit_logger", None)


def _log_auth_event(request: Request, event_type: str, user_id: Optional[str], username: str, role: Optional[str], success: bool, reason: Optional[str] = None):
    audit = _get_audit_logger(request)
    if audit:
        audit.log(
            event_type=event_type,
            user_id=user_id,
            role=role,
            action=event_type,
            resource=f"user:{username}",
            success=success,
            failure_reason=reason,
            metadata={"username": username},
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/login", response_model=LoginResponse, summary="Log in with credentials")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
):
    """Authenticate and obtain a server-side session token."""
    auth_store: AuthStore = getattr(request.app.state, "auth_store", None)
    session_mgr: SessionManager = getattr(request.app.state, "session_manager", None)
    bf_protector: BruteForceProtector = getattr(request.app.state, "brute_force_protector", None)

    if not auth_store or not session_mgr or not bf_protector:
        raise HTTPException(status_code=500, detail="Auth services uninitialized")

    client_ip = request.client.host if request.client else "unknown"

    # Check brute-force lockout
    if bf_protector.is_locked_out(body.username):
        _log_auth_event(request, "auth.lockout", None, body.username, None, False, "Account temporarily locked")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Account temporarily locked.",
        )

    user = auth_store.get_user_by_username(body.username)

    # Constant-time comparison check
    valid = False
    if user and user.is_active:
        valid = verify_password(body.password, user.password_hash)

    if not valid:
        is_now_locked = bf_protector.record_failure(body.username, client_ip)
        _log_auth_event(request, "auth.login_failed", user.id if user else None, body.username, None, False, "Invalid credentials")
        if is_now_locked:
            _log_auth_event(request, "auth.lockout", user.id if user else None, body.username, None, False, "Account locked after max failures")
        # Generic error message to prevent username enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Success — clear failed attempt count
    bf_protector.record_success(body.username)

    # Update last login time
    now_iso = datetime.now(timezone.utc).isoformat()
    auth_store.update_user(user.id, last_login_at=now_iso)

    # Create session
    raw_token, session = session_mgr.create_session(user.id)

    # Set httpOnly SameSite=Lax cookie
    response.set_cookie(
        key="session_token",
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=False,  # Set True if HTTPS in production
        max_age=86400,
    )

    _log_auth_event(request, "auth.login_success", user.id, user.username, user.role, True)

    user_public = UserPublic(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
        last_login_at=now_iso,
    )

    return LoginResponse(token=raw_token, user=user_public)


@router.post("/logout", summary="Log out and revoke session")
async def logout(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
):
    """Revoke current session token."""
    session_mgr: SessionManager = getattr(request.app.state, "session_manager", None)
    raw_token = request.cookies.get("session_token")
    if not raw_token:
        # Check bearer header
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            raw_token = auth_header.split(" ", 1)[1]

    if session_mgr and raw_token:
        session_mgr.revoke_session(raw_token)

    response.delete_cookie("session_token")
    _log_auth_event(request, "auth.logout", current_user.id, current_user.username, current_user.role, True)
    return {"message": "Successfully logged out"}


@router.get("/me", response_model=UserPublic, summary="Get current user profile")
async def get_me(current_user: User = Depends(get_current_user)):
    """Return profile of the authenticated user."""
    return UserPublic(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role,
        is_active=current_user.is_active,
        must_change_password=current_user.must_change_password,
        created_at=current_user.created_at,
        last_login_at=current_user.last_login_at,
    )


@router.post("/change-password", summary="Change own password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Change password for current user, clearing must_change_password flag."""
    auth_store: AuthStore = getattr(request.app.state, "auth_store", None)
    if not auth_store:
        raise HTTPException(status_code=500, detail="Auth services uninitialized")

    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password verification failed",
        )

    new_hash = hash_password(body.new_password)
    auth_store.update_user(current_user.id, password_hash=new_hash, must_change_password=False)

    _log_auth_event(request, "auth.password_changed", current_user.id, current_user.username, current_user.role, True)
    return {"message": "Password updated successfully"}


# ---------------------------------------------------------------------------
# Admin User Management Endpoints
# ---------------------------------------------------------------------------

@router.get("/users", response_model=List[UserPublic], summary="List all users (admin only)")
async def list_users(
    request: Request,
    admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """List all registered users."""
    auth_store: AuthStore = getattr(request.app.state, "auth_store", None)
    if not auth_store:
        raise HTTPException(status_code=500, detail="Auth store uninitialized")
    return auth_store.list_users()


@router.post("/users", response_model=UserPublic, status_code=status.HTTP_201_CREATED, summary="Create user (admin only)")
async def create_user(
    body: CreateUserRequest,
    request: Request,
    admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Create a new user account."""
    auth_store: AuthStore = getattr(request.app.state, "auth_store", None)
    if not auth_store:
        raise HTTPException(status_code=500, detail="Auth store uninitialized")

    existing = auth_store.get_user_by_username(body.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is already taken",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    new_user = User(
        id=f"user_{uuid.uuid4().hex[:12]}",
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role.value,
        is_active=True,
        must_change_password=False,
        created_at=now_iso,
    )
    auth_store.create_user(new_user)
    _log_auth_event(request, "auth.user_created", new_user.id, new_user.username, new_user.role, True)

    return UserPublic(
        id=new_user.id,
        username=new_user.username,
        role=new_user.role,
        is_active=new_user.is_active,
        must_change_password=new_user.must_change_password,
        created_at=new_user.created_at,
    )


@router.patch("/users/{user_id}", response_model=UserPublic, summary="Update user (admin only)")
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    request: Request,
    admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Update role, active status, or reset password for a user."""
    auth_store: AuthStore = getattr(request.app.state, "auth_store", None)
    if not auth_store:
        raise HTTPException(status_code=500, detail="Auth store uninitialized")

    target = auth_store.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    pwd_hash = hash_password(body.password) if body.password else None
    role_val = body.role.value if body.role else None

    auth_store.update_user(
        user_id,
        role=role_val,
        is_active=body.is_active,
        password_hash=pwd_hash,
    )

    updated = auth_store.get_user_by_id(user_id)
    _log_auth_event(request, "auth.user_updated", target.id, target.username, updated.role, True)

    return UserPublic(
        id=updated.id,
        username=updated.username,
        role=updated.role,
        is_active=updated.is_active,
        must_change_password=updated.must_change_password,
        created_at=updated.created_at,
        last_login_at=updated.last_login_at,
    )
