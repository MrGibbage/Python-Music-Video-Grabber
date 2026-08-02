from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .db import Database, utcnow
from .text import canonical_track_key

logger = logging.getLogger(__name__)

_YEAR = re.compile(r"[\[(]((?:19|20)\d{2})[\])]")
_DESCRIPTORS = re.compile(
    r"\s*[\[(]\s*(?:official\s*(?:music\s*)?(?:video|audio)?|music\s*video|"
    r"lyric\s*video|audio|visuali[sz]er|hd|4k)\s*[\])]",
    re.IGNORECASE,
)
_SEPARATORS = (" - ", " — ", " – ")


def parse_media_filename(path: Path) -> tuple[str, str, int | None]:
    stem = " ".join(path.stem.replace("：", ":").split())
    year_matches = list(_YEAR.finditer(stem))
    year = int(year_matches[-1].group(1)) if year_matches else None
    if year_matches:
        match = year_matches[-1]
        stem = (stem[: match.start()] + stem[match.end() :]).strip()
    stem = _DESCRIPTORS.sub("", stem).strip(" -")
    for separator in _SEPARATORS:
        if separator in stem:
            artist, title = stem.split(separator, 1)
            return artist.strip() or "Unknown", title.strip() or stem, year
    return "Unknown", stem, year


def _upsert_imported(
    db: Database,
    *,
    artist: str,
    title: str,
    release_year: int | None = None,
    media_path: str | None = None,
    source_track_id: str | None = None,
    source_url: str | None = None,
    imported_from: str,
) -> int:
    key = canonical_track_key(artist, title)
    now = utcnow()
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT id, media_path FROM tracks WHERE canonical_key=?", (key,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE tracks SET
                       source_track_id=COALESCE(source_track_id, ?),
                       release_year=COALESCE(release_year, ?),
                       media_path=COALESCE(?, media_path),
                       source_url=COALESCE(source_url, ?),
                       imported_from=COALESCE(imported_from, ?),
                       status='imported', updated_at=?
                   WHERE id=?""",
                (
                    source_track_id,
                    release_year,
                    media_path,
                    source_url,
                    imported_from,
                    now,
                    existing["id"],
                ),
            )
            return int(existing["id"])
        cursor = conn.execute(
            """INSERT INTO tracks(
                   station, source_track_id, canonical_key, title, artist, release_year,
                   status, media_path, source_url, imported_from, created_at, updated_at
               ) VALUES ('altnation', ?, ?, ?, ?, ?, 'imported', ?, ?, ?, ?, ?)""",
            (
                source_track_id,
                key,
                title,
                artist,
                release_year,
                media_path,
                source_url,
                imported_from,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def import_legacy_json(db: Database, path: Path) -> dict[str, int]:
    if not path.exists():
        return {"read": 0, "imported": 0, "skipped": 0}
    payload = json.loads(path.read_text(encoding="utf-8"))
    songs = payload.get("songs", {})
    imported = skipped = 0
    for source_id, song in songs.items():
        title = song.get("song-title")
        artist = song.get("song-artist")
        if not title or not artist:
            skipped += 1
            continue
        raw_path = song.get("video-filename") or song.get("output_filename")
        media_path = Path(raw_path).name if raw_path else None
        _upsert_imported(
            db,
            artist=artist,
            title=title,
            media_path=media_path,
            source_track_id=str(source_id),
            source_url=song.get("video-url"),
            imported_from=f"legacy-json:{path.name}",
        )
        imported += 1
    logger.info("Legacy JSON import complete: %s imported, %s skipped", imported, skipped)
    return {"read": len(songs), "imported": imported, "skipped": skipped}


def import_media_directory(db: Database, media_dir: Path) -> dict[str, int]:
    imported = skipped = 0
    for path in sorted(media_dir.glob("*.mp4")):
        if path.name.startswith(".") or path.name.startswith("__"):
            skipped += 1
            continue
        artist, title, year = parse_media_filename(path)
        _upsert_imported(
            db,
            artist=artist,
            title=title,
            release_year=year,
            media_path=path.name,
            imported_from="nas-scan",
        )
        imported += 1
    logger.info("NAS catalog import complete: %s imported, %s skipped", imported, skipped)
    return {"read": imported + skipped, "imported": imported, "skipped": skipped}
