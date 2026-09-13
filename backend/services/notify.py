"""Web-push notifications for the dashboard PWA (mobile + desktop).

VAPID keypair is generated once on first use and persisted in the `settings`
table, so no env vars are needed. Subscriptions live in `push_subscriptions`.

Only active when GROQ is optional — this module is fully independent and simply
no-ops when there are no subscriptions or the push service is unreachable.
"""
import base64
import json
import logging

logger = logging.getLogger("uvicorn.error")


def _b64u_decode(s):
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode((s + pad).replace("-", "+").replace("_", "/"))


def _b64u_encode(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _public_point_from_scalar(raw_scalar):
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = ec.derive_private_key(
        int.from_bytes(raw_scalar, "big"), ec.SECP256R1(), default_backend()
    )
    return key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint  # 65 bytes (0x04 || X || Y)
    )


def _build_vapid_signer(priv):
    """Build the VAPID ES256 signer whose public key == the stored public key.

    CRITICAL: py_vapid's Vapid01.from_raw() does NOT interpret its input as a
    P-256 scalar — passing our raw 32-byte scalar yields a *different* key, so
    every JWT was signed with the wrong key and FCM replied 403 "VAPID
    credentials do not correspond to the credentials used to create the
    subscriptions". Deriving the key via cryptography and loading it as a PEM
    gives a signer whose public key matches what the browser subscribed to.
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01

    raw = _b64u_decode(priv)
    key = ec.derive_private_key(
        int.from_bytes(raw, "big"), ec.SECP256R1(), default_backend()
    )
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return Vapid01.from_pem(pem)


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
    applicationServerKey); private is the base64url raw 32-byte P-256 scalar,
    which py_vapid's Vapid01.from_raw() can rebuild for signing."""
    db = _db()
    try:
        pub = _get_setting(db, "vapid_pub", "")
        priv = _get_setting(db, "vapid_priv", "")
        if pub and priv:
            try:
                raw = _b64u_decode(priv)
                if len(raw) == 32:
                    # CRITICAL: only trust the pair if the private scalar actually
                    # derives to the stored public key. A mismatch means the two
                    # settings rows drifted apart (e.g. from earlier dev regens),
                    # and signing with the stale scalar makes the push service
                    # return HTTP 403 "VAPID credentials do not correspond..."
                    derived = _b64u_encode(
                        _public_point_from_scalar(raw)
                    )
                    if derived == pub:
                        return pub, priv
                    logger.warning(
                        "VAPID pair mismatch (stored pub != derived pub); regenerating"
                    )
            except Exception:
                pass
            # Legacy PEM-encoded key (py_vapid parser can't load PKCS8 ECDSA). Regenerate.

        import base64
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization

        key = ec.generate_private_key(ec.SECP256R1())
        raw_scalar = key.private_numbers().private_value.to_bytes(32, "big")
        pub_point = key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,  # 65 bytes (0x04 || X || Y)
        )
        pub = base64.urlsafe_b64encode(pub_point).rstrip(b"=").decode("ascii")
        priv = base64.urlsafe_b64encode(raw_scalar).rstrip(b"=").decode("ascii")
        _set_setting(db, "vapid_pub", pub)
        _set_setting(db, "vapid_priv", priv)
        db.commit()
        return pub, priv
    except Exception:
        logger.exception("VAPID key generation failed")
        return None, None
    finally:
        db.close()


def push(title: str, body: str, url: str = "/dashboard", icon: str = "", _report=None):
    """Send a web push to every registered subscription. Best-effort: never raises."""
    if _report is None:
        _report = []
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

        # Rebuild a signer from the raw scalar; pywebpush accepts a Vapid01
        # instance directly and skips its Parser (which rejects PKCS8 PEM).
        vv = _build_vapid_signer(priv)

        claims = {
            "sub": f"mailto:{Config.YOUR_EMAIL or 'admin@localhost'}",
        }
        sent = 0
        stale = []
        payload = json.dumps(
            {"title": title, "body": body, "url": url, "icon": icon},
            ensure_ascii=False,
        ).encode("utf-8")
        for s in subs:
            try:
                webpush(
                    subscription_info={
                        "endpoint": s.endpoint,
                        "keys": {"p256dh": s.p256dh, "auth": s.auth},
                    },
                    data=payload,
                    vapid_private_key=vv,
                    vapid_claims=claims,
                    timeout=15,
                    # Queue offline deliveries: FCM holds the message up to 12h and
                    # delivers it when the device reconnects. Fixes "missed 9 AM
                    # reminder / job captured while phone offline" — falls back to
                    # instant delivery whenever the device is already online.
                    ttl=43200,
                )
                sent += 1
            except WebPushException as e:
                # 404/410 -> subscription gone; drop it. For anything else we keep
                # the sub and surface the push service response so failures are
                # actually visible and diagnosable (e.g. VAPID/audience issues).
                sc = getattr(e.response, "status_code", None)
                body = ""
                try:
                    body = (e.response.text or "").strip()[:300]
                except Exception:
                    pass
                if not _report:
                    _report.append(f"push service HTTP {sc}: {body}")
                logger.warning(
                    "webpush failed for sub %s: status=%s body=%s",
                    s.id, sc, body,
                )
                if sc in (404, 410):
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
    report = []
    sent = push(
        "Job Auto-Apply ✅",
        "Notifications work on this device. You'll get alerts for new jobs, applications and reminders.",
        "/dashboard",
        _report=report,
    )
    return sent, (report[0] if report else "")