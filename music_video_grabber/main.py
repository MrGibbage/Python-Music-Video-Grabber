from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import __version__
from .config import Settings, get_settings
from .db import Database, database_from_settings, utcnow
from .jobs import JobProcessor
from .logging_config import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    database_from_settings(settings).initialize()
    yield


app = FastAPI(
    title="Music Video Grabber",
    version=__version__,
    description="Durable acquisition API for the weekly Alt Nation Top 18.",
    lifespan=lifespan,
)
templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))


def get_db(settings: Settings = Depends(get_settings)) -> Database:
    return database_from_settings(settings)


def require_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.api_token:
        if settings.environment.lower() == "production":
            raise HTTPException(status_code=503, detail="API token is not configured")
        return
    expected = f"Bearer {settings.api_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


class RunRequest(BaseModel):
    station: str = "altnation"


class CandidateDecision(BaseModel):
    candidate_id: int


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health/live")
async def live():
    return {"status": "ok", "version": __version__}


@app.get("/health/ready")
async def ready(
    settings: Settings = Depends(get_settings), db: Database = Depends(get_db)
):
    try:
        db.one("SELECT 1 AS ok")
        media = settings.media_dir.exists() and settings.media_dir.is_dir()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not media:
        raise HTTPException(status_code=503, detail="Media directory is unavailable")
    return {"status": "ready", "media_directory": str(settings.media_dir)}


@app.post("/api/v1/runs", dependencies=[Depends(require_token)], status_code=202)
async def create_run(payload: RunRequest, db: Database = Depends(get_db)):
    if payload.station != "altnation":
        raise HTTPException(status_code=422, detail="The MVP supports only altnation")
    active = db.one(
        """SELECT id FROM runs WHERE status IN ('queued','running','processing')
           ORDER BY id DESC LIMIT 1"""
    )
    if active:
        raise HTTPException(status_code=409, detail=f"Run {active['id']} is already active")
    run_id = db.create_run(payload.station)
    return {"id": run_id, "status": "queued"}


@app.get("/api/v1/runs", dependencies=[Depends(require_token)])
async def list_runs(db: Database = Depends(get_db)):
    rows = db.query("SELECT * FROM runs ORDER BY id DESC LIMIT 50")
    for row in rows:
        row["summary"] = json.loads(row.pop("summary_json") or "{}")
    return rows


@app.get("/api/v1/runs/{run_id}", dependencies=[Depends(require_token)])
async def get_run(run_id: int, db: Database = Depends(get_db)):
    row = db.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    row["summary"] = json.loads(row.pop("summary_json") or "{}")
    row["jobs"] = db.query("SELECT * FROM jobs WHERE run_id=? ORDER BY id", (run_id,))
    row["events"] = db.query("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))
    return row


@app.get("/api/v1/tracks", dependencies=[Depends(require_token)])
async def list_tracks(
    track_status: str | None = None, limit: int = 100, db: Database = Depends(get_db)
):
    limit = min(max(limit, 1), 500)
    if track_status:
        return db.query(
            "SELECT * FROM tracks WHERE status=? ORDER BY updated_at DESC LIMIT ?",
            (track_status, limit),
        )
    return db.query("SELECT * FROM tracks ORDER BY updated_at DESC LIMIT ?", (limit,))


@app.get("/api/v1/tracks/{track_id}", dependencies=[Depends(require_token)])
async def get_track(track_id: int, db: Database = Depends(get_db)):
    track = db.one("SELECT * FROM tracks WHERE id=?", (track_id,))
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    candidates = db.query(
        "SELECT * FROM candidates WHERE track_id=? ORDER BY score DESC", (track_id,)
    )
    for candidate in candidates:
        candidate["reasons"] = json.loads(candidate.pop("reasons_json") or "[]")
    track["candidates"] = candidates
    return track


@app.get("/api/v1/review", dependencies=[Depends(require_token)])
async def review_queue(db: Database = Depends(get_db)):
    tracks = db.query("SELECT * FROM tracks WHERE status='review' ORDER BY updated_at DESC")
    for track in tracks:
        candidates = db.query(
            """SELECT * FROM candidates WHERE track_id=? AND rejected=0
               ORDER BY score DESC, id LIMIT 10""",
            (track["id"],),
        )
        for candidate in candidates:
            candidate["reasons"] = json.loads(candidate.pop("reasons_json") or "[]")
        track["candidates"] = candidates
    return tracks


@app.post(
    "/api/v1/tracks/{track_id}/approve",
    dependencies=[Depends(require_token)],
    status_code=202,
)
async def approve_candidate(
    track_id: int,
    decision: CandidateDecision,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
):
    try:
        job_id = JobProcessor(settings, db).approve(track_id, decision.candidate_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/v1/tracks/{track_id}/reject", dependencies=[Depends(require_token)])
async def reject_candidate(
    track_id: int,
    decision: CandidateDecision,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
):
    try:
        JobProcessor(settings, db).reject(track_id, decision.candidate_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "rejected"}


@app.get("/api/v1/jobs", dependencies=[Depends(require_token)])
async def list_jobs(limit: int = 100, db: Database = Depends(get_db)):
    return db.query("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),))


@app.post("/api/v1/jobs/{job_id}/retry", dependencies=[Depends(require_token)], status_code=202)
async def retry_job(job_id: int, db: Database = Depends(get_db)):
    with db.connect() as conn:
        result = conn.execute(
            """UPDATE jobs SET status='queued', attempts=0, error=NULL, available_at=?,
               started_at=NULL, finished_at=NULL WHERE id=? AND status='failed'""",
            (utcnow(), job_id),
        )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="Only failed jobs can be retried")
    return {"id": job_id, "status": "queued"}


@app.post("/api/v1/jobs/{job_id}/cancel", dependencies=[Depends(require_token)])
async def cancel_job(job_id: int, db: Database = Depends(get_db)):
    with db.connect() as conn:
        result = conn.execute(
            "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status='queued'",
            (utcnow(), job_id),
        )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="Only queued jobs can be cancelled")
    return {"id": job_id, "status": "cancelled"}


@app.post("/api/v1/catalog/import", dependencies=[Depends(require_token)], status_code=202)
async def queue_catalog_import(db: Database = Depends(get_db)):
    active = db.one(
        "SELECT id FROM jobs WHERE kind='catalog_import' AND status IN ('queued','running') LIMIT 1"
    )
    if active:
        raise HTTPException(status_code=409, detail=f"Import job {active['id']} is active")
    job_id = db.enqueue("catalog_import", {})
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/v1/events", dependencies=[Depends(require_token)])
async def list_events(limit: int = 100, db: Database = Depends(get_db)):
    return db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),))
