import base64
import hashlib
import hmac
import json
import time

from fastapi import Cookie, Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings

SESSION_COOKIE = "tds_session"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_session_token(settings: Settings, username: str | None = None) -> str:
    payload = {
        "sub": username or settings.auth_username,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.auth_session_hours * 3600,
    }
    encoded = _encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(settings.auth_secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_encode(signature)}"


def session_subject(token: str, settings: Settings) -> str | None:
    try:
        encoded, supplied = token.split(".", 1)
        expected = _encode(hmac.new(settings.auth_secret.encode(), encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, supplied):
            return None
        payload = json.loads(_decode(encoded))
        subject = str(payload.get("sub") or "")
        allowed = {settings.auth_username}
        if settings.demo_password:
            allowed.add(settings.demo_username)
        return subject if subject in allowed and int(payload.get("exp", 0)) > int(time.time()) else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def verify_session_token(token: str, settings: Settings) -> bool:
    return session_subject(token, settings) is not None


def require_auth(
    request: Request,
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    settings: Settings = Depends(get_settings),
) -> str:
    if not settings.auth_enabled:
        return "auth-disabled"
    username = session_subject(token, settings) if token else None
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    request.state.auth_user = username
    if username == settings.demo_username and request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path != "/api/auth/logout":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="体验账号为只读模式")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in settings.cors_origins:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid request origin")
    return username
