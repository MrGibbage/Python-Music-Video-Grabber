"""Tell a still-image upload (album art over the audio) from a real video.

The title can't: artist channels post both under the bare song title. YouTube's
storyboard can. It is the sprite sheet of thumbnails behind the scrub bar, one
small image covering the whole runtime, so a single download shows whether the
picture ever changes.

Calibrated on 2026-11-24 against 19 known videos. Still-image uploads had
0-18% of storyboard steps moving, a typewriter lyric video 36%, and real
videos 74-100%.
"""

from __future__ import annotations

import re

_BLACK = 16  # tiles (and pixels) at or below this are padding / empty grid cells
_PIXEL_CHANGE = 24  # grey levels; JPEG noise on a still picture stays under this
_STEP_MOVING = 0.15  # share of lit pixels that must change for a step to count
_STILL_BELOW = 0.25  # share of moving steps under which the whole video is still


def parse_pgm(data: bytes) -> tuple[int, int, bytes]:
    """Width, height and pixels of a binary 8-bit PGM (what ffmpeg writes)."""
    match = re.match(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s", data)
    if not match:
        raise ValueError("not a binary PGM image")
    width, height = int(match.group(1)), int(match.group(2))
    pixels = data[match.end() : match.end() + width * height]
    if len(pixels) != width * height:
        raise ValueError("truncated PGM image")
    return width, height, pixels


def moving_share(
    pixels: bytes, sheet_width: int, sheet_height: int, tile_width: int, tile_height: int
) -> float | None:
    """Share of consecutive storyboard tiles that differ visibly, or None when
    the sheet has too few tiles to judge."""
    columns = sheet_width // tile_width
    rows = sheet_height // tile_height
    tiles = []
    for row in range(rows):
        for column in range(columns):
            tile = b"".join(
                pixels[y * sheet_width + column * tile_width : y * sheet_width
                       + (column + 1) * tile_width]
                for y in range(row * tile_height, (row + 1) * tile_height)
            )
            if max(tile, default=0) > _BLACK:
                tiles.append(tile)
    if len(tiles) < 10:
        return None
    moving = 0
    for before, after in zip(tiles, tiles[1:], strict=False):
        lit = [(a, b) for a, b in zip(before, after, strict=True) if a > _BLACK or b > _BLACK]
        changed = sum(abs(a - b) > _PIXEL_CHANGE for a, b in lit)
        if lit and changed / len(lit) > _STEP_MOVING:
            moving += 1
    return moving / (len(tiles) - 1)


def is_still(share: float | None) -> bool | None:
    return None if share is None else share < _STILL_BELOW
