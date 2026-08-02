from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
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

    def latest(self, station: str, limit: int) -> list[StationTrack]:
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
        cookie = self.settings.youtube_cookie_file
        if cookie and cookie.exists() and cookie.stat().st_size:
            options["cookiefile"] = str(cookie)
        query = f"ytsearch{self.settings.max_results}:{artist} {title} official music video"
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


def parse_played_at(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except ValueError:
        return value
