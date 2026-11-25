from pathlib import Path

from fastapi.testclient import TestClient

from music_video_grabber.config import Settings
from music_video_grabber.db import Database
from music_video_grabber.jobs import JobProcessor
from music_video_grabber.main import app, get_settings
from music_video_grabber.providers import StationTrack, is_youtube_video_url
from music_video_grabber.scoring import CandidateInput

TRACK = StationTrack("song-1", "Song One", "Artist One", "2026-11-21T18:00:00+00:00")


class FixedXm:
    def latest(self, station, limit):
        return [TRACK]


class NoSpotify:
    def release_year(self, title, artist):
        return None


class FakeYouTube:
    def __init__(self, videos):
        self.videos = videos

    def search(self, title, artist):
        return [
            CandidateInput(
                video_id=vid, url=f"https://www.youtube.com/watch?v={vid}", title=t,
                uploader=rest[0] if rest else "",
            )
            for vid, t, *rest in self.videos
        ]

    def lookup(self, url):
        return CandidateInput(video_id="manualvid01", url=url, title="Artist One - Song One")


# Titles chosen so nothing auto-approves unless a test wants it to.
WEAK = [("weakvideo01", "Song One lyrics fan edit"), ("weakvideo02", "Song One cover live")]
STRONG = ("strongvid01", "Artist One - Song One (Official Music Video)")
# Good enough to show again (scores in the 60s), but not to auto-download.
DECENT = ("decentvid01", "Artist One - Song One (Official Lyric Video)")


def processor(tmp_path, videos):
    db = Database(tmp_path / "app.db")
    db.initialize()
    proc = JobProcessor(Settings(top_tracks_limit=1, media_dir=tmp_path), db)
    proc.xm = FixedXm()
    proc.spotify = NoSpotify()
    proc.youtube = FakeYouTube(videos)
    return db, proc


def run_discovery(db, proc):
    db.create_run("altnation")
    job = db.claim_job()
    proc._discover(job)
    db.finish_job(job["id"])
    return db.one("SELECT * FROM tracks WHERE canonical_key IS NOT NULL")


def test_rejections_survive_rediscovery(tmp_path):
    db, proc = processor(tmp_path, WEAK)
    track = run_discovery(db, proc)
    assert track["status"] == "review"
    for c in db.query("SELECT id FROM candidates WHERE track_id=?", (track["id"],)):
        proc.reject(track["id"], c["id"])

    proc.youtube = FakeYouTube(WEAK[:1])  # one rejected video drops out of the search
    run_discovery(db, proc)
    rows = db.query("SELECT video_id, rejected FROM candidates WHERE track_id=?", (track["id"],))
    assert {r["video_id"]: r["rejected"] for r in rows} == {"weakvideo01": 1, "weakvideo02": 1}


def test_dismissed_track_stays_dismissed_without_new_videos(tmp_path):
    db, proc = processor(tmp_path, WEAK)
    track = run_discovery(db, proc)
    proc.dismiss(track["id"])
    run_discovery(db, proc)
    assert db.one("SELECT status FROM tracks WHERE id=?", (track["id"],))["status"] == "dismissed"
    run = db.one("SELECT summary_json FROM runs ORDER BY id DESC LIMIT 1")
    assert '"dismissed": 1' in run["summary_json"]


def test_dismissed_track_resurfaces_when_youtube_has_a_decent_new_video(tmp_path):
    db, proc = processor(tmp_path, WEAK[:1])
    track = run_discovery(db, proc)
    proc.dismiss(track["id"])
    proc.youtube = FakeYouTube([DECENT, *WEAK])
    run_discovery(db, proc)
    assert db.one("SELECT status FROM tracks WHERE id=?", (track["id"],))["status"] == "review"


def test_weak_or_reuploaded_new_videos_do_not_resurface(tmp_path):
    db, proc = processor(tmp_path, WEAK[:1])
    track = run_discovery(db, proc)
    proc.dismiss(track["id"])
    # A new weak result, and a decent-looking title from a fan channel.
    proc.youtube = FakeYouTube([*WEAK, ("fanupload01", DECENT[1], "Random Fan 99")])
    run_discovery(db, proc)
    assert db.one("SELECT status FROM tracks WHERE id=?", (track["id"],))["status"] == "dismissed"
    proc._research({"payload": {"track_id": track["id"]}})
    assert db.one("SELECT status FROM tracks WHERE id=?", (track["id"],))["status"] == "dismissed"


def test_dismissed_track_auto_downloads_a_new_strong_video(tmp_path):
    db, proc = processor(tmp_path, WEAK)
    track = run_discovery(db, proc)
    proc.dismiss(track["id"])
    proc.youtube = FakeYouTube([STRONG, *WEAK])
    run_discovery(db, proc)
    assert db.one("SELECT status FROM tracks WHERE id=?", (track["id"],))["status"] == "queued"


def test_only_review_like_tracks_can_be_dismissed(tmp_path):
    db, proc = processor(tmp_path, WEAK)
    track = run_discovery(db, proc)
    with db.connect() as conn:
        conn.execute("UPDATE tracks SET status='published' WHERE id=?", (track["id"],))
    try:
        proc.dismiss(track["id"])
    except ValueError:
        pass
    else:
        raise AssertionError("published track must not be dismissable")


def test_manual_candidate_queues_download(tmp_path):
    db, proc = processor(tmp_path, WEAK)
    track = run_discovery(db, proc)
    proc.dismiss(track["id"])
    proc.queue_manual_candidate(track["id"], "https://youtu.be/manualvid01")
    job = db.claim_job()
    proc.process(job)
    assert db.one("SELECT status FROM tracks WHERE id=?", (track["id"],))["status"] == "queued"
    download = db.claim_job()
    assert download["kind"] == "download"


def test_youtube_url_validation():
    assert is_youtube_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube_video_url("https://youtu.be/dQw4w9WgXcQ?t=3")
    assert not is_youtube_video_url("https://example.com/watch?v=dQw4w9WgXcQ")
    assert not is_youtube_video_url("https://www.youtube.com/playlist?list=PL123")
    assert not is_youtube_video_url("file:///etc/passwd")


def test_dismiss_api_round_trip(tmp_path: Path):
    settings = Settings(
        environment="test",
        database_path=tmp_path / "mvg.db",
        media_dir=tmp_path,
        app_password="pw",
        dashboard_session_secret="secret",
        api_token="svc",
    )
    db = Database(settings.database_path)
    db.initialize()
    with db.connect() as conn:
        track_id = conn.execute(
            """INSERT INTO tracks(station, canonical_key, title, artist, status,
                                  created_at, updated_at)
               VALUES ('altnation', 'a s', 'S', 'A', 'review', 'now', 'now')"""
        ).lastrowid
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app, base_url="https://testserver") as client:
            client.post("/auth/login", data={"password": "pw"})
            assert client.post(f"/api/v1/tracks/{track_id}/dismiss").status_code == 200
            assert client.get("/api/v1/review").json() == []
            assert [t["id"] for t in client.get("/api/v1/dismissed").json()] == [track_id]
            assert client.post(f"/api/v1/tracks/{track_id}/dismiss").status_code == 409
            bad = client.post(
                f"/api/v1/tracks/{track_id}/manual-url", json={"url": "https://example.com/x"}
            )
            assert bad.status_code == 422
            good = client.post(
                f"/api/v1/tracks/{track_id}/manual-url",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            )
            assert good.status_code == 202
            assert client.post(f"/api/v1/tracks/{track_id}/search").status_code == 202
            assert client.post(f"/api/v1/tracks/{track_id}/restore").status_code == 200
            assert [t["id"] for t in client.get("/api/v1/review").json()] == [track_id]
            assert client.post("/api/v1/tracks/999/dismiss").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_not_for_me_is_never_searched_again(tmp_path):
    db, proc = processor(tmp_path, WEAK)
    track = run_discovery(db, proc)
    proc.not_for_me(track["id"])
    # Even a strong new video on the next run must not bring it back or download it.
    proc.youtube = FakeYouTube([STRONG, *WEAK])
    run_discovery(db, proc)
    assert db.one("SELECT status FROM tracks WHERE id=?", (track["id"],))["status"] == "ignored"
    assert db.one("SELECT COUNT(*) n FROM jobs WHERE kind='download'")["n"] == 0
    run = db.one("SELECT summary_json FROM runs ORDER BY id DESC LIMIT 1")
    assert '"not_for_me": 1' in run["summary_json"]
    proc._research({"payload": {"track_id": track["id"]}})
    assert db.one("SELECT status FROM tracks WHERE id=?", (track["id"],))["status"] == "ignored"


def test_dismissed_track_can_be_marked_not_for_me_and_restored(tmp_path):
    db, proc = processor(tmp_path, WEAK)
    track = run_discovery(db, proc)
    proc.dismiss(track["id"])
    proc.not_for_me(track["id"])
    proc.restore(track["id"])
    assert db.one("SELECT status FROM tracks WHERE id=?", (track["id"],))["status"] == "review"


def test_not_for_me_api_and_chart_status(tmp_path: Path):
    settings = Settings(
        environment="test",
        database_path=tmp_path / "mvg.db",
        media_dir=tmp_path,
        app_password="pw",
        dashboard_session_secret="secret",
        api_token="svc",
    )
    db = Database(settings.database_path)
    db.initialize()
    proc = JobProcessor(Settings(top_tracks_limit=1, media_dir=tmp_path), db)
    proc.xm, proc.spotify, proc.youtube = FixedXm(), NoSpotify(), FakeYouTube(WEAK)
    track = run_discovery(db, proc)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app, base_url="https://testserver") as client:
            client.post("/auth/login", data={"password": "pw"})
            chart = client.get("/api/v1/charts/latest").json()
            assert chart["entries"][0]["track_status"] == "review"
            assert client.post(f"/api/v1/tracks/{track['id']}/not-for-me").status_code == 200
            assert client.post(f"/api/v1/tracks/{track['id']}/not-for-me").status_code == 409
            listed = client.get("/api/v1/not-for-me").json()
            assert [t["id"] for t in listed] == [track["id"]]
            assert listed[0]["marked_at"] and listed[0]["skipped_since"] == 0
            chart = client.get("/api/v1/charts/latest").json()
            assert chart["entries"][0]["track_status"] == "ignored"
            run_discovery(db, proc)
            assert client.get("/api/v1/not-for-me").json()[0]["skipped_since"] == 1
            assert client.post(f"/api/v1/tracks/{track['id']}/restore").status_code == 200
            assert client.get("/api/v1/not-for-me").json() == []
    finally:
        app.dependency_overrides.clear()
