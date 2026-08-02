from __future__ import annotations

import logging
import signal
import time

from .config import get_settings
from .db import database_from_settings, utcnow
from .jobs import JobProcessor
from .logging_config import configure_logging

logger = logging.getLogger(__name__)


def run_worker(*, once: bool = False) -> None:
    configure_logging()
    settings = get_settings()
    db = database_from_settings(settings)
    db.initialize()
    processor = JobProcessor(settings, db)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("Worker started", extra={"event": "worker_started"})

    while not stopping:
        job = db.claim_job()
        if job is None:
            if once:
                break
            time.sleep(settings.worker_poll_seconds)
            continue
        logger.info(
            "Processing %s job", job["kind"], extra={"event": "job_started", "job_id": job["id"]}
        )
        try:
            processor.process(job)
        except Exception as exc:
            logger.exception(
                "Job failed", extra={"event": "job_failed", "job_id": job["id"]}
            )
            db.fail_job(job, str(exc))
            if job["kind"] == "download":
                track_id = job["payload"].get("track_id")
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE tracks SET status='failed', updated_at=? WHERE id=?",
                        (utcnow(), track_id),
                    )
        else:
            db.finish_job(job["id"])
            logger.info(
                "Job succeeded", extra={"event": "job_succeeded", "job_id": job["id"]}
            )
        finally:
            processor.maybe_finalize_run(job["run_id"])
        if once:
            break

    logger.info("Worker stopped", extra={"event": "worker_stopped"})
