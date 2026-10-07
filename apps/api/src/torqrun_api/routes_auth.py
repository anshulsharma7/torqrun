"""Sign-in, first-run setup, the current user, password changes and personal API tokens."""

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, text, update

from torqrun_api.deps import SessionDep, SettingsDep
from torqrun_api.security import (
    API_TOKEN_PREFIX,
    CSRF_HEADER,
    SESSION_COOKIE,
    LoginLimiter,
    Principal,
    check_password_strength,
    client_ip,
    get_principal,
    hash_password,
    needs_rehash,
    new_token,
    sha256,
    verify_password,
)
from torqrun_db.models import ApiToken, User, UserSession
from torqrun_db.runs import utcnow

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]
SETUP_LOCK = 0x7471_7375  # advisory lock id: one first-run setup at a time


def normalize_email(v: str) -> str:
    v = v.strip().lower()
    if "@" not in v or v.startswith("@") or v.endswith("@") or " " in v:
        raise ValueError("enter a valid email address")
    return v


class Me(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: Literal["viewer", "operator", "admin"]
    via: Literal["session", "token"]


class AuthStatus(BaseModel):
    setup_required: bool
    user: Me | None


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)

    _email = field_validator("email")(normalize_email)


class SetupIn(LoginIn):
    name: str = Field(default="", max_length=120)


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)


class TokenOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None


class TokenCreated(TokenOut):
    token: str = Field(description="Shown once. Use as `Authorization: Bearer <token>`.")


def _me(p: Principal) -> Me:
    return Me(id=p.user_id, email=p.email, name=p.name, role=p.role, via=p.via)


def _require_client_header(request: Request) -> None:
    # Login and setup create a cookie session, so they get the same CSRF guard as cookie writes.
    if not request.headers.get(CSRF_HEADER):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"missing {CSRF_HEADER} header")


def _limiter(request: Request) -> LoginLimiter:
    limiter: LoginLimiter = request.app.state.login_limiter
    return limiter


async def _start_session(
    session: SessionDep, settings: SettingsDep, request: Request, response: Response, user: User
) -> None:
    token = new_token("tqs_")
    now = utcnow()
    ttl = timedelta(hours=settings.session_ttl_hours)
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=sha256(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + ttl,
            ip=client_ip(request)[:64],
            user_agent=request.headers.get("user-agent", "")[:255],
        )
    )
    user.last_login_at = now
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        path="/",
    )


@router.get("/status")
async def auth_status(request: Request, session: SessionDep) -> AuthStatus:
    """Unauthenticated: whether first-run setup is needed, and who is signed in (if anyone)."""
    has_users = (await session.execute(select(func.count()).select_from(User))).scalar_one() > 0
    user = None
    if has_users:
        try:
            user = _me(await get_principal(request, session))
        except HTTPException:
            user = None
    return AuthStatus(setup_required=not has_users, user=user)


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(
    body: SetupIn, request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> Me:
    """Create the first administrator. Only works while no user exists."""
    _require_client_header(request)
    check_password_strength(body.password, body.email)
    request.state.actor = body.email
    async with session.begin():
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SETUP_LOCK})
        if (await session.execute(select(func.count()).select_from(User))).scalar_one():
            raise HTTPException(status.HTTP_409_CONFLICT, detail="setup is already complete")
        user = User(
            email=body.email,
            name=body.name.strip() or body.email.split("@")[0],
            password_hash=hash_password(body.password),
            role="admin",
            disabled=False,
            created_at=utcnow(),
        )
        session.add(user)
        await session.flush()
        await _start_session(session, settings, request, response, user)
    return Me(id=user.id, email=user.email, name=user.name, role="admin", via="session")


@router.post("/login")
async def login(
    body: LoginIn, request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> Me:
    _require_client_header(request)
    request.state.actor = body.email
    key = f"{client_ip(request)}|{body.email}"
    limiter = _limiter(request)
    limiter.check(key)
    async with session.begin():
        user = (
            await session.execute(select(User).where(User.email == body.email))
        ).scalar_one_or_none()
        ok = verify_password(user.password_hash if user else None, body.password)
        if user is None or not ok or user.disabled:
            limiter.failed(key)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="incorrect email or password")
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(body.password)
        await _start_session(session, settings, request, response, user)
    limiter.reset(key)
    return Me(id=user.id, email=user.email, name=user.name, role=user.role, via="session")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, session: SessionDep, settings: SettingsDep) -> Response:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        async with session.begin():
            await session.execute(
                delete(UserSession).where(UserSession.token_hash == sha256(cookie))
            )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.secure_cookies, httponly=True, samesite="strict"
    )
    return response


@router.get("/me")
async def me(principal: PrincipalDep) -> Me:
    return _me(principal)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordIn, request: Request, principal: PrincipalDep, session: SessionDep
) -> Response:
    """Change your password. Signs out your other sessions."""
    check_password_strength(body.new_password, principal.email)
    async with session.begin():
        user = await session.get(User, principal.user_id, with_for_update=True)
        if user is None or not verify_password(user.password_hash, body.current_password):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail="current password is incorrect"
            )
        user.password_hash = hash_password(body.new_password)
        keep = sha256(request.cookies.get(SESSION_COOKIE, ""))
        await session.execute(
            delete(UserSession).where(
                UserSession.user_id == user.id, UserSession.token_hash != keep
            )
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _token_out(t: ApiToken) -> TokenOut:
    return TokenOut(
        id=t.id,
        name=t.name,
        prefix=t.prefix,
        created_at=t.created_at,
        last_used_at=t.last_used_at,
        expires_at=t.expires_at,
    )


@router.get("/tokens")
async def list_tokens(principal: PrincipalDep, session: SessionDep) -> list[TokenOut]:
    rows = (
        await session.execute(
            select(ApiToken)
            .where(ApiToken.user_id == principal.user_id, ApiToken.revoked_at.is_(None))
            .order_by(ApiToken.created_at.desc())
        )
    ).scalars()
    return [_token_out(t) for t in rows]


@router.post("/tokens", status_code=status.HTTP_201_CREATED)
async def create_token(body: TokenIn, principal: PrincipalDep, session: SessionDep) -> TokenCreated:
    """A personal API token with your role. The value is shown only in this response."""
    token = new_token(API_TOKEN_PREFIX)
    now = utcnow()
    async with session.begin():
        row = ApiToken(
            user_id=principal.user_id,
            name=body.name.strip(),
            prefix=token[:12],
            token_hash=sha256(token),
            created_at=now,
            expires_at=now + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
        )
        session.add(row)
    return TokenCreated(**_token_out(row).model_dump(), token=token)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: uuid.UUID, principal: PrincipalDep, session: SessionDep
) -> Response:
    async with session.begin():
        result = await session.execute(
            update(ApiToken)
            .where(
                ApiToken.id == token_id,
                ApiToken.user_id == principal.user_id,
                ApiToken.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="token not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
