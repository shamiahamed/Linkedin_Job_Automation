"""Authentication for the API.

Two accepted identities:
  1. Shared API token (X-API-Key / Authorization: Bearer) — used by the
     browser extension and any scripts.
  2. Signed session cookie — issued by the web login page for the dashboard.

Local dev (no token AND no APP_PASSWORD) stays open so the CLI works.
"""
import base64
import hashlib
import hmac
import secrets
import time
from typing import Optional

from fastapi import Header, HTTPException, Request
from config import Config

_SESSION_TTL = 60 * 60 * 24 * 30  # 30 days


def _sign(payload: str) -> str:
    return hmac.new(Config.SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session_token() -> str:
    payload = f"{int(time.time())}:{secrets.token_hex(8)}"
    return base64.urlsafe_b64encode(f"{payload}:{_sign(payload)}".encode()).decode()


def _check_session(cookie: Optional[str]) -> bool:
    if not cookie:
        return False
    try:
        raw = base64.urlsafe_b64decode(cookie.encode()).decode()
        payload, sig = raw.rsplit(":", 1)
        ts = int(payload.split(":", 1)[0])
    except Exception:
        return False
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    if time.time() - ts > _SESSION_TTL:
        return False
    return True


def has_session(request: Request) -> bool:
    return _check_session(request.cookies.get("session"))


def require_auth(
    request: Request,
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> None:
    expected = (Config.API_TOKEN or "").strip()
    if expected:
        token = (x_api_key or authorization or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token == expected:
            return
    if _check_session(request.cookies.get("session")):
        return
    # Fully open local dev: no token AND no login configured.
    if not expected and not (Config.APP_PASSWORD or "").strip():
        return
    raise HTTPException(status_code=401, detail="authentication required")