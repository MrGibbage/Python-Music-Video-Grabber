import json

from music_video_grabber.db import Database


def test_job_claim_is_durable(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    run_id = db.create_run("altnation")
    job = db.claim_job()
    assert job is not None
    assert job["run_id"] == run_id
    assert job["kind"] == "discover"
    assert job["status"] == "queued"
    assert db.claim_job() is None
    db.finish_job(job["id"])
    assert db.one("SELECT status FROM jobs WHERE id=?", (job["id"],))["status"] == "succeeded"


def test_run_keeps_station_and_requested_song_count_in_discover_job(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()

    run_id = db.create_run("siriusxmhits1", song_count=2)
    run = db.one("SELECT station FROM runs WHERE id=?", (run_id,))
    job = db.one("SELECT payload_json FROM jobs WHERE run_id=?", (run_id,))

    assert run == {"station": "siriusxmhits1"}
    assert json.loads(job["payload_json"]) == {"station": "siriusxmhits1", "song_count": 2}


def test_chart_snapshot_preserves_rank_and_as_of_date(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    run_id = db.create_run("altnation")
    db.save_chart(
        run_id=run_id,
        station="altnation",
        source="xmplaylist_recent_plays",
        as_of="2026-08-03T03:00:00+00:00",
        entries=[
            {
                "rank": 1,
                "source_track_id": "song-1",
                "artist": "Artist One",
                "title": "Song One",
                "played_at": "2026-08-03T03:00:00+00:00",
            },
            {
                "rank": 2,
                "source_track_id": "song-2",
                "artist": "Artist Two",
                "title": "Song Two",
                "played_at": "2026-08-03T02:56:00+00:00",
            },
        ],
    )

    chart = db.latest_chart("altnation")

    assert chart is not None
    assert chart["as_of"] == "2026-08-03T03:00:00+00:00"
    assert [entry["rank"] for entry in chart["entries"]] == [1, 2]
    assert chart["entries"][0]["title"] == "Song One"
