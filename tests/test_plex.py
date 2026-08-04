from datetime import date

import httpx

from music_video_grabber.config import Settings
from music_video_grabber.db import Database
from music_video_grabber.plex import PlexReadOnlyClient
from music_video_grabber.plex_playlists import (
    PlaylistPlan,
    PlexPlaylistClient,
    build_playlist_plans,
)


def test_snapshot_reads_plex_and_persists_locally(tmp_path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/library/sections":
            return httpx.Response(
                200,
                content=(
                    b'<MediaContainer><Directory key="3" title="Music Videos - Alternative" '
                    b'type="movie" scanner="Plex Video Files Scanner"><Location '
                    b'path="/mnt/plex/media/Music Videos - Alternative"/></Directory>'
                    b"</MediaContainer>"
                ),
            )
        assert request.url.path == "/library/sections/3/all"
        return httpx.Response(
            200,
            content=(
                b'<MediaContainer size="1"><Video ratingKey="42" title="Artist - Song [2025]" '
                b'originallyAvailableAt="2025-01-25" year="2025" guid="plex://movie/42">'
                b'<Media><Part file="/mnt/plex/media/Music Videos - Alternative/Artist - Song '
                b'[2025].mp4"/></Media></Video></MediaContainer>'
            ),
        )

    settings = Settings(plex_url="http://plex.example:32400", plex_token="test-token")
    client = PlexReadOnlyClient(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    library, media = client.snapshot_library()

    assert [request.method for request in calls] == ["GET", "GET"]
    assert all(request.headers["X-Plex-Token"] == "test-token" for request in calls)
    db = Database(tmp_path / "app.db")
    db.initialize()
    assert db.save_plex_library_snapshot(library, media) == {"libraries": 1, "media": 1}
    saved = db.one("SELECT * FROM plex_media WHERE plex_rating_key='42'")
    assert saved is not None
    assert saved["originally_available_at"] == "2025-01-25"


def test_snapshot_removes_stale_entries_for_the_refreshed_section(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    library = {"section_key": "3", "title": "Alternative", "library_type": "movie"}
    db.save_plex_library_snapshot(library, [{"rating_key": "old", "title": "Old"}])
    db.save_plex_library_snapshot(library, [{"rating_key": "new", "title": "New"}])
    assert db.one("SELECT * FROM plex_media WHERE plex_rating_key='old'") is None
    assert db.one("SELECT * FROM plex_media WHERE plex_rating_key='new'") is not None


def test_playlist_plan_uses_snapshot_and_conservative_top_18_matching(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    library = {"section_key": "3", "title": "Alternative", "library_type": "movie"}
    db.save_plex_library_snapshot(
        library,
        [
            {
                "rating_key": "new",
                "title": "Artist - Song",
                "originally_available_at": "2025-01-01",
                "media_path": "/media/Artist - Song.mp4",
            },
            {
                "rating_key": "old",
                "title": "Other - Tune",
                "originally_available_at": None,
                "media_path": "/media/Other - Tune.mp4",
            },
        ],
    )
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO runs(id, station, requested_at) VALUES (1, 'altnation', '2026-01-02')"
        )
        conn.execute(
            """INSERT INTO charts(run_id, station, source, as_of, captured_at)
               VALUES (1, 'altnation', 'test', '2026-01-02', '2026-01-02')"""
        )
        conn.execute(
            """INSERT INTO chart_entries(chart_id, rank, source_track_id, artist, title)
               VALUES (1, 1, 'a', 'Artist', 'Song')"""
        )
    plans, unresolved = build_playlist_plans(db, section_key="3", cutoff=date(2024, 8, 3))
    assert [(plan.title, plan.rating_keys) for plan in plans] == [
        ("Alt Nation — Latest Top 18", ("new",)),
        ("Music Videos — New (Last 2 Years)", ("new",)),
        ("Music Videos — Older (2+ Years)", ("old",)),
    ]
    assert unresolved == []


def test_playlist_client_creates_and_deletes_only_the_expected_endpoints():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET" and request.url.path == "/":
            return httpx.Response(200, content=b'<MediaContainer machineIdentifier="machine"/>')
        if request.method == "POST" and request.url.path == "/playlists":
            assert request.url.params["type"] == "video"
            assert request.url.params["title"] == "Example"
            return httpx.Response(
                200,
                content=b'<MediaContainer><Playlist ratingKey="99"/></MediaContainer>',
            )
        if request.method == "DELETE" and request.url.path == "/playlists/99":
            return httpx.Response(200, content=b"")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = PlexPlaylistClient(
        "http://plex.example:32400",
        "test-token",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.create_video_playlist(PlaylistPlan("Example", ("1", "2"))) == "99"
    client.delete_playlist("99")
    assert [request.method for request in calls] == ["GET", "POST", "DELETE"]
