from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import quote_plus

import httpx

from app.core.config import get_settings


def dingtalk_status() -> dict[str, object]:
    settings = get_settings()
    return {
        "enabled": settings.dingtalk_enabled,
        "configured": bool(settings.dingtalk_webhook),
        "signed": bool(settings.dingtalk_secret),
    }


def send_dingtalk_markdown(title: str, text: str) -> dict:
    settings = get_settings()
    if not settings.dingtalk_enabled or not settings.dingtalk_webhook:
        raise RuntimeError("钉钉机器人尚未启用或未配置 Webhook")
    url = settings.dingtalk_webhook
    if settings.dingtalk_secret:
        timestamp = str(round(time.time() * 1000))
        digest = hmac.new(settings.dingtalk_secret.encode(), f"{timestamp}\n{settings.dingtalk_secret}".encode(), hashlib.sha256).digest()
        sign = quote_plus(base64.b64encode(digest).decode())
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}timestamp={timestamp}&sign={sign}"
    response = httpx.post(url, json={"msgtype": "markdown", "markdown": {"title": title, "text": text}}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if int(payload.get("errcode") or 0) != 0:
        raise RuntimeError(str(payload.get("errmsg") or "钉钉发送失败"))
    return payload
