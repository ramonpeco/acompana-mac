"""Single-tenant admin auth: bcrypt password + HS256 JWT bearer tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import AuditLog, User

bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(username: str, role: str) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {"sub": username, "role": role, "iat": now, "exp": now + timedelta(minutes=s.jwt_ttl_minutes)}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])


async def ensure_admin(session: AsyncSession) -> None:
    """Create the admin user on first boot from ADMIN_PASSWORD_HASH / ADMIN_PASSWORD."""
    s = get_settings()
    existing = (await session.execute(select(User).where(User.username == s.admin_username))).scalar_one_or_none()
    pw_hash = s.admin_password_hash or (hash_password(s.admin_password) if s.admin_password else "")
    if existing is None and pw_hash:
        session.add(User(username=s.admin_username, password_hash=pw_hash, role="admin"))
        await session.commit()
    elif existing is not None and s.admin_password_hash and existing.password_hash != s.admin_password_hash:
        existing.password_hash = s.admin_password_hash
        await session.commit()


async def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing token")
    try:
        return decode_token(creds.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc


async def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user


async def audit(session: AsyncSession, actor: str, action: str, **details) -> None:
    session.add(AuditLog(actor=actor, action=action, details=details))


__all__ = ["audit", "create_token", "current_user", "ensure_admin", "get_session", "require_admin", "verify_password"]
