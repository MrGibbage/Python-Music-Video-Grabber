from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from . import __version__
from .auth import (
    ALL_SCOPES,
    SESSION_COOKIE,
    authorize,
    dashboard_authenticated,
    issue_api_token,
    make_session,
)
from .config import Settings, get_settings
from .db import Database, database_from_settings, utcnow
from .jobs import JobProcessor
from .logging_config import configure_logging
from .plex import PlexReadOnlyClient
from .plex_playlists import PlexPlaylistClient, build_playlist_plans

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


@app.middleware("http")
async def disable_dashboard_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def get_db(settings: Settings = Depends(get_settings)) -> Database:
    return database_from_settings(settings)


def require_scope(scope: str):
    def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        settings: Settings = Depends(get_settings),
        db: Database = Depends(get_db),
    ) -> None:
        authorize(request, authorization, settings, db, scope)

    return dependency


def require_dashboard(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    if not dashboard_authenticated(request, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Dashboard sign-in required"
        )


def verify_dashboard_configuration(settings: Settings) -> None:
    if not settings.app_password or not settings.session_signing_secret:
        raise HTTPException(status_code=503, detail="Dashboard sign-in is not configured")


class RunRequest(BaseModel):
    station: str = "altnation"
    song_count: int | None = Field(default=None, ge=1, le=100)

    @field_validator("station")
    @classmethod
    def validate_station(cls, value: str) -> str:
        station = value.strip().lower()
        if not station or len(station) > 64 or not station.replace("-", "").isalnum():
            raise ValueError("station must use letters, numbers, and hyphens only")
        return station


class CandidateDecision(BaseModel):
    candidate_id: int


class ApiTokenRequest(BaseModel):
    name: str
    scopes: set[str] = {"read"}


class PlexPlaylistPreferencesRequest(BaseModel):
    include_top_18: bool = True
    include_new: bool = True
    include_older: bool = True
    remove_unplanned_items: bool = False


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, settings: Settings = Depends(get_settings)):
    if not dashboard_authenticated(request, settings):
        return templates.TemplateResponse(request=request, name="login.html")
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/auth/login")
async def login(
    request: Request, password: str = Form(), settings: Settings = Depends(get_settings)
):
    verify_dashboard_configuration(settings)
    import hmac

    if not hmac.compare_digest(password, settings.app_password):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Incorrect password"},
            status_code=401,
        )
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        make_session(settings),
        max_age=settings.dashboard_session_ttl_hours * 3600,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@app.post("/auth/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=True, samesite="lax")
    return response


@app.get("/health/live")
async def live():
    return {"status": "ok", "version": __version__}


@app.get("/health/ready")
async def ready(settings: Settings = Depends(get_settings), db: Database = Depends(get_db)):
    try:
        db.one("SELECT 1 AS ok")
        media = settings.media_dir.exists() and settings.media_dir.is_dir()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not media:
        raise HTTPException(status_code=503, detail="Media directory is unavailable")
    return {"status": "ready", "media_directory": str(settings.media_dir)}


@app.get("/api/v1/operations/status", dependencies=[Depends(require_scope("read"))])
async def operations_status(
    settings: Settings = Depends(get_settings), db: Database = Depends(get_db)
):
    """Return local operational health without contacting Plex or other services."""
    try:
        db.one("SELECT 1 AS ok")
        database_size = settings.database_path.stat().st_size
        media_usage = shutil.disk_usage(settings.media_dir)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Local operational check failed: {exc}"
        ) from exc
    backup_dir = settings.database_path.parent / "backups"
    backups = (
        [path for path in backup_dir.glob("*.db") if path.is_file()]
        if backup_dir.exists()
        else []
    )
    latest_backup = max(backups, key=lambda path: path.stat().st_mtime, default=None)
    job_counts = {
        row["status"]: row["count"]
        for row in db.query("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status")
    }
    return {
        "database": {
            "filename": settings.database_path.name,
            "size_bytes": database_size,
            "latest_backup": (
                {
                    "filename": latest_backup.name,
                    "size_bytes": latest_backup.stat().st_size,
                    "created_at": datetime.fromtimestamp(latest_backup.stat().st_mtime).isoformat(),
                }
                if latest_backup
                else None
            ),
        },
        "media": {
            "available": True,
            "free_bytes": media_usage.free,
            "total_bytes": media_usage.total,
        },
        "jobs": job_counts,
    }


@app.post("/api/v1/runs", dependencies=[Depends(require_scope("runs:write"))], status_code=202)
async def create_run(payload: RunRequest, db: Database = Depends(get_db)):
    active = db.one(
        """SELECT id FROM runs WHERE status IN ('queued','running','processing')
           ORDER BY id DESC LIMIT 1"""
    )
    if active:
        raise HTTPException(status_code=409, detail=f"Run {active['id']} is already active")
    run_id = db.create_run(payload.station, payload.song_count)
    return {
        "id": run_id,
        "status": "queued",
        "station": payload.station,
        "song_count": payload.song_count,
    }


@app.get("/api/v1/runs", dependencies=[Depends(require_scope("read"))])
async def list_runs(db: Database = Depends(get_db)):
    rows = db.query("SELECT * FROM runs ORDER BY id DESC LIMIT 50")
    for row in rows:
        row["summary"] = json.loads(row.pop("summary_json") or "{}")
    return rows


@app.get("/api/v1/runs/{run_id}", dependencies=[Depends(require_scope("read"))])
async def get_run(run_id: int, db: Database = Depends(get_db)):
    row = db.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    row["summary"] = json.loads(row.pop("summary_json") or "{}")
    row["jobs"] = db.query("SELECT * FROM jobs WHERE run_id=? ORDER BY id", (run_id,))
    row["events"] = db.query("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))
    row["automatic_downloads"] = db.query(
        """SELECT t.id, t.artist, t.title, t.media_path
           FROM events e JOIN tracks t ON t.id=e.track_id
           WHERE e.run_id=? AND e.event='download_complete' ORDER BY e.id""",
        (run_id,),
    )
    review_labels = set(row["summary"].get("review_tracks", []))
    chart = db.chart_for_run(run_id)
    row["review_resolution"] = []
    if chart and review_labels:
        for entry in chart["entries"]:
            label = f"{entry['artist']} — {entry['title']}"
            if label not in review_labels or not entry["source_track_id"]:
                continue
            track = db.one(
                """SELECT id, artist, title, status, media_path, updated_at FROM tracks
                   WHERE station=? AND source_track_id=?""",
                (chart["station"], entry["source_track_id"]),
            )
            if track:
                row["review_resolution"].append(track)
    return row


@app.get("/api/v1/charts/latest", dependencies=[Depends(require_scope("read"))])
async def latest_chart(
    station: str = Query(default="altnation", min_length=1, max_length=64),
    db: Database = Depends(get_db),
):
    station = station.strip().lower()
    if not station or not station.replace("-", "").isalnum():
        raise HTTPException(
            status_code=422,
            detail="station must use letters, numbers, and hyphens only",
        )
    chart = db.latest_chart(station)
    if not chart:
        raise HTTPException(status_code=404, detail=f"No {station} chart has been captured yet")
    return chart


@app.get("/api/v1/stations/recent", dependencies=[Depends(require_scope("read"))])
async def recent_stations(limit: int = 12, db: Database = Depends(get_db)):
    """Return station IDs already used by MVG, newest first.

    xmplaylist remains the authority on whether a new free-form identifier is
    valid.  Remembering successful local requests avoids maintaining a fragile
    copied station directory.
    """
    return db.query(
        """SELECT station, MAX(requested_at) AS last_requested_at, COUNT(*) AS run_count
           FROM runs GROUP BY station
           ORDER BY last_requested_at DESC LIMIT ?""",
        (min(max(limit, 1), 50),),
    )


@app.get("/api/v1/tracks", dependencies=[Depends(require_scope("read"))])
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


@app.get("/api/v1/tracks/{track_id}", dependencies=[Depends(require_scope("read"))])
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


@app.get("/api/v1/tracks/{track_id}/media-info", dependencies=[Depends(require_scope("read"))])
async def media_info(
    track_id: int, settings: Settings = Depends(get_settings), db: Database = Depends(get_db)
):
    track = db.one("SELECT media_path FROM tracks WHERE id=? AND status='published'", (track_id,))
    if not track or not track["media_path"]:
        raise HTTPException(status_code=404, detail="Published media was not found for this track")
    filename = Path(track["media_path"])
    if filename.name != track["media_path"]:
        raise HTTPException(status_code=400, detail="Invalid stored media path")
    media = settings.media_dir / filename
    if not media.is_file():
        raise HTTPException(status_code=404, detail="Published media file is unavailable")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(media)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        probe = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Could not inspect published media") from exc
    format_info = probe.get("format", {})
    streams = probe.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    return {
        "filename": filename.name,
        "size_bytes": media.stat().st_size,
        "duration_seconds": float(format_info.get("duration") or 0),
        "container": format_info.get("format_long_name") or format_info.get("format_name"),
        "overall_bitrate": int(format_info.get("bit_rate") or 0),
        "video": {
            key: video.get(key)
            for key in (
                "codec_name",
                "profile",
                "width",
                "height",
                "pix_fmt",
                "avg_frame_rate",
                "bit_rate",
            )
        },
        "audio": {
            key: audio.get(key)
            for key in (
                "codec_name",
                "profile",
                "channels",
                "channel_layout",
                "sample_rate",
                "bit_rate",
            )
        },
    }


@app.get("/api/v1/review", dependencies=[Depends(require_scope("read"))])
async def review_queue(db: Database = Depends(get_db)):
    tracks = db.query("SELECT * FROM tracks WHERE status='review' ORDER BY updated_at DESC")
    for track in tracks:
        candidates = db.query(
            """SELECT * FROM candidates WHERE track_id=?
               ORDER BY rejected ASC, score DESC, id LIMIT 10""",
            (track["id"],),
        )
        for candidate in candidates:
            candidate["reasons"] = json.loads(candidate.pop("reasons_json") or "[]")
        track["candidates"] = candidates
    return tracks


@app.post(
    "/api/v1/tracks/{track_id}/approve",
    dependencies=[Depends(require_scope("review:write"))],
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


@app.post("/api/v1/tracks/{track_id}/reject", dependencies=[Depends(require_scope("review:write"))])
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


@app.post(
    "/api/v1/tracks/{track_id}/undo-reject",
    dependencies=[Depends(require_scope("review:write"))],
)
async def undo_reject_candidate(
    track_id: int,
    decision: CandidateDecision,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
):
    try:
        JobProcessor(settings, db).undo_reject(track_id, decision.candidate_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "restored"}


@app.get("/api/v1/jobs", dependencies=[Depends(require_scope("read"))])
async def list_jobs(limit: int = 100, db: Database = Depends(get_db)):
    return db.query("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),))


@app.post(
    "/api/v1/jobs/{job_id}/retry",
    dependencies=[Depends(require_scope("ops:write"))],
    status_code=202,
)
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


@app.post("/api/v1/jobs/{job_id}/acknowledge", dependencies=[Depends(require_scope("ops:write"))])
async def acknowledge_failed_job(job_id: int, db: Database = Depends(get_db)):
    """Retain a failed job for audit history without keeping it operationally open."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT run_id FROM jobs WHERE id=? AND status='failed'", (job_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="Only failed jobs can be acknowledged")
        conn.execute("UPDATE jobs SET status='acknowledged' WHERE id=?", (job_id,))
    db.event(
        "job_acknowledged",
        f"Historical failed job {job_id} acknowledged without retrying it",
        run_id=row["run_id"],
    )
    return {"id": job_id, "status": "acknowledged"}


@app.post("/api/v1/jobs/{job_id}/cancel", dependencies=[Depends(require_scope("ops:write"))])
async def cancel_job(job_id: int, db: Database = Depends(get_db)):
    with db.connect() as conn:
        result = conn.execute(
            "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status='queued'",
            (utcnow(), job_id),
        )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="Only queued jobs can be cancelled")
    return {"id": job_id, "status": "cancelled"}


@app.post(
    "/api/v1/catalog/import",
    dependencies=[Depends(require_scope("ops:write"))],
    status_code=202,
)
async def queue_catalog_import(db: Database = Depends(get_db)):
    active = db.one(
        "SELECT id FROM jobs WHERE kind='catalog_import' AND status IN ('queued','running') LIMIT 1"
    )
    if active:
        raise HTTPException(status_code=409, detail=f"Import job {active['id']} is active")
    job_id = db.enqueue("catalog_import", {})
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/v1/events", dependencies=[Depends(require_scope("read"))])
async def list_events(limit: int = 100, db: Database = Depends(get_db)):
    return db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),))


@app.get("/api/v1/tokens", dependencies=[Depends(require_scope("admin:tokens"))])
async def list_api_tokens(db: Database = Depends(get_db)):
    rows = db.query(
        """SELECT id, name, token_prefix, scopes_json, created_at, last_used_at, revoked_at
           FROM api_tokens ORDER BY id DESC"""
    )
    for row in rows:
        row["scopes"] = json.loads(row.pop("scopes_json"))
    return rows


@app.post("/api/v1/tokens", dependencies=[Depends(require_scope("admin:tokens"))], status_code=201)
async def create_api_token(payload: ApiTokenRequest, db: Database = Depends(get_db)):
    name = payload.name.strip()
    if not 1 <= len(name) <= 80:
        raise HTTPException(status_code=422, detail="Token name must be 1–80 characters")
    if not payload.scopes or not payload.scopes <= ALL_SCOPES:
        raise HTTPException(status_code=422, detail="One or more token scopes are invalid")
    token, secret = issue_api_token(db, name, payload.scopes)
    return {"token": token, "secret": secret}


@app.delete("/api/v1/tokens/{token_id}", dependencies=[Depends(require_scope("admin:tokens"))])
async def revoke_api_token(token_id: int, db: Database = Depends(get_db)):
    with db.connect() as conn:
        result = conn.execute(
            "UPDATE api_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (utcnow(), token_id),
        )
    if result.rowcount != 1:
        raise HTTPException(status_code=404, detail="Active API token not found")
    return {"id": token_id, "status": "revoked"}


@app.get("/api/v1/plex-playlist-preferences", dependencies=[Depends(require_scope("admin:tokens"))])
async def get_plex_playlist_preferences(db: Database = Depends(get_db)):
    return db.plex_playlist_preferences()


@app.put("/api/v1/plex-playlist-preferences", dependencies=[Depends(require_scope("admin:tokens"))])
async def update_plex_playlist_preferences(
    payload: PlexPlaylistPreferencesRequest,
    db: Database = Depends(get_db),
):
    if not (payload.include_top_18 or payload.include_new or payload.include_older):
        raise HTTPException(status_code=422, detail="Select at least one playlist type")
    return db.save_plex_playlist_preferences(payload.model_dump())


def selected_plex_playlist_plans(db: Database, settings: Settings):
    library = db.one(
        "SELECT plex_section_key FROM plex_libraries WHERE title=?",
        (settings.plex_library_title,),
    )
    if library is None:
        return None, [], db.plex_playlist_preferences(), None
    today = datetime.now(ZoneInfo("America/New_York")).date()
    cutoff = today.replace(year=today.year - 2)
    plans, unresolved = build_playlist_plans(
        db,
        section_key=library["plex_section_key"],
        cutoff=cutoff,
    )
    preferences = db.plex_playlist_preferences()
    selected = [
        plan
        for plan, enabled in zip(
            plans,
            (
                preferences["include_top_18"],
                preferences["include_new"],
                preferences["include_older"],
            ),
            strict=True,
        )
        if enabled
    ]
    return library, selected, preferences, {"cutoff": cutoff.isoformat(), "unresolved": unresolved}


@app.get("/api/v1/plex-status", dependencies=[Depends(require_scope("read"))])
async def plex_status(settings: Settings = Depends(get_settings), db: Database = Depends(get_db)):
    library, plans, preferences, plan_data = selected_plex_playlist_plans(db, settings)
    if library is None:
        return {
            "configured": bool(settings.plex_url and settings.plex_token),
            "snapshot": None,
            "preferences": preferences,
            "playlists": [],
        }
    snapshot = db.one(
        """SELECT l.title, l.last_synced_at, COUNT(m.plex_rating_key) AS media_count,
                  SUM(m.originally_available_at >= ?) AS new_count,
                  SUM(
                      m.originally_available_at < ? OR m.originally_available_at IS NULL
                  ) AS older_count,
                  SUM(m.originally_available_at IS NULL) AS missing_date_count
           FROM plex_libraries l LEFT JOIN plex_media m ON m.plex_section_key=l.plex_section_key
           WHERE l.plex_section_key=? GROUP BY l.plex_section_key""",
        (plan_data["cutoff"], plan_data["cutoff"], library["plex_section_key"]),
    )
    return {
        "configured": bool(settings.plex_url and settings.plex_token),
        "snapshot": snapshot,
        "cutoff": plan_data["cutoff"],
        "preferences": preferences,
        "unresolved_top_18": plan_data["unresolved"],
        "snapshot_needs_refresh": bool(
            snapshot
            and db.one(
                """SELECT 1 AS newer_publish FROM tracks
                   WHERE status='published' AND updated_at > ? LIMIT 1""",
                (snapshot["last_synced_at"],),
            )
        ),
        "playlists": [
            {"title": plan.title, "target_count": len(plan.rating_keys)} for plan in plans
        ],
    }


@app.post("/api/v1/plex-status/snapshot", dependencies=[Depends(require_scope("ops:write"))])
async def refresh_plex_snapshot(
    settings: Settings = Depends(get_settings), db: Database = Depends(get_db)
):
    """Refresh MVG's local Plex snapshot using Plex GET requests only."""
    if not settings.plex_url or not settings.plex_token:
        raise HTTPException(status_code=503, detail="Plex is not configured")
    try:
        library, media = PlexReadOnlyClient(settings).snapshot_library()
        result = db.save_plex_library_snapshot(library, media)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Plex snapshot refresh failed: {exc}") from exc
    return {"read_only": True, "snapshot": result}


@app.get("/api/v1/plex-status/live", dependencies=[Depends(require_scope("read"))])
async def live_plex_status(
    settings: Settings = Depends(get_settings), db: Database = Depends(get_db)
):
    if not settings.plex_url or not settings.plex_token:
        raise HTTPException(status_code=503, detail="Plex is not configured")
    library, plans, _preferences, plan_data = selected_plex_playlist_plans(db, settings)
    if library is None:
        raise HTTPException(status_code=409, detail="No local Plex snapshot exists")
    skipped = []
    comparable_plans = plans
    if plan_data["unresolved"]:
        comparable_plans = [
            plan for plan in plans if plan.title != "Alt Nation — Latest Top 18"
        ]
        if len(comparable_plans) != len(plans):
            skipped.append(
                {
                    "title": "Alt Nation — Latest Top 18",
                    "status": "unresolved",
                    "unresolved": plan_data["unresolved"],
                }
            )
    try:
        refresh = PlexPlaylistClient(settings.plex_url, settings.plex_token).playlist_refresh_plan(
            comparable_plans
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Plex status check failed: {exc}") from exc
    return {
        "read_only": True,
        "refresh": [*skipped, *refresh],
        "unresolved_top_18": plan_data["unresolved"],
    }
