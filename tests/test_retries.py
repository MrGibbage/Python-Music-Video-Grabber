from pathlib import Path

import pytest

from music_video_grabber.config import Settings
from music_video_grabber.db import Database
from music_video_grabber.jobs import JobProcessor
from music_video_grabber.providers import StationTrack, XmPlaylistClient, writable_cookie_copy


def test_xmplaylist_fixture_matches_captured_run_one():
    fixture = Path(__file__).parents[1] / "fixtures" / "xmplaylist-run-1.json"
    tracks = XmPlaylistClient(fixture).latest("altnation", 18)

    assert len(tracks) == 18
    assert tracks[12] == StationTrack(
        "3QS9-AHYZ", "Lost Boys", "Phoebe Bridgers", tracks[12].played_at
    )
    assert tracks[15].title == "Dreams"


def test_cookie_copy_is_writable_without_changing_source(tmp_path):
    source = tmp_path / "source-cookies.txt"
    source.write_text("original", encoding="utf-8")
    destination_dir = tmp_path / "staging"
    destination_dir.mkdir()

    with writable_cookie_copy(source, destination_dir) as copied:
        assert copied is not None
        copied.write_text("updated", encoding="utf-8")

    assert source.read_text(encoding="utf-8") == "original"
    assert not (destination_dir / "youtube-cookies.txt").exists()


def test_retry_reuses_persisted_chart_without_refetching(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    run_id = db.create_run("altnation")
    settings = Settings(top_tracks_limit=1)
    processor = JobProcessor(settings, db)
    track = StationTrack("song-1", "Song One", "Artist One", "2026-08-03T03:00:00+00:00")

    class FirstXm:
        calls = 0

        def latest(self, station, limit):
            self.calls += 1
            return [track]

    class RetryXm:
        def latest(self, station, limit):
            raise AssertionError("retry must use the persisted chart")

    class FailingYouTube:
        def search(self, title, artist):
            raise RuntimeError("temporary YouTube failure")

    job = db.claim_job()
    assert job is not None
    processor.xm = FirstXm()
    processor.youtube = FailingYouTube()
    with pytest.raises(RuntimeError, match="temporary YouTube failure"):
        processor._discover(job)
    db.fail_job(job, "temporary YouTube failure")

    retry = db.claim_job()
    assert retry is not None
    processor.xm = RetryXm()
    processor.youtube = FailingYouTube()
    with pytest.raises(RuntimeError, match="temporary YouTube failure"):
        processor._discover(retry)
    assert db.chart_for_run(run_id) is not None
