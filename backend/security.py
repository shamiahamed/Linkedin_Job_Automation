"""Shared-secret API token gate.
When Config.API_TOKEN is empty (local dev) every request passes.
When set (deployed), all /api/* routes require X-API-Key: <token>
(or Authorization: Bearer <token>)."""
from typing import Optional
from fastapi import Header, HTTPException
from config import Config


def require_api_token(
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> None:
    expected = (Config.API_TOKEN or "").strip()
    if not expected:
        return
    token = (x_api_key or authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing API token")