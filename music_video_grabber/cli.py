from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import get_settings
from .db import database_from_settings
from .importer import import_legacy_json, import_media_directory
from .logging_config import configure_logging
from .plex import PlexReadOnlyClient
from .plex_playlists import PlexPlaylistClient, build_playlist_plans
from .worker import run_worker


def import_catalog(legacy: Path | None) -> None:
    settings = get_settings()
    db = database_from_settings(settings)
    db.initialize()
    media_result = import_media_directory(db, settings.media_dir)
    print(f"NAS scan: {media_result}")
    if legacy:
        legacy_result = import_legacy_json(db, legacy)
        print(f"Legacy JSON: {legacy_result}")


def backup_database(destination: Path) -> None:
    settings = get_settings()
    source = sqlite3.connect(settings.database_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    print(destination)


def sync_plex_metadata() -> None:
    """Fetch Plex metadata with GET requests and persist it only in SQLite."""
    settings = get_settings()
    db = database_from_settings(settings)
    db.initialize()
    library, media = PlexReadOnlyClient(settings).snapshot_library()
    result = db.save_plex_library_snapshot(library, media)
    print(f"Plex metadata snapshot: {result}")


def sync_plex_playlists(*, apply: bool) -> None:
    """Print a static playlist plan, or explicitly create its Plex playlists."""
    settings = get_settings()
    db = database_from_settings(settings)
    db.initialize()
    library = db.one(
        "SELECT plex_section_key FROM plex_libraries WHERE title=?", (settings.plex_library_title,)
    )
    if library is None:
        raise RuntimeError("No local Plex snapshot exists; run sync-plex-metadata first")
    # The scheduled chart run and library-review workflow use Eastern time.
    # Use a calendar two-year boundary, rather than 730 days, so leap years do
    # not unexpectedly move items between these static playlists.
    today = datetime.now(ZoneInfo("America/New_York")).date()
    cutoff = today.replace(year=today.year - 2)
    plans, unresolved = build_playlist_plans(
        db, section_key=library["plex_section_key"], cutoff=cutoff
    )
    output = {
        "cutoff": cutoff.isoformat(),
        "playlists": [
            {"title": plan.title, "rating_keys": list(plan.rating_keys)} for plan in plans
        ],
        "unresolved_top_18": unresolved,
    }
    print(json.dumps(output, indent=2))
    if not apply:
        return
    if unresolved:
        raise RuntimeError("Refusing Plex writes while Top 18 membership is unresolved")
    client = PlexPlaylistClient(settings.plex_url, settings.plex_token)
    existing = client.existing_playlists()
    collisions = [plan.title for plan in plans if plan.title in existing]
    if collisions:
        raise RuntimeError(f"Refusing to alter existing Plex playlists: {', '.join(collisions)}")
    created: list[dict[str, str]] = []
    try:
        for plan in plans:
            created.append({"title": plan.title, "rating_key": client.create_video_playlist(plan)})
    except Exception:
        for item in reversed(created):
            client.delete_playlist(item["rating_key"])
        raise
    print(json.dumps({"created": created}, indent=2))


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(prog="music-video-grabber")
    subparsers = parser.add_subparsers(dest="command", required=True)
    worker = subparsers.add_parser("worker", help="Run the durable job worker")
    worker.add_argument("--once", action="store_true")
    importer = subparsers.add_parser("import-catalog", help="Import NAS and legacy history")
    importer.add_argument("--legacy", type=Path)
    backup = subparsers.add_parser("backup-db", help="Create an online SQLite backup")
    backup.add_argument("destination", type=Path)
    subparsers.add_parser(
        "sync-plex-metadata", help="Read Plex metadata and persist a local SQLite snapshot"
    )
    playlist = subparsers.add_parser(
        "sync-plex-playlists", help="Print or explicitly create the planned static Plex playlists"
    )
    playlist.add_argument(
        "--apply", action="store_true", help="Create playlists after safety checks"
    )
    args = parser.parse_args()

    if args.command == "worker":
        run_worker(once=args.once)
    elif args.command == "import-catalog":
        import_catalog(args.legacy)
    elif args.command == "backup-db":
        backup_database(args.destination)
    elif args.command == "sync-plex-metadata":
        sync_plex_metadata()
    elif args.command == "sync-plex-playlists":
        sync_plex_playlists(apply=args.apply)


if __name__ == "__main__":
    main()
