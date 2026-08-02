from __future__ import annotations

import re
import unicodedata


_NOISE = re.compile(
    r"\b(official|music|video|audio|visuali[sz]er|lyrics?|hd|4k|hq)\b",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(_NON_WORD.sub(" ", ascii_value.lower()).split())


def normalize_video_title(value: str) -> str:
    return " ".join(_NOISE.sub(" ", normalize(value)).split())


def canonical_track_key(artist: str, title: str) -> str:
    return f"{normalize(artist)}::{normalize(title)}"


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned[:180] or "Unknown"
