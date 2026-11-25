from datetime import UTC, datetime, timedelta

from music_video_grabber.config import Settings
from music_video_grabber.countdown import EASTERN, Play, find_countdown, weekend_for
from music_video_grabber.db import Database
from music_video_grabber.jobs import JobProcessor
from music_video_grabber.providers import StationTrack

SAT = datetime(2026, 11, 21, tzinfo=EASTERN)
CHART = [f"Song {n:02d}" for n in range(18, 0, -1)]  # aired #18 first, #1 last


def airing(start, before, songs, after, gap=3.5):
    """Plays (oldest first) starting at ``start``: lead-in, songs, trailing songs."""
    plays, t = [], start
    for title in [*before, *songs, *after]:
        plays.append(Play(t, "Band", title))
        t += timedelta(minutes=gap)
    return plays


def sat(hour, minute):
    return SAT.replace(hour=hour, minute=minute)


def test_countdown_is_the_run_both_airings_share():
    original = airing(sat(12, 45), ["Pre A", "Pre B", "Pre C", "Pre D"], CHART, ["Post A"])
    replay = airing(sat(17, 45), ["Other A", "Other B", "Other C", "Other D"], CHART, ["Other E"])
    match = find_countdown(original, replay)
    assert match.found and match.matched == 18
    assert [p.title for p in match.plays] == CHART
    assert match.plays[0].played_at == sat(12, 45) + timedelta(minutes=14)  # Saturday times


def test_number_one_debut_and_new_entries_need_no_history():
    chart = ["Brand New Single", *CHART[1:]]  # a debut anywhere changes nothing
    original = airing(sat(12, 45), ["Pre"], chart, ["Post"])
    replay = airing(sat(17, 45), ["Other"], chart, ["Other 2"])
    assert find_countdown(original, replay).found


def test_special_show_instead_of_countdown_aborts():
    original = airing(sat(12, 45), ["Pre"], CHART, ["Post"])
    interview = airing(sat(17, 45), ["Talk"], CHART[:9] + ["Interview clip"] + CHART[10:], [])
    match = find_countdown(original, interview)
    assert not match.found and match.matched < 18


def test_a_shared_neighbour_making_the_run_too_long_aborts():
    original = airing(sat(12, 45), ["Same lead-in"], CHART, ["Post"])
    replay = airing(sat(17, 45), ["Same lead-in"], CHART, ["Other"])
    match = find_countdown(original, replay)
    assert match.matched == 19 and not match.found


def test_weekend_boundaries():
    assert weekend_for(sat(11, 59)).saturday.isoformat() == "2026-11-14"
    assert weekend_for(sat(19, 30)).saturday.isoformat() == "2026-11-21"
    monday = datetime(2026, 11, 23, 16, 30, tzinfo=EASTERN)
    weekend = weekend_for(monday)
    assert weekend.saturday.isoformat() == "2026-11-21"
    assert weekend.is_final_attempt(monday)
    assert not weekend.is_final_attempt(datetime(2026, 11, 22, 23, 30, tzinfo=EASTERN))
    # A run during the Saturday airing has no replay window yet.
    assert weekend.replay_window(sat(14, 30)) is None
    start, _ = weekend.replay_window(sat(19, 30))
    assert start == sat(16, 30)


# --- the run itself -------------------------------------------------------


class HistoryXm:
    def __init__(self, original, replay):
        self.windows = {"original": original, "replay": replay}

    def history(self, station, start, end):
        plays = self.windows["original" if start.astimezone(EASTERN).hour == 12 else "replay"]
        return [
            StationTrack(
                f"id-{p.title}", p.title, p.artist, p.played_at.astimezone(UTC).isoformat()
            )
            for p in plays
            if start <= p.played_at <= end
        ]


class NoSpotify:
    def release_year(self, title, artist):
        return None


class NoYouTube:
    def search(self, title, artist):
        return []


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def send(self, subject, body):
        self.sent.append((subject, body))
        return True


def run_countdown(tmp_path, original, replay, now, db=None):
    db = db or Database(tmp_path / "app.db")
    db.initialize()
    proc = JobProcessor(Settings(media_dir=tmp_path, still_image_checks=0), db)
    proc.xm = HistoryXm(original, replay)
    proc.spotify, proc.youtube = NoSpotify(), NoYouTube()
    proc.notifier = RecordingNotifier()
    proc.now = lambda: now
    run_id = db.create_run("altnation", mode="countdown")
    job = db.claim_job()
    proc.process(job)
    db.finish_job(job["id"])
    proc.maybe_finalize_run(run_id)
    return db, proc, db.one("SELECT * FROM runs WHERE id=?", (run_id,))


def test_confirmed_countdown_is_saved_with_number_one_first(tmp_path):
    original = airing(sat(12, 45), ["Pre A", "Pre B", "Pre C", "Pre D"], CHART, ["Post A"])
    replay = airing(sat(17, 45), ["X", "Y", "Z", "W"], CHART, ["V"])
    db, proc, run = run_countdown(tmp_path, original, replay, sat(19, 30))
    chart = db.one("SELECT * FROM charts WHERE run_id=?", (run["id"],))
    assert chart["source"] == "xmplaylist_countdown"
    ranks = db.query("SELECT rank, title FROM chart_entries WHERE chart_id=? ORDER BY rank",
                     (chart["id"],))
    assert ranks[0] == {"rank": 1, "title": "Song 01"}
    assert ranks[-1] == {"rank": 18, "title": "Song 18"}
    windows = db.query("SELECT label FROM station_play_windows WHERE run_id=?", (run["id"],))
    assert {w["label"] for w in windows} == {"countdown_original", "countdown_replay"}

    # The next scheduled attempt that weekend skips quietly.
    _, proc2, run2 = run_countdown(tmp_path, original, replay, sat(23, 30), db=db)
    assert run2["status"] == "skipped"
    assert proc2.notifier.sent == []


def test_unaligned_airings_abort_and_notify(tmp_path):
    original = airing(sat(12, 45), ["Pre"], CHART, ["Post"])
    special = airing(sat(17, 45), ["Talk"], ["Interview"] * 3 + CHART[:6], [])
    db, proc, run = run_countdown(tmp_path, original, special, sat(19, 30))
    assert run["status"] == "aborted"
    assert db.one("SELECT COUNT(*) n FROM charts")["n"] == 0
    subject, body = proc.notifier.sent[0]
    assert "not confirmed" in subject
    assert "try again after the next replay" in body


def test_last_attempt_says_so(tmp_path):
    original = airing(sat(12, 45), ["Pre"], CHART, ["Post"])
    monday = datetime(2026, 11, 23, 16, 30, tzinfo=EASTERN)
    special = airing(monday - timedelta(hours=1, minutes=30), ["Talk"], CHART[:5], [])
    _, proc, run = run_countdown(tmp_path, original, special, monday)
    assert run["status"] == "aborted"
    assert "last replay this weekend" in proc.notifier.sent[0][1]
