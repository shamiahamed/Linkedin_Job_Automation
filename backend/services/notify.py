"""Web-push notifications for the dashboard PWA (mobile + desktop).

VAPID keypair is generated once on first use and persisted in the `settings`
table, so no env vars are needed. Subscriptions live in `push_subscriptions`.

Only active when GROQ is optional — this module is fully independent and simply
no-ops when there are no subscriptions or the push service is unreachable.
"""
import base64
import logging

logger = logging.getLogger("uvicorn.error")


def _db():
    from database import SessionLocal
    return SessionLocal()


def _get_setting(db, key, default=""):
    from models import Setting
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else default


def _set_setting(db, key, value):
    from models import Setting
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def _vapid_keys():
    """Return (public, private) VAPID keys, generating + persisting on first call.
    public is the base64url 65-byte uncompressed P-256 point (the browser's
    applicationServerKey); private is a PKCS8 PEM (for signing/webpush)."""
    db = _db()
    try:
        pub = _get_setting(db, "vapid_pub", "")
        priv = _get_setting(db, "vapid_priv", "")
        if pub and priv:
            return pub, priv

        import base64
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization

        key = ec.generate_private_key(ec.SECP256R1())
        priv_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        pub_point = key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,  # 65 bytes (0x04 || X || Y)
        )
        pub = base64.urlsafe_b64encode(pub_point).rstrip(b"=").decode("ascii")
        _set_setting(db, "vapid_pub", pub)
        _set_setting(db, "vapid_priv", priv_pem)
        db.commit()
        return pub, priv_pem
    except Exception:
        logger.exception("VAPID key generation failed")
        return None, None
    finally:
        db.close()


def push(title: str, body: str, url: str = "/dashboard", icon: str = ""):
    """Send a web push to every registered subscription. Best-effort: never raises."""
    if not title and not body:
        return 0
    db = _db()
    try:
        from models import PushSubscription

        subs = db.query(PushSubscription).all()
        if not subs:
            return 0
        pub, priv = _vapid_keys()
        if not pub or not priv:
            return 0

        from config import Config
        from pywebpush import webpush, WebPushException

        claims = {
            "sub": f"mailto:{Config.YOUR_EMAIL or 'admin@localhost'}",
            "aud": "",
        }
        sent = 0
        stale = []
        for s in subs:
            try:
                webpush(
                    subscription_info={
                        "endpoint": s.endpoint,
                        "keys": {"p256dh": s.p256dh, "auth": s.auth},
                    },
                    data={"title": title, "body": body, "url": url, "icon": icon},
                    vapid_private_key=priv,
                    vapid_claims=claims,
                    timeout=15,
                )
                sent += 1
            except WebPushException as e:
                # 404/410 -> subscription dead; drop it. 403 -> the VAPID key used to
                # subscribe is stale (key rotated in dev), so re-enabling will use the
                # current key — self-healing instead of failing forever.
                sc = getattr(e.response, "status_code", None)
                if sc in (404, 410, 403):
                    stale.append(s.id)
            except Exception:
                pass
        if stale:
            db.query(PushSubscription).filter(
                PushSubscription.id.in_(stale)
            ).delete(synchronize_session=False)
            db.commit()
        return sent
    except Exception:
        logger.exception("push() failed")
        return 0
    finally:
        db.close()


def test_push():
    """Send a test notification (used by POST /api/push/test)."""
    return push(
        "Job Auto-Apply ✅",
        "Notifications work on this device. You'll get alerts for new jobs, applications and reminders.",
        "/dashboard",
    )