from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

import httpx

from .config import Settings


class PlexReadOnlyClient:
    """Small Plex client restricted to GET requests used for metadata snapshots."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.plex_url or not settings.plex_token:
            raise ValueError("MVG_PLEX_URL and MVG_PLEX_TOKEN must be configured")
        self.base_url = settings.plex_url.rstrip("/")
        self.library_title = settings.plex_library_title
        self.client = client or httpx.Client(timeout=30.0)
        self.headers = {
            "Accept": "application/xml",
            "X-Plex-Token": settings.plex_token,
            "X-Plex-Container-Start": "0",
            "X-Plex-Container-Size": "10000",
        }

    def _get_xml(self, path: str) -> ElementTree.Element:
        response = self.client.get(f"{self.base_url}{path}", headers=self.headers)
        response.raise_for_status()
        return ElementTree.fromstring(response.content)

    def snapshot_library(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        sections = self._get_xml("/library/sections")
        section = next(
            (
                item
                for item in sections.findall("Directory")
                if item.get("title") == self.library_title
            ),
            None,
        )
        if section is None:
            raise LookupError(f"Plex library {self.library_title!r} was not found")
        section_key = section.get("key")
        if not section_key:
            raise RuntimeError("Plex library did not include a section key")
        library = {
            "section_key": section_key,
            "title": section.get("title", ""),
            "library_type": section.get("type", ""),
            "scanner": section.get("scanner"),
            "locations": [location.get("path", "") for location in section.findall("Location")],
        }
        container = self._get_xml(f"/library/sections/{section_key}/all?type=1")
        media = [
            self._parse_video(video)
            for video in container.findall("Video")
            if video.get("ratingKey")
        ]
        return library, media

    @staticmethod
    def _parse_video(video: ElementTree.Element) -> dict[str, Any]:
        part = video.find("./Media/Part")
        return {
            "rating_key": video.get("ratingKey", ""),
            "title": video.get("title", ""),
            "originally_available_at": video.get("originallyAvailableAt"),
            "year": int(video.get("year", "")) if video.get("year", "").isdigit() else None,
            "media_path": part.get("file") if part is not None else None,
            "metadata": {
                key: value
                for key, value in video.attrib.items()
                if key in {"guid", "addedAt", "updatedAt", "duration", "contentRating"}
            },
        }
