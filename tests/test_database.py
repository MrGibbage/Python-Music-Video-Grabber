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
