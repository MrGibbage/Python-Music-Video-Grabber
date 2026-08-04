"""Explicit, narrowly scoped support for creating Plex video playlists.

The snapshot client in :mod:`music_video_grabber.plex` remains GET-only.  This
module is deliberately separate because its methods make Plex changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from .db import Database
from .importer import parse_media_filename
from .text import canonical_track_key


@dataclass(frozen=True)
class PlaylistPlan:
    title: str
    rating_keys: tuple[str, ...]


def _path_name(value: str | None) -> str | None:
    if not value:
        return None
    return Path(value.replace("\\", "/")).name


def _media_keys_by_filename(db: Database, section_key: str) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for row in db.query(
        "SELECT plex_rating_key, media_path FROM plex_media WHERE plex_section_key=?",
        (section_key,),
    ):
        name = _path_name(row["media_path"])
        if name:
            keys.setdefault(name.casefold(), []).append(row["plex_rating_key"])
    return keys


def _media_keys_by_canonical_name(db: Database, section_key: str) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for row in db.query(
        "SELECT plex_rating_key, media_path FROM plex_media WHERE plex_section_key=?",
        (section_key,),
    ):
        name = _path_name(row["media_path"])
        if not name:
            continue
        artist, title, _ = parse_media_filename(Path(name))
        if artist == "Unknown":
            continue
        keys.setdefault(canonical_track_key(artist, title), []).append(row["plex_rating_key"])
    return keys


def build_playlist_plans(
    db: Database,
    *,
    section_key: str,
    cutoff: date | None = None,
) -> tuple[list[PlaylistPlan], list[dict[str, Any]]]:
    """Build static playlist membership from the local Plex snapshot.

    The current Alt Nation chart is matched conservatively: an item is included
    only when its exact media filename or its parsed artist/title maps to one
    Plex rating key.  Ambiguous or absent items are returned as unresolved.
    """
    cutoff = cutoff or (date.today() - timedelta(days=365 * 2))
    cutoff_text = cutoff.isoformat()
    newer = db.query(
        """SELECT plex_rating_key FROM plex_media
           WHERE plex_section_key=? AND originally_available_at >= ?
           ORDER BY originally_available_at DESC, title COLLATE NOCASE, plex_rating_key""",
        (section_key, cutoff_text),
    )
    older = db.query(
        """SELECT plex_rating_key FROM plex_media
           WHERE plex_section_key=?
             AND (originally_available_at < ? OR originally_available_at IS NULL)
           ORDER BY originally_available_at, title COLLATE NOCASE, plex_rating_key""",
        (section_key, cutoff_text),
    )

    exact_names = _media_keys_by_filename(db, section_key)
    canonical_names = _media_keys_by_canonical_name(db, section_key)
    chart = db.query(
        """SELECT ce.rank, ce.artist, ce.title, t.media_path
           FROM chart_entries ce
           JOIN charts c ON c.id=ce.chart_id
           LEFT JOIN tracks t ON t.station=c.station AND t.source_track_id=ce.source_track_id
           WHERE c.station='altnation'
           ORDER BY c.as_of DESC, c.id DESC, ce.rank ASC
           LIMIT 18"""
    )
    top_keys: list[str] = []
    unresolved: list[dict[str, Any]] = []
    for row in chart:
        exact = _path_name(row["media_path"])
        matches = exact_names.get(exact.casefold(), []) if exact else []
        if not matches:
            matches = canonical_names.get(canonical_track_key(row["artist"], row["title"]), [])
        if len(matches) == 1:
            top_keys.append(matches[0])
        else:
            unresolved.append(
                {
                    "rank": row["rank"],
                    "artist": row["artist"],
                    "title": row["title"],
                    "reason": "not found" if not matches else "ambiguous Plex match",
                }
            )

    plans = [
        PlaylistPlan("Alt Nation — Latest Top 18", tuple(top_keys)),
        PlaylistPlan(
            "Music Videos — New (Last 2 Years)", tuple(row["plex_rating_key"] for row in newer)
        ),
        PlaylistPlan(
            "Music Videos — Older (2+ Years)", tuple(row["plex_rating_key"] for row in older)
        ),
    ]
    return plans, unresolved


class PlexPlaylistClient:
    """Plex write client used only after an operator approves a printed plan."""

    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30.0)
        self.headers = {"Accept": "application/xml", "X-Plex-Token": token}

    def existing_playlists(self) -> dict[str, str]:
        response = self.client.get(f"{self.base_url}/playlists", headers=self.headers)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        return {
            item.get("title", ""): item.get("ratingKey", "")
            for item in root.findall("Playlist")
            if item.get("title") and item.get("ratingKey")
        }

    def create_video_playlist(self, plan: PlaylistPlan) -> str:
        """Create one static video playlist and return its Plex rating key."""
        if not plan.rating_keys:
            raise ValueError(f"Refusing to create empty playlist {plan.title!r}")
        identity = self.client.get(f"{self.base_url}/", headers=self.headers)
        identity.raise_for_status()
        machine_id = ElementTree.fromstring(identity.content).get("machineIdentifier")
        if not machine_id:
            raise RuntimeError("Plex did not return a machine identifier")
        uri = f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/" + ",".join(
            plan.rating_keys
        )
        response = self.client.post(
            f"{self.base_url}/playlists",
            params={"type": "video", "title": plan.title, "smart": "0", "uri": uri},
            headers=self.headers,
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        playlist = root.find("Playlist")
        rating_key = playlist.get("ratingKey") if playlist is not None else None
        if not rating_key:
            raise RuntimeError("Plex created a playlist but did not return its rating key")
        return rating_key

    def playlist_item_keys(self, rating_key: str) -> tuple[str, ...]:
        """Return playlist membership using a GET-only request."""
        response = self.client.get(
            f"{self.base_url}/playlists/{quote(rating_key, safe='')}/items",
            headers=self.headers,
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        return tuple(
            item.get("ratingKey", "") for item in root.findall("Video") if item.get("ratingKey")
        )

    def playlist_refresh_plan(self, plans: list[PlaylistPlan]) -> list[dict[str, Any]]:
        """Compare target membership with Plex, without changing Plex."""
        existing = self.existing_playlists()
        result: list[dict[str, Any]] = []
        for plan in plans:
            rating_key = existing.get(plan.title)
            current = self.playlist_item_keys(rating_key) if rating_key else ()
            wanted = set(plan.rating_keys)
            actual = set(current)
            result.append(
                {
                    "title": plan.title,
                    "playlist_rating_key": rating_key,
                    "current_count": len(current),
                    "target_count": len(plan.rating_keys),
                    "add_rating_keys": [key for key in plan.rating_keys if key not in actual],
                    "remove_rating_keys": [key for key in current if key not in wanted],
                    "missing_playlist": rating_key is None,
                }
            )
        return result

    def delete_playlist(self, rating_key: str) -> None:
        response = self.client.delete(
            f"{self.base_url}/playlists/{quote(rating_key, safe='')}", headers=self.headers
        )
        response.raise_for_status()
