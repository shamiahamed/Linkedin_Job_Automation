import asyncio
import os
import time
import json
import uvicorn
from pathlib import Path
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from config import Config
from database import Base, engine
from security import require_api_token
from routes import jobs, applications, email_routes


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

_AUTH = [Depends(require_api_token)]

app.include_router(jobs.router, dependencies=_AUTH)
app.include_router(applications.router, dependencies=_AUTH)
app.include_router(email_routes.router, dependencies=_AUTH)

from fastapi.responses import RedirectResponse

app.mount("/static", StaticFiles(directory=Config.BASE_DIR / "static"), name="static")


@app.get("/dashboard")
def dashboard():
    return RedirectResponse(url="/static/dashboard/index.html")


@app.get("/dashboard/jobs")
def dashboard_jobs():
    return RedirectResponse(url="/static/dashboard/index.html")


DEBUG_LOG = Path(__file__).parent / "debug.log"


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