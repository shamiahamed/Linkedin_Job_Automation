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


@router.get("/selfcheck")
def vapid_selfcheck():
    """Diagnostic: confirm the pub key we serve actually derives from the priv
    key we sign with (guards against the 403 'credentials do not correspond'
    failure caused by a drifted settings pair)."""
    from services.notify import _vapid_keys, _b64u_decode, _public_point_from_scalar, _b64u_encode
    pub, priv = _vapid_keys()
    if not pub or not priv:
        raise HTTPException(503, "VAPID key not available")
    derived = _b64u_encode(_public_point_from_scalar(_b64u_decode(priv)))
    return {
        "publicKey": pub,
        "publicKey_derived": derived,
        "match": pub == derived,
    }


@router.post("/register")
def register_subscription(payload: dict = Body(default=None), db: Session = Depends(get_db)):
    """Save a service-worker push subscription (from navigator.pushManager.subscribe)."""
    payload = payload or {}
    endpoint = (payload.get("endpoint") or "").strip()
    keys = payload.get("keys") or {}
    p256dh = (payload.get("p256dh") or keys.get("p256dh") or "").strip()
    auth = (payload.get("auth") or keys.get("auth") or "").strip()
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
def send_test_push(db: Session = Depends(get_db)):
    from services.notify import test_push
    from models import PushSubscription
    registered = db.query(PushSubscription).count()
    sent, detail = test_push()
    return {"success": sent > 0, "sent": sent, "registered": registered, "detail": detail}