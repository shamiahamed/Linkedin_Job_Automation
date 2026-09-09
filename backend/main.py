import asyncio
import os
import time
import json
import uvicorn
from pathlib import Path
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
from config import Config
from database import Base, engine
from security import require_auth, make_session_token, has_session
from routes import jobs, applications, email_routes, resumes


# Create tables on startup — never crash a worker if the DB is cold/ waking
# (Neon scales to zero): retry in the startup event instead.
def _ensure_tables(retries: int = 4, wait: float = 5.0):
    import time

    for i in range(retries):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except Exception:
            if i == retries - 1:
                return
            time.sleep(wait)


_ensure_tables()

# Lightweight migration: add apply_link to existing SQLite DB (idempotent, SQLite only)
if Config.DATABASE_URL.startswith("sqlite"):
    try:
        from sqlalchemy import text as _sql

        with engine.connect() as _conn:
            _cols = [r[1] for r in _conn.execute(_sql("PRAGMA table_info(jobs)")).fetchall()]
            if "apply_link" not in _cols:
                _conn.execute(_sql("ALTER TABLE jobs ADD COLUMN apply_link TEXT"))
                _conn.commit()
    except Exception:
        pass

app = FastAPI(
    title=Config.APP_NAME,
    version=Config.APP_VERSION,
    description="Automated job application assistant",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["X-DNS-Prefetch-Control"] = "off"
        return response


app.add_middleware(SecurityHeaders)

_AUTH = [Depends(require_auth)]

app.include_router(jobs.router, dependencies=_AUTH)
app.include_router(applications.router, dependencies=_AUTH)
app.include_router(email_routes.router, dependencies=_AUTH)
app.include_router(resumes.router, dependencies=_AUTH)

from fastapi.responses import RedirectResponse, JSONResponse

app.mount("/static", StaticFiles(directory=Config.BASE_DIR / "static"), name="static")


@app.get("/dashboard")
def dashboard():
    return RedirectResponse(url="/static/dashboard/index.html")


@app.get("/dashboard/jobs")
def dashboard_jobs():
    return RedirectResponse(url="/static/dashboard/index.html")


DEBUG_LOG = Path(__file__).parent / "debug.log"


@app.get("/health", include_in_schema=False)
def health():
    """Public readiness probe for Render — deliberately OUTSIDE the API token
    gate so platform health checks (no auth header) can reach it."""
    return {"status": "ok", "app": Config.APP_NAME}


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


@app.post("/api/auth/login")
def auth_login(request: Request, body: LoginBody):
    import hmac as _hmac

    pw_configured = (Config.APP_PASSWORD or "").strip()
    ok = (
        pw_configured
        and _hmac.compare_digest(body.username.strip(), Config.APP_USERNAME)
        and _hmac.compare_digest(body.password, pw_configured)
    )
    if not ok:
        raise HTTPException(status_code=401, detail="invalid username or password")
    resp = JSONResponse({"ok": True, "name": Config.YOUR_NAME})
    resp.set_cookie(
        "session",
        make_session_token(),
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return resp


@app.post("/api/auth/logout")
def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session", path="/")
    return resp


@app.get("/api/auth/me")
def auth_me(request: Request):
    open_dev = not (Config.API_TOKEN or "").strip() and not (Config.APP_PASSWORD or "").strip()
    if open_dev:
        return {"authenticated": True}
    return {"authenticated": has_session(request)}


@app.post("/api/debug/log", dependencies=_AUTH)
async def debug_log(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    line = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(), **body}
    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=str) + "\n")
    return {"ok": True}


@app.get("/api/debug/logs", dependencies=_AUTH)
def debug_logs(limit: int = 500):
    lines = []
    if DEBUG_LOG.exists():
        lines = DEBUG_LOG.read_text(encoding="utf-8").splitlines()[-limit:]
    return {"logs": lines}


@app.get("/")
def root():
    return {
        "app": Config.APP_NAME,
        "version": Config.APP_VERSION,
        "endpoints": [
            "GET  /api/jobs",
            "POST /api/jobs",
            "GET  /api/jobs/{id}",
            "POST /api/jobs/{id}/apply",
            "GET  /api/applications",
            "PUT  /api/applications/{id}",
            "POST /api/ocr/upload",
            "GET  /api/ocr/status",
            "POST /api/email/test",
            "GET  /api/stats",
            "GET  /health",
        ],
    }


@app.on_event("startup")
async def _startup():
    """Create tables with retry (Neon wakes from zero on first connect), then
    run the periodic no-contact cleaner."""
    _ensure_tables(retries=3, wait=3.0)
    from routes.jobs import cleanup_no_contact
    from database import SessionLocal

    async def _loop():
        while True:
            try:
                db = SessionLocal()
                try:
                    cleanup_no_contact(db)
                finally:
                    db.close()
            except Exception:
                pass
            await asyncio.sleep(6 * 3600)

    asyncio.create_task(_loop())


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=Config.API_HOST,
        port=Config.API_PORT,
        reload=Config.DEBUG,
    )