"""Find the Alt Nation Top 18 countdown by matching its airing against a replay.

The countdown first airs Saturday around 13:00 ET and is replayed several
times over the weekend (seen 2026-11-21/23: Sat 18:00, Sun 03:00, 10:00,
16:00, 22:00, Mon 15:00 ET). Every airing plays the same 18 songs in the same
order, while the songs around it differ each time. So the countdown is exactly
the run of plays the Saturday airing and a replay have in common. Nothing is
guessed: if that common run isn't exactly 18 long (a special show, an interview,
a gap in xmplaylist's data), the caller aborts and tries the next replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .text import canonical_track_key

EASTERN = ZoneInfo("America/New_York")
COUNTDOWN_SIZE = 18
# The Saturday airing: the countdown has started 12:58-13:01 and ended
# 14:01-14:08 ET in every week checked, so this window holds it with margin.
ORIGINAL_START = time(12, 30)
ORIGINAL_END = time(14, 50)
# How far back from the run time to look for the replay. Runs are scheduled
# about 30 minutes after a replay ends, so 3 hours holds the whole replay.
REPLAY_LOOKBACK = timedelta(hours=3)


@dataclass(frozen=True)
class Play:
    played_at: datetime  # timezone-aware
    artist: str
    title: str
    source_track_id: str = ""

    @property
    def key(self) -> str:
        return canonical_track_key(self.artist, self.title)


@dataclass(frozen=True)
class CountdownMatch:
    """The longest run the two airings share. ``plays`` is set only when that
    run is exactly the countdown's length and unique."""

    matched: int
    plays: tuple[Play, ...] = ()

    @property
    def found(self) -> bool:
        return bool(self.plays)


def find_countdown(
    original: list[Play], replay: list[Play], size: int = COUNTDOWN_SIZE
) -> CountdownMatch:
    """Plays of the countdown, oldest first (#18 to #1), as aired on Saturday."""
    a = sorted(original, key=lambda p: p.played_at)
    b = sorted(replay, key=lambda p: p.played_at)
    ka = [p.key for p in a]
    kb = [p.key for p in b]
    best = 0
    ends: set[int] = set()
    previous = [0] * (len(kb) + 1)
    for i in range(1, len(ka) + 1):
        current = [0] * (len(kb) + 1)
        for j in range(1, len(kb) + 1):
            if ka[i - 1] == kb[j - 1]:
                current[j] = previous[j - 1] + 1
                if current[j] > best:
                    best, ends = current[j], {i}
                elif current[j] == best:
                    ends.add(i)
        previous = current
    if best != size or len(ends) != 1:
        return CountdownMatch(matched=best)
    end = ends.pop()
    return CountdownMatch(matched=best, plays=tuple(a[end - size : end]))


@dataclass(frozen=True)
class Weekend:
    saturday: date

    def at(self, day_offset: int, clock: time) -> datetime:
        return datetime.combine(self.saturday + timedelta(days=day_offset), clock, EASTERN)

    @property
    def original_window(self) -> tuple[datetime, datetime]:
        return self.at(0, ORIGINAL_START), self.at(0, ORIGINAL_END)

    @property
    def starts(self) -> datetime:
        """Noon Saturday ET: a chart with ``as_of`` after this belongs to this weekend."""
        return self.at(0, time(12, 0))

    def replay_window(self, now: datetime) -> tuple[datetime, datetime] | None:
        """The stretch before ``now`` to search for a replay, never overlapping the
        Saturday airing (else the airing would trivially match itself)."""
        start = max(now - REPLAY_LOOKBACK, self.original_window[1])
        return (start, now) if start < now else None

    def is_final_attempt(self, now: datetime) -> bool:
        """True once the Monday afternoon replay has aired: no later one to wait for."""
        return now >= self.at(2, time(16, 0))


def weekend_for(now: datetime) -> Weekend:
    """The countdown weekend ``now`` falls in: from Saturday noon ET onward."""
    local = now.astimezone(EASTERN)
    days_back = (local.weekday() - 5) % 7  # Saturday is weekday 5
    saturday = local.date() - timedelta(days=days_back)
    if days_back == 0 and local.time() < time(12, 0):
        saturday -= timedelta(days=7)
    return Weekend(saturday)
