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


def test_parse_media_filename_strips_upload_tags():
    cases = {
        "Malcolm Todd, Omar Apollo - Bleed (Official Video) [Song].mp4": "Bleed",
        "Sam Fender - Little Bit Closer (Official Lyric Video) [Song].mp4": "Little Bit Closer",
        "MGMT - Time to Pretend (Official HD Video) [2007].mp4": "Time to Pretend",
        "half•alive - Automatic (Official).mp4": "Automatic",
        "Pearl Jam - Wreckage (Official Visualizer) [2024].mp4": "Wreckage",
        "bby - Kinky [visualiser] [Song].mp4": "Kinky",
    }
    for name, title in cases.items():
        assert parse_media_filename(Path(name))[1] == title, name


def test_parse_media_filename_keeps_meaningful_brackets():
    # Version, cover and live markers distinguish real recordings; keep them.
    assert parse_media_filename(Path("Royel Otis - white flag (dido cover) [Song].mp4"))[1] == (
        "white flag (dido cover)"
    )
    assert parse_media_filename(Path("Band - Song (Live) [2024].mp4"))[1] == "Song (Live)"
    assert parse_media_filename(Path("Band - Song (Acoustic).mp4"))[1] == "Song (Acoustic)"


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
