from __future__ import annotations

import re
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
    channel_verified: bool | None = None
    # From the storyboard check: one picture for the whole runtime. None = not checked.
    still_image: bool | None = None


@dataclass(frozen=True)
class ScoreResult:
    score: float
    reasons: tuple[str, ...]


def _ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


# Channels that publish official videos for many artists: record labels and
# official session/performance series. Compared with spaces removed, exactly.
OFFICIAL_CHANNELS = frozenset(
    name.replace(" ", "")
    for name in (
        "warner records", "atlantic records", "interscope records", "republic records",
        "columbia records", "rca records", "capitol records", "island records", "def jam",
        "elektra records", "epic records", "fueled by ramen", "sub pop", "matador records",
        "4ad", "xl recordings", "domino recording co", "rough trade records", "dirty hit",
        "polydor records", "parlophone records", "virgin records", "mom pop music",
        "glassnote records", "arista records", "sony music", "universal music group",
        "rhino", "roadrunner records", "concord records", "bmg", "loma vista recordings",
        "partisan records", "secretly canadian", "jagjaguwar", "dead oceans", "ato records",
        "nettwerk music group", "big loud records", "300 entertainment", "ninja tune",
        "transgressive records", "fiction records", "hopeless records", "pure noise records",
        "rise records", "epitaph records", "fearless records", "anti records", "merge records",
        "awal", "atlantic records uk", "emi records",
        "siriusxm", "sirius xm", "npr music", "kexp", "audiotree", "colors",
        "spotify", "apple music", "amazon music", "vevo", "vevo dscvr",
    )
)
# A result without one of these in its title might be a still-image audio
# upload even on the artist's own channel, so it is never auto-approved.
_VIDEO_MARKERS = ("official video", "music video", "official hd video", "official 4k video")
_NO_MARKER_CAP = 85.0
# Enough to drop the artist's own album-art upload (90 before the cap) below the
# same artist's lyric video (about 66-71), which at least shows the words.
_STILL_IMAGE_PENALTY = 30.0
# Below this title similarity the result is almost always a different song from
# the same artist, so channel and "official" bonuses must not lift it.
_TITLE_MISMATCH = 0.6
_MISMATCH_CAP = 40.0


def _squash(value: str) -> str:
    return value.replace(" ", "")


_ARTIST_SPLIT = re.compile(
    r",|&|\+|/|\s(?:and|x|feat\.?|ft\.?|with|featuring)\s", re.IGNORECASE
)


def _artist_names(artist: str) -> set[str]:
    """The full credit plus each credited artist, normalized with spaces removed.

    Splits the raw credit (before normalization strips the commas) so that
    "Daniel Caesar, Rex Orange County" matches either artist's own channel.
    """
    names = {_squash(normalize(artist))}
    names.update(_squash(normalize(part)) for part in _ARTIST_SPLIT.split(artist))
    return {name for name in names if name}


def _channel_is_artist(channel: str, artist: str) -> bool:
    """True when a channel name is the artist's own: an exact match, or the name
    plus a short suffix such as "official" or "music" (never a mere substring,
    so "muse" does not match "museumofsound")."""
    for name in _artist_names(artist):
        bare = name.removeprefix("the")
        for candidate in {name, bare} - {""}:
            if channel == candidate or channel == "the" + candidate:
                return True
            if channel.startswith(candidate) and channel[len(candidate):] in {
                "official", "music", "band", "tv", "officialchannel", "channel", "hq"
            }:
                return True
    return False


def has_video_marker(title: str) -> bool:
    raw = normalize(title)
    return any(marker in raw for marker in _VIDEO_MARKERS)


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
    artist_in_title = _ratio(wanted_artist, raw)
    if wanted_artist and wanted_artist in raw:
        artist_in_title = 1.0

    is_topic = uploader.endswith(" topic")
    is_vevo = uploader.endswith("vevo") or " vevo" in uploader
    channel = _squash(uploader.removesuffix(" topic").removesuffix("vevo").strip())
    by_artist = bool(
        wanted_artist and not is_topic and channel and _channel_is_artist(channel, artist)
    )
    is_official_channel = not is_topic and _squash(uploader) in OFFICIAL_CHANNELS

    vevo_for_artist = is_vevo and _channel_is_artist(channel, artist)
    artist_ratio = 1.0 if by_artist or vevo_for_artist else (
        max(artist_in_title, _ratio(wanted_artist, uploader))
    )
    score = title_ratio * 58 + artist_ratio * 24
    reasons = [f"title {title_ratio:.0%}", f"artist in title {artist_in_title:.0%}"]

    if "official video" in raw or "official music video" in raw:
        score += 10
        reasons.append("official video")
    elif "official" in raw:
        score += 5
        reasons.append("official")

    if is_topic:
        score -= 30
        reasons.append("auto-generated Topic channel (album art)")
    elif is_vevo:
        score += 6
        reasons.append("VEVO uploader")
    elif by_artist:
        score += 6
        reasons.append("uploaded by artist")
        if candidate.channel_verified:
            score += 2
            reasons.append("verified channel")
    elif is_official_channel:
        score += 6
        reasons.append(f"official channel: {candidate.uploader.strip()}")
    elif uploader:
        score -= 8
        reasons.append(f"re-upload by {candidate.uploader.strip()}")

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

    if candidate.still_image:
        score -= _STILL_IMAGE_PENALTY
        reasons.append("still image for the whole video")

    if candidate.duration is not None and candidate.duration < 60:
        score -= 20
        reasons.append("short duration")

    if title_ratio < _TITLE_MISMATCH and score > _MISMATCH_CAP:
        score = _MISMATCH_CAP
        reasons.append("title mismatch: probably a different song")
    if score > _NO_MARKER_CAP and not any(marker in raw for marker in _VIDEO_MARKERS):
        score = _NO_MARKER_CAP
        reasons.append("no video marker in title: needs review")

    return ScoreResult(round(max(0.0, min(100.0, score)), 1), tuple(reasons))


def should_auto_approve(
    scores: list[float], *, threshold: float, minimum_margin: float
) -> bool:
    if not scores or scores[0] < threshold:
        return False
    return len(scores) == 1 or scores[0] - scores[1] >= minimum_margin
