from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from .config import get_settings
from .db import database_from_settings
from .importer import import_legacy_json, import_media_directory
from .logging_config import configure_logging
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
    args = parser.parse_args()

    if args.command == "worker":
        run_worker(once=args.once)
    elif args.command == "import-catalog":
        import_catalog(args.legacy)
    elif args.command == "backup-db":
        backup_database(args.destination)


if __name__ == "__main__":
    main()
