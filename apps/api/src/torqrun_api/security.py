"""Authentication, authorization (roles), CSRF protection and login throttling.

Users authenticate with a session cookie (browser) or a personal API token (``Authorization:
Bearer tqt_…``). Every user-facing endpoint declares the minimum role it needs:

* **viewer**: read everything (never secret values),
* **operator**: + create, edit, run, cancel and retry jobs, workflows and schedules; queues,
* **admin**: + users, agent enrollment and revocation, secrets, audit log.

CSRF: a cookie-authenticated request that changes state must carry ``X-Torqrun-Client``. Browsers
only let same-origin scripts set custom headers on cross-origin requests after a CORS preflight,
which this API never grants, so a hostile site cannot forge one. Bearer-token requests are not
cookie-based and are exempt.
"""

import hashlib
import logging
import secrets
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, update

from torqrun_api.deps import SessionDep
from torqrun_db.models import ApiToken, User, UserSession
from torqrun_db.runs import utcnow

logger = logging.getLogger(__name__)

SESSION_COOKIE = "torqrun_session"
CSRF_HEADER = "X-Torqrun-Client"
API_TOKEN_PREFIX = "tqt_"  # noqa: S105 - a prefix, not a secret
ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TOUCH_EVERY = timedelta(seconds=60)

_hasher = PasswordHasher()  # Argon2id with the library's current recommended parameters
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-password")


# ---------------------------------------------------------------- passwords & tokens


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-ish time: an unknown user still pays for one Argon2 verification."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


MIN_PASSWORD = 10


def check_password_strength(password: str, email: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"password must be at least {MIN_PASSWORD} characters",
        )
    if password.lower() in (email.lower(), email.split("@")[0].lower()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="password must not be your email"
        )


# ---------------------------------------------------------------- principals


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    name: str
    role: str
    via: str  # "session" | "token"

    @property
    def actor(self) -> str:
        return self.email

    def can(self, role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


def _unauthorized(detail: str = "authentication required") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail)


async def get_principal(request: Request, session: SessionDep) -> Principal:
    """Resolve the caller from an API token or session cookie (cached per request)."""
    cached: Principal | None = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    now = utcnow()
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    principal: Principal | None = None

    if scheme.lower() == "bearer" and token.startswith(API_TOKEN_PREFIX):
        row = (
            await session.execute(
                select(ApiToken, User)
                .join(User, User.id == ApiToken.user_id)
                .where(ApiToken.token_hash == sha256(token), ApiToken.revoked_at.is_(None))
            )
        ).one_or_none()
        if row is None or (row[0].expires_at and row[0].expires_at <= now) or row[1].disabled:
            await session.commit()
            raise _unauthorized("invalid or expired API token")
        tok, user = row
        if tok.last_used_at is None or now - tok.last_used_at > TOUCH_EVERY:
            await session.execute(
                update(ApiToken).where(ApiToken.id == tok.id).values(last_used_at=now)
            )
        principal = Principal(user.id, user.email, user.name, user.role, "token")
    else:
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie:
            srow = (
                await session.execute(
                    select(UserSession, User)
                    .join(User, User.id == UserSession.user_id)
                    .where(UserSession.token_hash == sha256(cookie), UserSession.expires_at > now)
                )
            ).one_or_none()
            if srow is not None and not srow[1].disabled:
                sess, user = srow
                if now - sess.last_seen_at > TOUCH_EVERY:
                    await session.execute(
                        update(UserSession)
                        .where(UserSession.id == sess.id)
                        .values(last_seen_at=now)
                    )
                principal = Principal(user.id, user.email, user.name, user.role, "session")
    await session.commit()  # end the read so handlers can begin their own transaction
    if principal is None:
        raise _unauthorized()
    if (
        principal.via == "session"
        and request.method not in SAFE_METHODS
        and not request.headers.get(CSRF_HEADER)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail=f"missing {CSRF_HEADER} header (CSRF protection)"
        )
    request.state.principal = principal
    request.state.actor = principal.actor
    return principal


def require(role: str):  # type: ignore[no-untyped-def]  # returns a FastAPI dependency
    async def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if not principal.can(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"requires the {role} role")
        return principal

    dependency.__name__ = f"require_{role}"
    return dependency


require_viewer = require("viewer")
require_operator = require("operator")
require_admin = require("admin")

ViewerDep = Annotated[Principal, Depends(require_viewer)]
OperatorDep = Annotated[Principal, Depends(require_operator)]
AdminDep = Annotated[Principal, Depends(require_admin)]


# ---------------------------------------------------------------- login throttling


class LoginLimiter:
    """In-memory sliding window per (client IP, email). Per replica; good enough to make online
    password guessing impractical (with Argon2's cost on top)."""

    def __init__(self, attempts: int = 10, window_seconds: float = 300) -> None:
        self.attempts = attempts
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.attempts:
            retry = int(self.window - (now - hits[0])) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many login attempts; try again later",
                headers={"Retry-After": str(retry)},
            )

    def failed(self, key: str) -> None:
        self._hits[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def access(*, read: str = "viewer", write: str = "operator"):  # type: ignore[no-untyped-def]
    """Router-level guard: reads (GET/HEAD) need ``read``, everything else needs ``write``."""

    async def dependency(
        request: Request, principal: Annotated[Principal, Depends(get_principal)]
    ) -> Principal:
        role = read if request.method in SAFE_METHODS else write
        if not principal.can(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"requires the {role} role")
        return principal

    dependency.__name__ = f"access_{read}_{write}"
    return dependency


async def current_actor(principal: Annotated[Principal, Depends(get_principal)]) -> str:
    return principal.actor


ActorDep = Annotated[str, Depends(current_actor)]
