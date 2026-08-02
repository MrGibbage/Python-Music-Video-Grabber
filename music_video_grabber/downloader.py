from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx
import yt_dlp

from .config import Settings
from .db import Database, utcnow
from .text import safe_filename

logger = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    pass


class Downloader:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    def download(self, track_id: int, candidate_id: int) -> Path:
        row = self.db.one(
            """SELECT t.*, c.url, c.title AS candidate_title
               FROM tracks t JOIN candidates c ON c.track_id=t.id
               WHERE t.id=? AND c.id=? AND c.rejected=0""",
            (track_id, candidate_id),
        )
        if not row:
            raise DownloadError("Track or candidate was not found")

        year_suffix = f" [{row['release_year']}]" if row["release_year"] else ""
        final_name = safe_filename(f"{row['artist']} - {row['title']}{year_suffix}") + ".mp4"
        final_path = self.settings.media_dir / final_name
        if final_path.exists():
            self._mark_published(track_id, candidate_id, final_path)
            return final_path

        job_staging = self.settings.staging_dir / str(uuid.uuid4())
        job_staging.mkdir(parents=True, exist_ok=False)
        template = str(job_staging / "download.%(ext)s")
        height = self.settings.max_download_height
        options: dict[str, Any] = {
            "format": (
                f"bv*[height<={height}][vcodec^=avc1]+ba[acodec^=mp4a]/"
                f"b[height<={height}][ext=mp4]/bv*[height<={height}]+ba/b"
            ),
            "merge_output_format": "mp4",
            "outtmpl": template,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "retries": 5,
            "fragment_retries": 5,
        }
        cookie = self.settings.youtube_cookie_file
        if cookie and cookie.exists() and cookie.stat().st_size:
            options["cookiefile"] = str(cookie)

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(row["url"], download=True)
            candidates = sorted(job_staging.glob("download.*"))
            media = next((path for path in candidates if path.suffix.lower() == ".mp4"), None)
            if media is None:
                raise DownloadError("yt-dlp did not produce an MP4 file")
            probe = self._probe(media)
            if not any(stream.get("codec_type") == "video" for stream in probe["streams"]):
                raise DownloadError("Downloaded file has no video stream")
            if not any(stream.get("codec_type") == "audio" for stream in probe["streams"]):
                raise DownloadError("Downloaded file has no audio stream")
            self.settings.media_dir.mkdir(parents=True, exist_ok=True)
            os.replace(media, final_path)
            self._write_sidecar(final_path, row, info, probe)
            self._mark_published(track_id, candidate_id, final_path)
            self._trigger_personal_mtv_scan()
            logger.info(
                "Published %s", final_path.name, extra={"event": "download_published", "track_id": track_id}
            )
            return final_path
        finally:
            shutil.rmtree(job_staging, ignore_errors=True)

    @staticmethod
    def _probe(path: Path) -> dict[str, Any]:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return json.loads(result.stdout)

    @staticmethod
    def _write_sidecar(
        final_path: Path, track: dict[str, Any], info: dict[str, Any], probe: dict[str, Any]
    ) -> None:
        sidecar = final_path.with_suffix(".metadata.json")
        payload = {
            "artist": track["artist"],
            "title": track["title"],
            "year": track["release_year"],
            "source_url": track["url"],
            "youtube_title": info.get("title"),
            "youtube_uploader": info.get("uploader"),
            "duration": info.get("duration"),
            "format": probe.get("format", {}),
            "streams": probe.get("streams", []),
        }
        sidecar.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _mark_published(self, track_id: int, candidate_id: int, final_path: Path) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE tracks SET status='published', selected_candidate_id=?, media_path=?,
                   updated_at=? WHERE id=?""",
                (candidate_id, final_path.name, utcnow(), track_id),
            )

    def _trigger_personal_mtv_scan(self) -> None:
        if not self.settings.personal_mtv_scan_url:
            return
        try:
            response = httpx.get(self.settings.personal_mtv_scan_url, timeout=60)
            response.raise_for_status()
        except Exception:
            logger.exception(
                "Personal MTV scan failed after publish", extra={"event": "mtv_scan_failed"}
            )
