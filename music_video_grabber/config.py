from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MVG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Music Video Grabber"
    environment: str = "production"
    api_token: str = ""
    app_password: str = ""
    dashboard_session_secret: str = ""
    dashboard_session_ttl_hours: int = Field(default=24, ge=1, le=24 * 31)
    database_path: Path = Path("data/music-video-grabber.db")
    media_dir: Path = Path("videos")
    staging_dir: Path = Path("staging")
    legacy_json_path: Path = Path("/import/altnation-songs.json")
    youtube_cookie_file: Path | None = None

    station: str = "altnation"
    station_display_name: str = "Alt Nation"
    xmplaylist_fixture_path: Path | None = None
    top_tracks_limit: int = Field(default=18, ge=1, le=100)
    max_results: int = Field(default=8, ge=1, le=25)
    auto_approve_score: float = Field(default=88, ge=0, le=100)
    auto_approve_margin: float = Field(default=8, ge=0, le=100)
    max_download_height: int = Field(default=1080, ge=360, le=4320)

    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    # Plex access is deliberately read-only in this phase. Keep the token in
    # the host env file; it must never be committed or returned by the API.
    plex_url: str = ""
    plex_token: str = ""
    plex_library_title: str = "Music Videos - Alternative"

    mailrise_host: str = ""
    mailrise_port: int = 8025
    notification_to: str = "telegram@mailrise.xyz"
    notification_from: str = "music-video-grabber@pelorus.org"
    personal_mtv_scan_url: str = "http://192.168.0.181:8000/api/scan"

    worker_poll_seconds: float = Field(default=2.0, ge=0.2, le=60)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)

    @property
    def session_signing_secret(self) -> str:
        """Prefer a distinct dashboard secret; otherwise use the private service secret."""
        return self.dashboard_session_secret or self.api_token


@lru_cache
def get_settings() -> Settings:
    return Settings()
