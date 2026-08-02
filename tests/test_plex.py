import httpx

from music_video_grabber.config import Settings
from music_video_grabber.db import Database
from music_video_grabber.plex import PlexReadOnlyClient


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
