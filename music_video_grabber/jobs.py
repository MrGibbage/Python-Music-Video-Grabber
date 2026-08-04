from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from .config import Settings
from .db import Database, utcnow
from .downloader import Downloader
from .importer import import_legacy_json, import_media_directory
from .notifications import Notifier
from .providers import SpotifyClient, StationTrack, XmPlaylistClient, YouTubeClient, parse_played_at
from .scoring import score_candidate, should_auto_approve
from .text import canonical_track_key

logger = logging.getLogger(__name__)


class JobProcessor:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.xm = XmPlaylistClient(settings.xmplaylist_fixture_path)
        self.spotify = SpotifyClient(settings)
        self.youtube = YouTubeClient(settings)
        self.downloader = Downloader(settings, db)
        self.notifier = Notifier(settings)

    def process(self, job: dict[str, Any]) -> None:
        kind = job["kind"]
        if kind == "discover":
            self._discover(job)
        elif kind == "download":
            self._download(job)
        elif kind == "catalog_import":
            self._catalog_import(job)
        else:
            raise ValueError(f"Unknown job kind: {kind}")

    def _catalog_import(self, job: dict[str, Any]) -> None:
        media = import_media_directory(self.db, self.settings.media_dir)
        legacy = import_legacy_json(self.db, self.settings.legacy_json_path)
        self.db.event(
            "catalog_import_complete",
            f"NAS: {media}; legacy: {legacy}",
            run_id=job["run_id"],
        )

    def _discover(self, job: dict[str, Any]) -> None:
        station = job["payload"].get("station", self.settings.station)
        song_count = int(job["payload"].get("song_count", self.settings.top_tracks_limit))
        run_id = job["run_id"]
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE runs SET status='running', started_at=? WHERE id=?",
                (utcnow(), run_id),
            )
        chart = self.db.chart_for_run(run_id)
        if chart:
            tracks = [
                StationTrack(
                    source_track_id=entry["source_track_id"] or "",
                    title=entry["title"],
                    artist=entry["artist"],
                    played_at=entry["played_at"],
                )
                for entry in chart["entries"]
            ]
            logger.info("Reusing persisted chart snapshot", extra={"event": "chart_reused"})
        else:
            tracks = self.xm.latest(station, song_count)
            if len(tracks) != song_count:
                raise RuntimeError(
                    f"xmplaylist returned {len(tracks)} usable tracks; "
                    f"expected {song_count}"
                )
            as_of = parse_played_at(tracks[0].played_at) if tracks else None
            self.db.save_chart(
                run_id=run_id,
                station=station,
                source="xmplaylist_recent_plays",
                as_of=as_of or utcnow(),
                entries=[
                    {
                        "rank": rank,
                        "source_track_id": track.source_track_id,
                        "title": track.title,
                        "artist": track.artist,
                        "played_at": parse_played_at(track.played_at),
                    }
                    for rank, track in enumerate(tracks, start=1)
                ],
            )
        counters: Counter[str] = Counter()
        track_lists: dict[str, list[str]] = {
            "already_owned_tracks": [],
            "auto_approved_tracks": [],
            "review_tracks": [],
        }

        for station_track in tracks:
            label = f"{station_track.artist} — {station_track.title}"
            key = canonical_track_key(station_track.artist, station_track.title)
            existing = self.db.one("SELECT * FROM tracks WHERE canonical_key=?", (key,))
            if existing and existing["status"] in {"imported", "published"}:
                counters["already_owned"] += 1
                track_lists["already_owned_tracks"].append(label)
                self.db.event(
                    "duplicate_skipped",
                    f"Already owned: {station_track.artist} - {station_track.title}",
                    run_id=run_id,
                    track_id=existing["id"],
                )
                continue

            track_id = self._upsert_track(station, station_track)
            year = self.spotify.release_year(station_track.title, station_track.artist)
            if year:
                with self.db.connect() as conn:
                    conn.execute(
                        "UPDATE tracks SET release_year=?, updated_at=? WHERE id=?",
                        (year, utcnow(), track_id),
                    )

            candidates = self.youtube.search(station_track.title, station_track.artist)
            scored: list[tuple[int, float]] = []
            with self.db.connect() as conn:
                conn.execute("DELETE FROM candidates WHERE track_id=?", (track_id,))
                for candidate in candidates:
                    result = score_candidate(
                        station_track.title, station_track.artist, candidate
                    )
                    cursor = conn.execute(
                        """INSERT INTO candidates(
                               track_id, video_id, url, title, uploader, duration,
                               view_count, score, reasons_json, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            track_id,
                            candidate.video_id,
                            candidate.url,
                            candidate.title,
                            candidate.uploader,
                            candidate.duration,
                            candidate.view_count,
                            result.score,
                            json.dumps(result.reasons),
                            utcnow(),
                        ),
                    )
                    scored.append((int(cursor.lastrowid), result.score))
            scored.sort(key=lambda item: item[1], reverse=True)

            if should_auto_approve(
                [score for _, score in scored],
                threshold=self.settings.auto_approve_score,
                minimum_margin=self.settings.auto_approve_margin,
            ):
                candidate_id = scored[0][0]
                self._queue_download(track_id, candidate_id, run_id)
                counters["auto_approved"] += 1
                track_lists["auto_approved_tracks"].append(label)
            else:
                with self.db.connect() as conn:
                    conn.execute(
                        "UPDATE tracks SET status='review', updated_at=? WHERE id=?",
                        (utcnow(), track_id),
                    )
                counters["needs_review"] += 1
                track_lists["review_tracks"].append(label)

        summary = dict(counters)
        summary.update(track_lists)
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE runs SET status='processing', summary_json=? WHERE id=?",
                (json.dumps(summary), run_id),
            )
        self.db.event(
            "discovery_complete",
            f"Found {len(tracks)} tracks: {dict(counters)}",
            run_id=run_id,
        )

    def _upsert_track(self, station: str, station_track: Any) -> int:
        key = canonical_track_key(station_track.artist, station_track.title)
        now = utcnow()
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM tracks WHERE canonical_key=?", (key,)).fetchone()
            if row:
                conn.execute(
                    """UPDATE tracks SET station=?, source_track_id=?, played_at=?,
                       status='matching', updated_at=? WHERE id=?""",
                    (
                        station,
                        station_track.source_track_id,
                        parse_played_at(station_track.played_at),
                        now,
                        row["id"],
                    ),
                )
                return int(row["id"])
            cursor = conn.execute(
                """INSERT INTO tracks(
                       station, source_track_id, canonical_key, title, artist, played_at,
                       status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'matching', ?, ?)""",
                (
                    station,
                    station_track.source_track_id,
                    key,
                    station_track.title,
                    station_track.artist,
                    parse_played_at(station_track.played_at),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def _queue_download(self, track_id: int, candidate_id: int, run_id: int | None) -> int:
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE tracks SET status='queued', selected_candidate_id=?, updated_at=?
                   WHERE id=?""",
                (candidate_id, utcnow(), track_id),
            )
        return self.db.enqueue(
            "download",
            {"track_id": track_id, "candidate_id": candidate_id},
            run_id=run_id,
        )

    def _download(self, job: dict[str, Any]) -> None:
        track_id = int(job["payload"]["track_id"])
        candidate_id = int(job["payload"]["candidate_id"])
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE tracks SET status='downloading', updated_at=? WHERE id=?",
                (utcnow(), track_id),
            )
        path = self.downloader.download(track_id, candidate_id)
        self.db.event(
            "download_complete",
            f"Published {path.name}",
            run_id=job["run_id"],
            track_id=track_id,
        )

    def approve(self, track_id: int, candidate_id: int) -> int:
        track = self.db.one("SELECT id FROM tracks WHERE id=?", (track_id,))
        candidate = self.db.one(
            "SELECT id FROM candidates WHERE id=? AND track_id=? AND rejected=0",
            (candidate_id, track_id),
        )
        if not track or not candidate:
            raise LookupError("Track or candidate not found")
        return self._queue_download(track_id, candidate_id, None)

    def reject(self, track_id: int, candidate_id: int) -> None:
        with self.db.connect() as conn:
            result = conn.execute(
                "UPDATE candidates SET rejected=1 WHERE id=? AND track_id=?",
                (candidate_id, track_id),
            )
            if result.rowcount != 1:
                raise LookupError("Track or candidate not found")

    def undo_reject(self, track_id: int, candidate_id: int) -> None:
        with self.db.connect() as conn:
            result = conn.execute(
                "UPDATE candidates SET rejected=0 WHERE id=? AND track_id=? AND rejected=1",
                (candidate_id, track_id),
            )
            if result.rowcount != 1:
                raise LookupError("Rejected candidate not found")

    def maybe_finalize_run(self, run_id: int | None) -> None:
        if run_id is None:
            return
        active = self.db.one(
            """SELECT COUNT(*) AS count FROM jobs
               WHERE run_id=? AND status IN ('queued', 'running')""",
            (run_id,),
        )
        if active and active["count"]:
            return
        failed = self.db.one(
            "SELECT COUNT(*) AS count FROM jobs WHERE run_id=? AND status='failed'", (run_id,)
        )
        review = self.db.one(
            """SELECT COUNT(*) AS count FROM tracks t
               WHERE t.status='review' AND t.updated_at >=
                   (SELECT requested_at FROM runs WHERE id=?)""",
            (run_id,),
        )
        if failed and failed["count"]:
            status = "failed"
        elif review and review["count"]:
            status = "needs_review"
        else:
            status = "succeeded"
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE runs SET status=?, finished_at=? WHERE id=?",
                (status, utcnow(), run_id),
            )
        run = self.db.one("SELECT * FROM runs WHERE id=?", (run_id,)) or {}
        summary = json.loads(run.get("summary_json") or "{}")
        downloaded = self.db.query(
            """SELECT t.artist, t.title
               FROM jobs j
               JOIN tracks t ON t.id=json_extract(j.payload_json, '$.track_id')
               WHERE j.run_id=? AND j.kind='download' AND j.status='succeeded'
               ORDER BY j.id""",
            (run_id,),
        )
        downloaded_lines = [
            f"- {track['artist']} — {track['title']}" for track in downloaded
        ] or ["- None"]
        notification_sent = self.notifier.send(
            f"{run['station']} music video run: {status}",
            "\n".join(
                [
                    f"Run: {run_id}",
                    f"Status: {status}",
                    f"Already owned: {summary.get('already_owned', 0)}",
                    f"Automatically approved: {summary.get('auto_approved', 0)}",
                    f"Needs review: {summary.get('needs_review', 0)}",
                    f"Failed jobs: {(failed or {}).get('count', 0)}",
                    "",
                    f"Downloaded ({len(downloaded)}):",
                    *downloaded_lines,
                    "Review: https://grabber.pelorus.org/",
                ]
            ),
        )
        self.db.event(
            "notification_sent" if notification_sent else "notification_not_sent",
            "Telegram notification accepted by Mailrise"
            if notification_sent
            else "Notification was disabled or could not be accepted by Mailrise",
            level="info" if notification_sent else "warning",
            run_id=run_id,
        )
