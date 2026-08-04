from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    requested_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station TEXT NOT NULL,
    source_track_id TEXT,
    canonical_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    played_at TEXT,
    release_year INTEGER,
    status TEXT NOT NULL DEFAULT 'discovered',
    selected_candidate_id INTEGER,
    media_path TEXT,
    source_url TEXT,
    imported_from TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_source
ON tracks(station, source_track_id) WHERE source_track_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    video_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    uploader TEXT NOT NULL DEFAULT '',
    duration INTEGER,
    view_count INTEGER,
    score REAL NOT NULL,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    rejected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(track_id, video_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_claim
ON jobs(status, available_at, id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
    track_id INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS charts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    station TEXT NOT NULL,
    source TEXT NOT NULL,
    as_of TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chart_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chart_id INTEGER NOT NULL REFERENCES charts(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    source_track_id TEXT,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    played_at TEXT,
    UNIQUE(chart_id, rank)
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_prefix TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE,
    scopes_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_active
ON api_tokens(revoked_at, token_prefix);

CREATE TABLE IF NOT EXISTS plex_libraries (
    plex_section_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    library_type TEXT NOT NULL,
    scanner TEXT,
    locations_json TEXT NOT NULL DEFAULT '[]',
    last_synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plex_media (
    plex_rating_key TEXT PRIMARY KEY,
    plex_section_key TEXT NOT NULL REFERENCES plex_libraries(plex_section_key) ON DELETE CASCADE,
    title TEXT NOT NULL,
    originally_available_at TEXT,
    year INTEGER,
    media_path TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plex_media_section_date
ON plex_media(plex_section_key, originally_available_at);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_run(self, station: str) -> int:
        now = utcnow()
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO runs(station, status, requested_at) VALUES (?, 'queued', ?)",
                (station, now),
            )
            run_id = int(cursor.lastrowid)
            self._enqueue(conn, "discover", {"station": station}, run_id=run_id)
            return run_id

    def save_chart(
        self,
        *,
        run_id: int,
        station: str,
        source: str,
        as_of: str,
        entries: list[dict[str, Any]],
    ) -> int:
        """Persist the exact ordered broadcast snapshot used by an acquisition run."""
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO charts(run_id, station, source, as_of, captured_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, station, source, as_of, utcnow()),
            )
            chart_id = int(cursor.lastrowid)
            conn.executemany(
                """INSERT INTO chart_entries(
                       chart_id, rank, source_track_id, title, artist, played_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        chart_id,
                        entry["rank"],
                        entry.get("source_track_id"),
                        entry["title"],
                        entry["artist"],
                        entry.get("played_at"),
                    )
                    for entry in entries
                ],
            )
        return chart_id

    def latest_chart(self, station: str) -> dict[str, Any] | None:
        chart = self.one(
            "SELECT * FROM charts WHERE station=? ORDER BY captured_at DESC, id DESC LIMIT 1",
            (station,),
        )
        if not chart:
            return None
        chart["entries"] = self.query(
            "SELECT * FROM chart_entries WHERE chart_id=? ORDER BY rank", (chart["id"],)
        )
        return chart

    def chart_for_run(self, run_id: int) -> dict[str, Any] | None:
        chart = self.one("SELECT * FROM charts WHERE run_id=?", (run_id,))
        if not chart:
            return None
        chart["entries"] = self.query(
            "SELECT * FROM chart_entries WHERE chart_id=? ORDER BY rank", (chart["id"],)
        )
        return chart

    def enqueue(
        self, kind: str, payload: dict[str, Any], *, run_id: int | None = None
    ) -> int:
        with self.connect() as conn:
            return self._enqueue(conn, kind, payload, run_id=run_id)

    def _enqueue(
        self,
        conn: sqlite3.Connection,
        kind: str,
        payload: dict[str, Any],
        *,
        run_id: int | None = None,
    ) -> int:
        now = utcnow()
        cursor = conn.execute(
            """INSERT INTO jobs(run_id, kind, payload_json, max_attempts, available_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, kind, json.dumps(payload), 3, now, now),
        )
        return int(cursor.lastrowid)

    def claim_job(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM jobs
                   WHERE status = 'queued' AND available_at <= ?
                   ORDER BY id LIMIT 1""",
                (utcnow(),),
            ).fetchone()
            if row is None:
                return None
            updated = conn.execute(
                """UPDATE jobs SET status='running', attempts=attempts+1, started_at=?
                   WHERE id=? AND status='queued'""",
                (utcnow(), row["id"]),
            )
            if updated.rowcount != 1:
                return None
            result = dict(row)
            result["attempts"] += 1
            result["payload"] = json.loads(result.pop("payload_json"))
            return result

    def finish_job(self, job_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='succeeded', finished_at=?, error=NULL WHERE id=?",
                (utcnow(), job_id),
            )

    def fail_job(self, job: dict[str, Any], error: str) -> None:
        retry = job["attempts"] < job["max_attempts"]
        with self.connect() as conn:
            conn.execute(
                """UPDATE jobs SET status=?, available_at=?, finished_at=?, error=? WHERE id=?""",
                (
                    "queued" if retry else "failed",
                    utcnow(),
                    None if retry else utcnow(),
                    error[:4000],
                    job["id"],
                ),
            )

    def event(
        self,
        event: str,
        message: str,
        *,
        level: str = "info",
        run_id: int | None = None,
        track_id: int | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO events(run_id, track_id, level, event, message, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, track_id, level, event, message, utcnow()),
            )

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def save_plex_library_snapshot(
        self, library: dict[str, Any], media: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Store a read-only Plex metadata snapshot locally.

        This method only writes this application's SQLite database. Plex is
        never contacted here, which keeps persistence separable from access.
        """
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO plex_libraries(
                       plex_section_key, title, library_type, scanner, locations_json,
                       last_synced_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(plex_section_key) DO UPDATE SET
                       title=excluded.title, library_type=excluded.library_type,
                       scanner=excluded.scanner, locations_json=excluded.locations_json,
                       last_synced_at=excluded.last_synced_at""",
                (
                    library["section_key"],
                    library["title"],
                    library["library_type"],
                    library.get("scanner"),
                    json.dumps(library.get("locations", [])),
                    now,
                ),
            )
            conn.executemany(
                """INSERT INTO plex_media(
                       plex_rating_key, plex_section_key, title, originally_available_at, year,
                       media_path, metadata_json, first_seen_at, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(plex_rating_key) DO UPDATE SET
                       plex_section_key=excluded.plex_section_key, title=excluded.title,
                       originally_available_at=excluded.originally_available_at, year=excluded.year,
                       media_path=excluded.media_path, metadata_json=excluded.metadata_json,
                       last_seen_at=excluded.last_seen_at""",
                [
                    (
                        item["rating_key"],
                        library["section_key"],
                        item["title"],
                        item.get("originally_available_at"),
                        item.get("year"),
                        item.get("media_path"),
                        json.dumps(item.get("metadata", {}), sort_keys=True),
                        now,
                        now,
                    )
                    for item in media
                ],
            )
            # A Plex scan is authoritative for the section being refreshed.
            # Remove entries that disappeared from Plex so later playlist plans
            # cannot refer to stale rating keys.  This changes only MVG's local
            # SQLite snapshot; it never sends a request to Plex.
            rating_keys = [item["rating_key"] for item in media]
            if rating_keys:
                placeholders = ", ".join("?" for _ in rating_keys)
                conn.execute(
                    f"DELETE FROM plex_media WHERE plex_section_key=? "
                    f"AND plex_rating_key NOT IN ({placeholders})",
                    (library["section_key"], *rating_keys),
                )
            else:
                conn.execute(
                    "DELETE FROM plex_media WHERE plex_section_key=?",
                    (library["section_key"],),
                )
        return {"libraries": 1, "media": len(media)}


def database_from_settings(settings: Settings) -> Database:
    return Database(settings.database_path)
