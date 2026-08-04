from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from music_video_grabber.config import Settings, get_settings
from music_video_grabber.db import Database, utcnow
from music_video_grabber.main import app


def test_dashboard_renders() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Music Video Grabber" in response.text


def test_dashboard_login_and_personal_token_scopes(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_path=tmp_path / "mvg.db",
        media_dir=tmp_path,
        app_password="test-password",
        dashboard_session_secret="test-session-secret",
        api_token="legacy-service-token",
    )
    Database(settings.database_path).initialize()
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app, base_url="https://testserver") as client:
            assert "Sign in" in client.get("/").text
            assert client.post("/auth/login", data={"password": "wrong"}).status_code == 401
            login = client.post(
                "/auth/login", data={"password": "test-password"}, follow_redirects=False
            )
            assert login.status_code == 303
            assert "HttpOnly" in login.headers["set-cookie"]
            assert "Secure" in login.headers["set-cookie"]
            dashboard = client.get("/").text
            assert "Recent runs" in dashboard
            assert "youtube-nocookie.com/embed" in dashboard
            assert "if(!previewOpen)refresh();" in dashboard
            plex_status = client.get("/api/v1/plex-status")
            assert plex_status.status_code == 200
            assert plex_status.json()["snapshot"] is None
            assert plex_status.json()["configured"] is False

            with Database(settings.database_path).connect() as conn:
                track_id = conn.execute(
                    """INSERT INTO tracks(
                           station, canonical_key, title, artist, status, created_at, updated_at
                       ) VALUES ('altnation', 'test artist test song', 'Test Song', 'Test Artist',
                                 'review', ?, ?)""",
                    (utcnow(), utcnow()),
                ).lastrowid
                candidate_id = conn.execute(
                    """INSERT INTO candidates(track_id, video_id, url, title, score, created_at)
                       VALUES (?, 'test-video', 'https://example.invalid',
                               'Test video', 90, ?)""",
                    (track_id, utcnow()),
                ).lastrowid
            assert (
                client.post(
                    f"/api/v1/tracks/{track_id}/reject", json={"candidate_id": candidate_id}
                ).status_code
                == 200
            )
            review = client.get("/api/v1/review").json()
            assert review[0]["candidates"][0]["rejected"] == 1
            assert (
                client.post(
                    f"/api/v1/tracks/{track_id}/undo-reject", json={"candidate_id": candidate_id}
                ).status_code
                == 200
            )
            assert client.get("/api/v1/review").json()[0]["candidates"][0]["rejected"] == 0

            preferences = client.get("/api/v1/plex-playlist-preferences")
            assert preferences.status_code == 200
            assert preferences.json()["remove_unplanned_items"] is False
            saved_preferences = client.put(
                "/api/v1/plex-playlist-preferences",
                json={
                    "include_top_18": True,
                    "include_new": False,
                    "include_older": True,
                    "remove_unplanned_items": True,
                },
            )
            assert saved_preferences.status_code == 200
            assert saved_preferences.json()["include_new"] is False
            assert saved_preferences.json()["remove_unplanned_items"] is True
            invalid_preferences = client.put(
                "/api/v1/plex-playlist-preferences",
                json={
                    "include_top_18": False,
                    "include_new": False,
                    "include_older": False,
                    "remove_unplanned_items": False,
                },
            )
            assert invalid_preferences.status_code == 422

            created = client.post(
                "/api/v1/tokens", json={"name": "read client", "scopes": ["read"]}
            )
            assert created.status_code == 201
            secret = created.json()["secret"]
            token_id = created.json()["token"]["id"]
            assert secret.startswith("mvg_")

            client.post("/auth/logout", follow_redirects=False)
            assert client.get("/api/v1/runs").status_code == 401
            headers = {"Authorization": f"Bearer {secret}"}
            assert client.get("/api/v1/runs", headers=headers).status_code == 200
            response = client.post("/api/v1/runs", headers=headers, json={"station": "altnation"})
            assert response.status_code == 401

            client.post("/auth/login", data={"password": "test-password"}, follow_redirects=False)
            assert client.delete(f"/api/v1/tokens/{token_id}").status_code == 200
            client.post("/auth/logout", follow_redirects=False)
            assert client.get("/api/v1/runs", headers=headers).status_code == 401

            run_response = client.post(
                "/api/v1/runs",
                headers={"Authorization": "Bearer legacy-service-token"},
                json={"station": "SiriusXMHits1", "song_count": 2},
            )
            assert run_response.status_code == 202
            assert run_response.json()["station"] == "siriusxmhits1"
            assert run_response.json()["song_count"] == 2
            latest_chart = client.get(
                "/api/v1/charts/latest?station=siriusxmhits1",
                headers={"Authorization": "Bearer legacy-service-token"},
            )
            assert latest_chart.status_code == 404
            invalid_station = client.post(
                "/api/v1/runs",
                headers={"Authorization": "Bearer legacy-service-token"},
                json={"station": "hits/1", "song_count": 2},
            )
            assert invalid_station.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_plex_status_allows_partial_comparison_and_snapshot_refresh(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_path=tmp_path / "mvg.db",
        media_dir=tmp_path,
        api_token="legacy-service-token",
        plex_url="http://plex.example:32400",
        plex_token="test-token",
        plex_library_title="Alternative",
    )
    db = Database(settings.database_path)
    db.initialize()
    library = {"section_key": "3", "title": "Alternative", "library_type": "movie"}
    db.save_plex_library_snapshot(
        library,
        [
            {
                "rating_key": "new",
                "title": "Artist - Song",
                "originally_available_at": "2026-01-01",
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
        run_id = conn.execute(
            "INSERT INTO runs(station, requested_at) VALUES ('altnation', ?)", (utcnow(),)
        ).lastrowid
        chart_id = conn.execute(
            """INSERT INTO charts(run_id, station, source, as_of, captured_at)
               VALUES (?, 'altnation', 'test', ?, ?)""",
            (run_id, utcnow(), utcnow()),
        ).lastrowid
        conn.execute(
            """INSERT INTO chart_entries(chart_id, rank, artist, title)
               VALUES (?, 1, 'Missing', 'Video')""",
            (chart_id,),
        )

    class FakePlaylistClient:
        received_titles: list[str] = []

        def __init__(self, *_args):
            pass

        def playlist_refresh_plan(self, plans):
            self.__class__.received_titles = [plan.title for plan in plans]
            return [{"title": plan.title, "current_count": 0} for plan in plans]

    class FakeReadOnlyClient:
        def __init__(self, *_args):
            pass

        def snapshot_library(self):
            return library, [{"rating_key": "fresh", "title": "Fresh"}]

    app.dependency_overrides[get_settings] = lambda: settings
    headers = {"Authorization": "Bearer legacy-service-token"}
    try:
        with TestClient(app) as client:
            with patch("music_video_grabber.main.PlexPlaylistClient", FakePlaylistClient):
                live = client.get("/api/v1/plex-status/live", headers=headers)
            assert live.status_code == 200
            assert live.json()["refresh"][0]["status"] == "unresolved"
            assert FakePlaylistClient.received_titles == [
                "Music Videos — New (Last 2 Years)",
                "Music Videos — Older (2+ Years)",
            ]
            with patch("music_video_grabber.main.PlexReadOnlyClient", FakeReadOnlyClient):
                refreshed = client.post("/api/v1/plex-status/snapshot", headers=headers)
            assert refreshed.status_code == 200
            assert refreshed.json() == {"read_only": True, "snapshot": {"libraries": 1, "media": 1}}
            assert db.one("SELECT plex_rating_key FROM plex_media WHERE plex_rating_key='fresh'")
    finally:
        app.dependency_overrides.clear()
