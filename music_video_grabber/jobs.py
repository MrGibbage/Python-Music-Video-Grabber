from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .countdown import COUNTDOWN_SIZE, EASTERN, Play, find_countdown, weekend_for
from .db import Database, utcnow
from .downloader import Downloader
from .importer import import_legacy_json, import_media_directory
from .notifications import Notifier
from .providers import SpotifyClient, StationTrack, XmPlaylistClient, YouTubeClient, parse_played_at
from .scoring import CandidateInput, has_video_marker, score_candidate, should_auto_approve
from .text import canonical_track_key

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredCandidate:
    candidate_id: int
    score: float
    rejected: bool
    is_new: bool
    reupload: bool = False


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
        countdown_info: dict[str, Any] | None = None
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
            if job["payload"].get("mode") == "countdown":
                found = self._countdown_tracks(station, run_id)
                if found is None:
                    return  # aborted or already done; maybe_finalize_run reports it
                tracks, countdown_info = found
                source = "xmplaylist_countdown"
                song_count = len(tracks)
            else:
                tracks = self.xm.latest(station, song_count)
                source = "xmplaylist_recent_plays"
            if len(tracks) != song_count:
                raise RuntimeError(
                    f"xmplaylist returned {len(tracks)} usable tracks; "
                    f"expected {song_count}"
                )
            as_of = parse_played_at(tracks[0].played_at) if tracks else None
            self.db.save_chart(
                run_id=run_id,
                station=station,
                source=source,
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
            "not_for_me_tracks": [],
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
            if existing and existing["status"] == "ignored":
                # The owner said "not for me": never search for it again.
                counters["not_for_me"] += 1
                track_lists["not_for_me_tracks"].append(label)
                self.db.event(
                    "not_for_me_skipped",
                    f"Not for me, skipped: {station_track.artist} - {station_track.title}",
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
            candidates = self._check_still_images(
                station_track.title, station_track.artist, candidates
            )
            scored = self._store_candidates(
                track_id, station_track.title, station_track.artist, candidates
            )
            active = [item for item in scored if not item.rejected]
            fresh = self._worth_resurfacing(active)

            if was_dismissed and not fresh:
                # Dismissed and nothing worth a look is new: stay out of the review queue.
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
        if countdown_info:
            summary["countdown"] = countdown_info
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

    def now(self) -> datetime:
        return datetime.now(UTC)

    def _countdown_tracks(
        self, station: str, run_id: int
    ) -> tuple[list[StationTrack], dict[str, Any]] | None:
        """This weekend's countdown, #1 first, confirmed by matching the Saturday
        airing against the most recent replay. None when the run should stop:
        already confirmed this weekend, or the airings didn't line up."""
        now = self.now()
        weekend = weekend_for(now)
        done = self.db.one(
            """SELECT id FROM charts WHERE station=? AND source='xmplaylist_countdown'
               AND as_of >= ?""",
            (station, weekend.starts.astimezone(UTC).isoformat()),
        )
        if done:
            self._end_countdown(
                run_id, "already_confirmed",
                f"This weekend's Top 18 is already confirmed (chart {done['id']}).",
            )
            return None
        original_window = weekend.original_window
        replay_window = weekend.replay_window(now)
        if replay_window is None:
            self._end_countdown(
                run_id, "not_aligned",
                "No replay to compare yet: the Saturday airing hasn't finished.",
                final=False,
            )
            return None
        original = self.xm.history(station, *original_window)
        replay = self.xm.history(station, *replay_window)
        for label, window, plays in (
            ("countdown_original", original_window, original),
            ("countdown_replay", replay_window, replay),
        ):
            self.db.save_play_window(
                run_id=run_id, station=station, label=label,
                start=window[0].isoformat(), end=window[1].isoformat(),
                plays=[track.__dict__ for track in plays],
            )
        match = find_countdown(_as_plays(original), _as_plays(replay))
        replay_text = (
            f"{replay_window[0].astimezone(EASTERN):%a %H:%M}–"
            f"{replay_window[1].astimezone(EASTERN):%H:%M} ET"
        )
        if not match.found:
            self._end_countdown(
                run_id, "not_aligned",
                f"The Saturday airing ({len(original)} plays) and the replay window "
                f"{replay_text} ({len(replay)} plays) share a run of {match.matched} "
                f"songs, not exactly {COUNTDOWN_SIZE}.",
                final=weekend.is_final_attempt(now),
            )
            return None
        tracks = [
            StationTrack(p.source_track_id, p.title, p.artist, p.played_at.isoformat())
            for p in reversed(match.plays)
        ]
        info = {
            "weekend": weekend.saturday.isoformat(),
            "replay_window": replay_text,
            "first_song_et": f"{match.plays[0].played_at.astimezone(EASTERN):%H:%M}",
            "number_one_et": f"{match.plays[-1].played_at.astimezone(EASTERN):%H:%M}",
        }
        self.db.event(
            "countdown_confirmed",
            f"Top 18 confirmed: Saturday {info['first_song_et']}–{info['number_one_et']} ET "
            f"matched the replay in {replay_text}",
            run_id=run_id,
        )
        return tracks, info

    def _end_countdown(self, run_id: int, outcome: str, message: str, final: bool = False):
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE runs SET summary_json=? WHERE id=?",
                (
                    json.dumps(
                        {"countdown_outcome": outcome, "countdown_message": message,
                         "final_attempt": final}
                    ),
                    run_id,
                ),
            )
        self.db.event(
            f"countdown_{outcome}", message,
            level="warning" if outcome == "not_aligned" else "info", run_id=run_id,
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

    def _worth_resurfacing(self, stored: list[StoredCandidate]) -> list[StoredCandidate]:
        """New results good enough to bring a dismissed track back to review.

        YouTube's results shift week to week, so "a video we haven't offered
        before" alone mostly surfaced fan re-uploads and wrong songs (22 of 30
        dismissed tracks came back on 2026-11-24, 3 with anything worth seeing).
        """
        return [
            item for item in stored
            if item.is_new and not item.rejected and not item.reupload
            and item.score >= self.settings.resurface_min_score
        ]

    def _check_still_images(
        self, title: str, artist: str, candidates: list[CandidateInput]
    ) -> list[CandidateInput]:
        """Look at the storyboard of the best few results that don't say "video" in
        their title, since those are the ones that may be album art over audio.

        Each check is a full YouTube lookup, so only the top few are checked.
        """
        limit = self.settings.still_image_checks
        check_still = getattr(self.youtube, "is_still_image", None)
        if not limit or check_still is None:
            return candidates
        unmarked = sorted(
            (c for c in candidates if not has_video_marker(c.title)),
            key=lambda c: score_candidate(title, artist, c).score,
            reverse=True,
        )
        verdicts = {c.video_id: check_still(c.video_id) for c in unmarked[:limit]}
        return [
            replace(c, still_image=verdicts[c.video_id]) if c.video_id in verdicts else c
            for c in candidates
        ]

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
                        reupload=any(r.startswith("re-upload") for r in result.reasons),
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
        if track["status"] == "ignored":
            return  # "Not for me": nothing to look for.
        candidates = self.youtube.search(track["title"], track["artist"])
        candidates = self._check_still_images(track["title"], track["artist"], candidates)
        stored = self._store_candidates(track_id, track["title"], track["artist"], candidates)
        fresh = self._worth_resurfacing(stored)
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

    # "Not for me" can be said from the review queue or the Dismissed list.
    IGNORABLE = DISMISSABLE | {"dismissed"}

    def not_for_me(self, track_id: int) -> None:
        """The owner doesn't like the song: never download or search for it."""
        track = self.db.one("SELECT status FROM tracks WHERE id=?", (track_id,))
        if not track:
            raise LookupError("Track not found")
        if track["status"] not in self.IGNORABLE:
            raise ValueError(f"A track in status {track['status']!r} cannot be set aside")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE tracks SET status='ignored', updated_at=? WHERE id=?",
                (utcnow(), track_id),
            )
        self.db.event("track_not_for_me", "Marked not for me", track_id=track_id)

    def restore(self, track_id: int) -> None:
        """Back to the review queue from Dismissed or Not for me."""
        track = self.db.one("SELECT status FROM tracks WHERE id=?", (track_id,))
        if not track or track["status"] not in {"dismissed", "ignored"}:
            raise LookupError("Dismissed or not-for-me track not found")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE tracks SET status='review', updated_at=? WHERE id=?",
                (utcnow(), track_id),
            )
        label = "Dismissed" if track["status"] == "dismissed" else "Not-for-me"
        self.db.event("track_restored", f"{label} track restored to review", track_id=track_id)

    def queue_research(self, track_id: int) -> int:
        if not self.db.one("SELECT id FROM tracks WHERE id=?", (track_id,)):
            raise LookupError("Track not found")
        return self.db.enqueue("research", {"track_id": track_id})

    def queue_manual_candidate(self, track_id: int, url: str) -> int:
        if not self.db.one("SELECT id FROM tracks WHERE id=?", (track_id,)):
            raise LookupError("Track not found")
        return self.db.enqueue("manual_candidate", {"track_id": track_id, "url": url})

    def _finalize_countdown_stop(self, run: dict[str, Any], outcome: str) -> None:
        """A countdown run that stopped before processing: quietly skipped when the
        weekend is already done, otherwise aborted with a Telegram message."""
        summary = json.loads(run.get("summary_json") or "{}")
        status = "skipped" if outcome == "already_confirmed" else "aborted"
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE runs SET status=?, finished_at=? WHERE id=?",
                (status, utcnow(), run["id"]),
            )
        if status == "skipped":
            return
        next_step = (
            "That was the last replay this weekend, so this week's Top 18 was not captured."
            if summary.get("final_attempt")
            else "MVG will try again after the next replay."
        )
        sent = self.notifier.send(
            f"{run['station']} Top 18 not confirmed",
            "\n".join(
                [summary.get("countdown_message", ""), "", next_step, "",
                 f"Run: {run['id']}", "Details: https://grabber.pelorus.org/"]
            ),
        )
        self.db.event(
            "notification_sent" if sent else "notification_not_sent",
            "Countdown abort notification "
            + ("accepted by Mailrise" if sent else "was disabled or not accepted"),
            level="info" if sent else "warning",
            run_id=run["id"],
        )

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
        run = self.db.one("SELECT * FROM runs WHERE id=?", (run_id,)) or {}
        outcome = json.loads(run.get("summary_json") or "{}").get("countdown_outcome")
        if outcome:
            self._finalize_countdown_stop(run, outcome)
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
                    f"Not for me (skipped): {summary.get('not_for_me', 0)}",
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


def _as_plays(tracks: list[StationTrack]) -> list[Play]:
    return [
        Play(
            played_at=datetime.fromisoformat(str(t.played_at).replace("Z", "+00:00")),
            artist=t.artist, title=t.title, source_track_id=t.source_track_id,
        )
        for t in tracks
        if t.played_at
    ]
