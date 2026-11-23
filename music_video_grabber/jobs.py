from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .db import Database, utcnow
from .downloader import Downloader
from .importer import import_legacy_json, import_media_directory
from .notifications import Notifier
from .providers import SpotifyClient, StationTrack, XmPlaylistClient, YouTubeClient, parse_played_at
from .scoring import CandidateInput, score_candidate, should_auto_approve
from .text import canonical_track_key

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredCandidate:
    candidate_id: int
    score: float
    rejected: bool
    is_new: bool


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
        elif kind == "research":
            self._research(job)
        elif kind == "manual_candidate":
            self._manual_candidate(job)
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
            "dismissed_tracks": [],
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

            was_dismissed = bool(existing and existing["status"] == "dismissed")
            candidates = self.youtube.search(station_track.title, station_track.artist)
            scored = self._store_candidates(
                track_id, station_track.title, station_track.artist, candidates
            )
            active = [item for item in scored if not item.rejected]
            fresh = [item for item in active if item.is_new]

            if was_dismissed and not fresh:
                # Dismissed and nothing new on YouTube: stay out of the review queue.
                with self.db.connect() as conn:
                    conn.execute(
                        "UPDATE tracks SET status='dismissed', updated_at=? WHERE id=?",
                        (utcnow(), track_id),
                    )
                counters["dismissed"] += 1
                track_lists["dismissed_tracks"].append(label)
                continue

            if should_auto_approve(
                [item.score for item in active],
                threshold=self.settings.auto_approve_score,
                minimum_margin=self.settings.auto_approve_margin,
            ) and (not was_dismissed or active[0].is_new):
                self._queue_download(track_id, active[0].candidate_id, run_id)
                counters["auto_approved"] += 1
                track_lists["auto_approved_tracks"].append(label)
            else:
                with self.db.connect() as conn:
                    conn.execute(
                        "UPDATE tracks SET status='review', updated_at=? WHERE id=?",
                        (utcnow(), track_id),
                    )
                if was_dismissed:
                    self.db.event(
                        "dismissed_track_resurfaced",
                        f"New YouTube candidates for dismissed track: {label}",
                        run_id=run_id,
                        track_id=track_id,
                    )
                counters["needs_review"] += 1
                track_lists["review_tracks"].append(label)

        summary = dict(counters)
        summary.update(track_lists)
        summary["song_count"] = song_count
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

    def _store_candidates(
        self, track_id: int, title: str, artist: str, candidates: list[CandidateInput]
    ) -> list[StoredCandidate]:
        """Replace a track's search results while keeping the owner's past decisions.

        A video rejected once stays rejected when the song is searched again, and
        ``is_new`` marks videos this track has never been offered before.
        """
        with self.db.connect() as conn:
            previous = {
                row["video_id"]: bool(row["rejected"])
                for row in conn.execute(
                    "SELECT video_id, rejected FROM candidates WHERE track_id=?", (track_id,)
                )
            }
            kept_rejected = [
                video_id for video_id, rejected in previous.items() if rejected
            ]
            conn.execute("DELETE FROM candidates WHERE track_id=?", (track_id,))
            stored: list[StoredCandidate] = []
            seen: set[str] = set()
            for candidate in candidates:
                if candidate.video_id in seen:
                    continue
                seen.add(candidate.video_id)
                result = score_candidate(title, artist, candidate)
                rejected = previous.get(candidate.video_id, False)
                cursor = conn.execute(
                    """INSERT INTO candidates(
                           track_id, video_id, url, title, uploader, duration,
                           view_count, score, reasons_json, rejected, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        int(rejected),
                        utcnow(),
                    ),
                )
                stored.append(
                    StoredCandidate(
                        candidate_id=int(cursor.lastrowid),
                        score=result.score,
                        rejected=rejected,
                        is_new=candidate.video_id not in previous,
                    )
                )
            # Rejected videos that dropped out of this search are kept as tombstones
            # so they are still recognised (and still rejected) if they come back.
            for video_id in kept_rejected:
                if video_id in seen:
                    continue
                conn.execute(
                    """INSERT INTO candidates(
                           track_id, video_id, url, title, score, rejected, created_at
                       ) VALUES (?, ?, ?, '(rejected earlier)', 0, 1, ?)""",
                    (
                        track_id,
                        video_id,
                        f"https://www.youtube.com/watch?v={video_id}",
                        utcnow(),
                    ),
                )
        stored.sort(key=lambda item: item.score, reverse=True)
        return stored

    def _research(self, job: dict[str, Any]) -> None:
        track_id = int(job["payload"]["track_id"])
        track = self.db.one("SELECT * FROM tracks WHERE id=?", (track_id,))
        if not track:
            raise LookupError(f"Track {track_id} not found")
        candidates = self.youtube.search(track["title"], track["artist"])
        stored = self._store_candidates(track_id, track["title"], track["artist"], candidates)
        fresh = [item for item in stored if item.is_new and not item.rejected]
        active = [item for item in stored if not item.rejected]
        # A manual search always lands in review when there is something to pick;
        # a dismissed track with nothing new stays dismissed.
        resurface = fresh or (track["status"] != "dismissed" and active)
        status = "review" if resurface else track["status"]
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE tracks SET status=?, updated_at=? WHERE id=?",
                (status, utcnow(), track_id),
            )
        self.db.event(
            "research_complete",
            f"Searched again for {track['artist']} - {track['title']}: "
            f"{len(fresh)} new candidate(s); status {status}",
            track_id=track_id,
        )

    def _manual_candidate(self, job: dict[str, Any]) -> None:
        track_id = int(job["payload"]["track_id"])
        url = str(job["payload"]["url"])
        track = self.db.one("SELECT * FROM tracks WHERE id=?", (track_id,))
        if not track:
            raise LookupError(f"Track {track_id} not found")
        candidate = self.youtube.lookup(url)
        result = score_candidate(track["title"], track["artist"], candidate)
        reasons = ["chosen manually by URL", *result.reasons]
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO candidates(
                       track_id, video_id, url, title, uploader, duration,
                       view_count, score, reasons_json, rejected, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                   ON CONFLICT(track_id, video_id) DO UPDATE SET
                       rejected=0, url=excluded.url, title=excluded.title,
                       uploader=excluded.uploader, reasons_json=excluded.reasons_json""",
                (
                    track_id,
                    candidate.video_id,
                    candidate.url,
                    candidate.title,
                    candidate.uploader,
                    candidate.duration,
                    candidate.view_count,
                    result.score,
                    json.dumps(reasons),
                    utcnow(),
                ),
            )
            row = conn.execute(
                "SELECT id FROM candidates WHERE track_id=? AND video_id=?",
                (track_id, candidate.video_id),
            ).fetchone()
        self.db.event(
            "manual_candidate_added",
            f"Manual URL for {track['artist']} - {track['title']}: {candidate.title}",
            track_id=track_id,
        )
        self._queue_download(track_id, int(row["id"]), job["run_id"])

    DISMISSABLE = {"review", "matching", "failed"}

    def dismiss(self, track_id: int) -> None:
        track = self.db.one("SELECT status FROM tracks WHERE id=?", (track_id,))
        if not track:
            raise LookupError("Track not found")
        if track["status"] not in self.DISMISSABLE:
            raise ValueError(f"A track in status {track['status']!r} cannot be dismissed")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE tracks SET status='dismissed', updated_at=? WHERE id=?",
                (utcnow(), track_id),
            )
        self.db.event("track_dismissed", "Track dismissed from review", track_id=track_id)

    def restore(self, track_id: int) -> None:
        with self.db.connect() as conn:
            result = conn.execute(
                "UPDATE tracks SET status='review', updated_at=? WHERE id=? AND status='dismissed'",
                (utcnow(), track_id),
            )
            if result.rowcount != 1:
                raise LookupError("Dismissed track not found")
        self.db.event("track_restored", "Dismissed track restored to review", track_id=track_id)

    def queue_research(self, track_id: int) -> int:
        if not self.db.one("SELECT id FROM tracks WHERE id=?", (track_id,)):
            raise LookupError("Track not found")
        return self.db.enqueue("research", {"track_id": track_id})

    def queue_manual_candidate(self, track_id: int, url: str) -> int:
        if not self.db.one("SELECT id FROM tracks WHERE id=?", (track_id,)):
            raise LookupError("Track not found")
        return self.db.enqueue("manual_candidate", {"track_id": track_id, "url": url})

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
            f"{run['station']} · {summary.get('song_count', 'recent')} plays: {status}",
            "\n".join(
                [
                    f"Run: {run_id}",
                    f"Station: {run['station']}",
                    f"Requested recent plays: {summary.get('song_count', 'unknown')}",
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
