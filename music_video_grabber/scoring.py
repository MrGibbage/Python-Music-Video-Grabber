from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from .text import normalize, normalize_video_title


@dataclass(frozen=True)
class CandidateInput:
    video_id: str
    url: str
    title: str
    uploader: str = ""
    duration: int | None = None
    view_count: int | None = None


@dataclass(frozen=True)
class ScoreResult:
    score: float
    reasons: tuple[str, ...]


def _ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def score_candidate(track_title: str, artist: str, candidate: CandidateInput) -> ScoreResult:
    raw = normalize(candidate.title)
    cleaned = normalize_video_title(candidate.title)
    wanted_title = normalize_video_title(track_title)
    wanted_artist = normalize(artist)
    uploader = normalize(candidate.uploader)

    title_only = cleaned.replace(wanted_artist, " ").strip(" -") if wanted_artist else cleaned
    title_ratio = max(
        _ratio(wanted_title, cleaned),
        _ratio(wanted_title, raw),
        _ratio(wanted_title, title_only),
    )
    artist_ratio = max(_ratio(wanted_artist, raw), _ratio(wanted_artist, uploader))
    if wanted_artist and (wanted_artist in raw or wanted_artist in uploader):
        artist_ratio = 1.0
    score = title_ratio * 58 + artist_ratio * 24
    reasons = [f"title {title_ratio:.0%}", f"artist {artist_ratio:.0%}"]

    if "official video" in raw or "official music video" in raw:
        score += 10
        reasons.append("official video")
    elif "official" in raw:
        score += 5
        reasons.append("official")
    if uploader.endswith("vevo") or " vevo" in uploader:
        score += 6
        reasons.append("VEVO uploader")
    if wanted_artist and wanted_artist in uploader:
        score += 6
        reasons.append("artist uploader")

    penalties = {
        "lyric": 24,
        "visualizer": 20,
        "audio": 20,
        "live": 28,
        "cover": 35,
        "reaction": 45,
        "remix": 25,
        "karaoke": 50,
    }
    for term, penalty in penalties.items():
        if term in raw and term not in normalize(track_title):
            score -= penalty
            reasons.append(f"{term} penalty")

    if candidate.duration is not None and candidate.duration < 60:
        score -= 20
        reasons.append("short duration")

    return ScoreResult(round(max(0.0, min(100.0, score)), 1), tuple(reasons))


def should_auto_approve(
    scores: list[float], *, threshold: float, minimum_margin: float
) -> bool:
    if not scores or scores[0] < threshold:
        return False
    return len(scores) == 1 or scores[0] - scores[1] >= minimum_margin
