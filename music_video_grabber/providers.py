from __future__ import annotations

import json
import logging
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cloudscraper
import httpx
import yt_dlp

from .config import Settings
from .scoring import CandidateInput

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StationTrack:
    source_track_id: str
    title: str
    artist: str
    played_at: str | None


class XmPlaylistClient:
    base_url = "https://xmplaylist.com/api"

    def __init__(self, fixture_path: Path | None = None):
        self.fixture_path = fixture_path

    def latest(self, station: str, limit: int) -> list[StationTrack]:
        if self.fixture_path:
            results = json.loads(self.fixture_path.read_text(encoding="utf-8")).get("results", [])
            logger.info("Using xmplaylist fixture", extra={"event": "xmplaylist_fixture"})
        else:
            scraper = cloudscraper.create_scraper()
            response = scraper.get(f"{self.base_url}/station/{station}", timeout=30)
            response.raise_for_status()
            results = response.json().get("results", [])
        tracks: list[StationTrack] = []
        for item in results[:limit]:
            track = item.get("track") or {}
            artists = track.get("artists") or []
            source_id = track.get("id")
            title = track.get("title")
            if not source_id or not title or not artists:
                logger.warning("Skipping malformed xmplaylist result")
                continue
            tracks.append(
                StationTrack(
                    source_track_id=str(source_id),
                    title=str(title),
                    artist=" ".join(str(artist) for artist in artists),
                    played_at=item.get("timestamp"),
                )
            )
        return tracks


class SpotifyClient:
    def __init__(self, settings: Settings):
        self.client_id = settings.spotify_client_id
        self.client_secret = settings.spotify_client_secret

    def release_year(self, title: str, artist: str) -> int | None:
        if not self.client_id or not self.client_secret:
            return None
        with httpx.Client(timeout=20) as client:
            token_response = client.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
            )
            token_response.raise_for_status()
            token = token_response.json()["access_token"]
            response = client.get(
                "https://api.spotify.com/v1/search",
                headers={"Authorization": f"Bearer {token}"},
                params={"q": f'track:"{title}" artist:"{artist}"', "type": "track", "limit": 1},
            )
            response.raise_for_status()
        items = response.json().get("tracks", {}).get("items", [])
        if not items:
            return None
        date = items[0].get("album", {}).get("release_date", "")
        return int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None


class YouTubeClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def search(self, title: str, artist: str) -> list[CandidateInput]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
        }
        query = f"ytsearch{self.settings.max_results}:{artist} {title} official music video"
        with (
            tempfile.TemporaryDirectory(prefix="mvg-youtube-cookies-") as temporary_directory,
            writable_cookie_copy(
                self.settings.youtube_cookie_file, Path(temporary_directory)
            ) as cookie,
        ):
            if cookie:
                options["cookiefile"] = str(cookie)
            with yt_dlp.YoutubeDL(options) as ydl:
                result = ydl.extract_info(query, download=False)
        candidates = []
        for entry in (result or {}).get("entries") or []:
            video_id = str(entry.get("id") or "")
            if not video_id:
                continue
            candidates.append(
                CandidateInput(
                    video_id=video_id,
                    url=entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
                    title=str(entry.get("title") or ""),
                    uploader=str(entry.get("uploader") or entry.get("channel") or ""),
                    duration=entry.get("duration"),
                    view_count=entry.get("view_count"),
                )
            )
        return candidates


@contextmanager
def writable_cookie_copy(source: Path | None, directory: Path):
    """Give yt-dlp a disposable cookie jar without mutating the mounted secret."""
    if not source or not source.exists() or not source.stat().st_size:
        yield None
        return
    destination = directory / "youtube-cookies.txt"
    shutil.copyfile(source, destination)
    destination.chmod(0o600)
    try:
        yield destination
    finally:
        destination.unlink(missing_ok=True)


def parse_played_at(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except ValueError:
        return value
