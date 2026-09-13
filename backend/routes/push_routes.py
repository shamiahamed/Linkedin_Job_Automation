from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import PushSubscription


router = APIRouter(prefix="/api/push", tags=["push notifications"])


@router.get("/vapid-key")
def vapid_public_key():
    """Public applicationServerKey for navigator.pushManager.subscribe()."""
    from services.notify import _vapid_keys
    pub, _priv = _vapid_keys()
    if not pub:
        raise HTTPException(503, "VAPID key not available")
    return {"publicKey": pub}


@router.post("/register")
def register_subscription(payload: dict = Body(default=None), db: Session = Depends(get_db)):
    """Save a service-worker push subscription (from navigator.pushManager.subscribe)."""
    payload = payload or {}
    endpoint = (payload.get("endpoint") or "").strip()
    p256dh = (payload.get("p256dh") or "").strip()
    auth = (payload.get("auth") or "").strip()
    if not endpoint or not endpoint.startswith("https://"):
        raise HTTPException(400, "invalid push endpoint")
    if not p256dh or not auth:
        raise HTTPException(400, "missing push keys")

    existing = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if existing:
        existing.p256dh = p256dh
        existing.auth = auth
        db.commit()
        return {"success": True, "registered": True, "updated": True}
    db.add(PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth))
    db.commit()
    return {"success": True, "registered": True}


@router.post("/unregister")
def unregister_subscription(payload: dict = Body(default=None), db: Session = Depends(get_db)):
    """Remove a push subscription (device disabled notifications)."""
    payload = payload or {}
    endpoint = (payload.get("endpoint") or "").strip()
    if not endpoint:
        raise HTTPException(400, "endpoint required")
    deleted = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).delete()
    db.commit()
    return {"success": True, "deleted": deleted}


@router.post("/test")
def send_test_push():
    from services.notify import test_push
    sent = test_push()
    return {"success": sent > 0, "sent": sent}