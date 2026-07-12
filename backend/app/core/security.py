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


def create_session_token(settings: Settings) -> str:
    payload = {
        "sub": settings.auth_username,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.auth_session_hours * 3600,
    }
    encoded = _encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(settings.auth_secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_encode(signature)}"


def verify_session_token(token: str, settings: Settings) -> bool:
    try:
        encoded, supplied = token.split(".", 1)
        expected = hmac.new(settings.auth_secret.encode(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(supplied)):
            return False
        payload = json.loads(_decode(encoded))
        return payload.get("sub") == settings.auth_username and int(payload.get("exp", 0)) > int(time.time())
    except (ValueError, TypeError, json.JSONDecodeError):
        return False


def require_auth(
    request: Request,
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    settings: Settings = Depends(get_settings),
) -> str:
    if not settings.auth_enabled:
        return "auth-disabled"
    if not token or not verify_session_token(token, settings):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in settings.cors_origins:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid request origin")
    return settings.auth_username
