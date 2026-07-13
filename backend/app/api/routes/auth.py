import base64
import hashlib
import hmac
import struct
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.limiter import limiter
from app.core.security import SESSION_COOKIE, create_session_token, require_auth

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    otp: str = Field(default="", max_length=12)


def _valid_totp(secret: str, otp: str, now: int | None = None) -> bool:
    if not secret:
        return True
    try:
        key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret.replace(" ", "")) % 8))
    except (ValueError, TypeError):
        return False
    current = int(now or time.time()) // 30
    for offset in (-1, 0, 1):
        digest = hmac.new(key, struct.pack(">Q", current + offset), hashlib.sha1).digest()
        position = digest[-1] & 0x0F
        value = (struct.unpack(">I", digest[position:position + 4])[0] & 0x7FFFFFFF) % 1_000_000
        if hmac.compare_digest(f"{value:06d}", otp.strip()):
            return True
    return False


@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, payload: LoginRequest, response: Response, settings: Settings = Depends(get_settings)) -> dict[str, str]:
    if not settings.auth_enabled:
        return {"status": "disabled", "username": ""}
    admin_valid = (
        hmac.compare_digest(payload.username, settings.auth_username)
        and hmac.compare_digest(payload.password, settings.auth_password)
        and _valid_totp(settings.auth_totp_secret, payload.otp)
    )
    demo_valid = bool(settings.demo_password) and hmac.compare_digest(payload.username, settings.demo_username) and hmac.compare_digest(payload.password, settings.demo_password)
    valid = admin_valid or demo_valid
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(settings, payload.username),
        max_age=settings.auth_session_hours * 3600,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )
    return {"status": "authenticated", "username": payload.username}


@router.get("/session")
def session(username: str = Depends(require_auth)) -> dict[str, str]:
    return {"status": "authenticated", "username": username}


@router.post("/logout")
def logout(response: Response, _username: str = Depends(require_auth)) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "logged_out"}
