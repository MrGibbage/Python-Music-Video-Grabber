import json
from pathlib import Path

from music_video_grabber.db import Database
from music_video_grabber.importer import (
    import_legacy_json,
    import_media_directory,
    parse_media_filename,
)


def test_parse_media_filename_formats():
    assert parse_media_filename(Path("Artist - Song [2024].mp4")) == ("Artist", "Song", 2024)
    assert parse_media_filename(Path("Artist — Song (Official Video) (1999).mp4")) == (
        "Artist",
        "Song",
        1999,
    )


def test_imports_are_idempotent_and_merge_sources(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    media = tmp_path / "media"
    media.mkdir()
    (media / "Artist - Song [2024].mp4").touch()
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "songs": {
                    "track-1": {
                        "song-title": "Song",
                        "song-artist": "Artist",
                        "video-url": "https://youtube.example/video",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    import_media_directory(db, media)
    import_legacy_json(db, legacy)
    import_media_directory(db, media)

    tracks = db.query("SELECT * FROM tracks")
    assert len(tracks) == 1
    assert tracks[0]["source_track_id"] == "track-1"
    assert tracks[0]["media_path"] == "Artist - Song [2024].mp4"
