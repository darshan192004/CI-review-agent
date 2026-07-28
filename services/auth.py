from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field

import bcrypt
from fastapi import Cookie, Request
from fastapi.responses import RedirectResponse

from config import settings

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 24 * 60 * 60  # 24 hours
_COOKIE_NAME = "session_token"
_COOKIE_MAX_AGE = SESSION_TTL_SECONDS


class AuthRedirect(Exception):
    """Raised to trigger a redirect to login."""


class Forbidden(Exception):
    """Raised when an authenticated user lacks required role."""


@dataclass(frozen=True, slots=True)
class User:
    username: str
    role: str  # "admin" or "viewer"


@dataclass(slots=True)
class _Session:
    user: User
    created_at: float = field(default_factory=time.time)


# In-memory session store (single-process deployment)
_sessions: dict[str, _Session] = {}


def hash_password(plaintext: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plaintext.encode("utf-8"), salt).decode("utf-8")


def verify_password(plaintext: str, hashed: str) -> bool:
    return bcrypt.checkpw(
        plaintext.encode("utf-8"), hashed.encode("utf-8")
    )


def create_session(user: User) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = _Session(user=user)
    return token


def validate_session(token: str) -> User | None:
    session = _sessions.get(token)
    if session is None:
        return None
    if time.time() - session.created_at > SESSION_TTL_SECONDS:
        del _sessions[token]
        return None
    return session.user


def destroy_session(token: str) -> None:
    _sessions.pop(token, None)


def _load_users() -> dict[str, User]:
    users: dict[str, User] = {}
    if settings.admin_username and settings.admin_password:
        users[settings.admin_username] = User(username=settings.admin_username, role="admin")
    if settings.viewer_username and settings.viewer_password:
        users[settings.viewer_username] = User(username=settings.viewer_username, role="viewer")
    return users


def authenticate_user(username: str, password: str) -> User | None:
    users = _load_users()
    user = users.get(username)
    if user is None:
        return None

    if user.role == "admin":
        stored = settings.admin_password
    else:
        stored = settings.viewer_password

    if not stored:
        return None

    # Support both bcrypt hashes and plaintext passwords
    if stored.startswith("$2"):
        if not verify_password(password, stored):
            return None
    else:
        if not hmac.compare_digest(password, stored):
            return None
    return user


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _require_auth(request: Request, session_token: str | None, require_admin: bool = False) -> User:
    if session_token:
        user = validate_session(session_token)
        if user is not None:
            if require_admin and user.role != "admin":
                raise Forbidden()
            return user
    raise AuthRedirect()


async def get_current_user(
    request: Request,
    session_token: str | None = Cookie(default=None),
) -> User:
    return _require_auth(request, session_token)


async def require_admin_role(
    request: Request,
    session_token: str | None = Cookie(default=None),
) -> User:
    return _require_auth(request, session_token, require_admin=True)
