from music_video_grabber.config import Settings
from music_video_grabber.db import Database
from music_video_grabber.jobs import JobProcessor
from music_video_grabber.scoring import CandidateInput, score_candidate
from music_video_grabber.stillness import is_still, moving_share, parse_pgm

TILE_W, TILE_H, COLS, ROWS = 8, 6, 5, 4


def sheet(tile_value):
    """A storyboard sheet whose tile i is filled by tile_value(i, x, y)."""
    width, height = TILE_W * COLS, TILE_H * ROWS
    pixels = bytearray(width * height)
    for index in range(COLS * ROWS):
        column, row = index % COLS, index // COLS
        for y in range(TILE_H):
            for x in range(TILE_W):
                pixels[(row * TILE_H + y) * width + column * TILE_W + x] = tile_value(index, x, y)
    return bytes(pixels), width, height


def test_still_picture_with_jpeg_noise_is_still():
    pixels, w, h = sheet(lambda i, x, y: 120 + (x * 7 + y * 3 + i) % 9)
    assert is_still(moving_share(pixels, w, h, TILE_W, TILE_H)) is True


def test_changing_picture_is_not_still():
    pixels, w, h = sheet(lambda i, x, y: 40 if (i + x) % 2 else 200)
    assert is_still(moving_share(pixels, w, h, TILE_W, TILE_H)) is False


def test_black_padding_tiles_are_ignored():
    # A still picture in the first 12 tiles; the rest of the grid is empty.
    pixels, w, h = sheet(lambda i, x, y: 150 if i < 12 else 0)
    assert moving_share(pixels, w, h, TILE_W, TILE_H) == 0.0


def test_too_few_tiles_is_unknown():
    pixels, w, h = sheet(lambda i, x, y: 150 if i < 5 else 0)
    assert is_still(moving_share(pixels, w, h, TILE_W, TILE_H)) is None


def test_parse_pgm():
    assert parse_pgm(b"P5\n3 2\n255\n" + bytes(range(6))) == (3, 2, bytes(range(6)))


def test_still_artist_upload_ranks_below_artist_lyric_video():
    # The gap: the artist's bare-title album-art upload scored 85 and outranked
    # the same artist's lyric video.
    still = score_candidate(
        "Self-Esteem", "Dayglow",
        CandidateInput("a", "u", "Self-Esteem", "Dayglow", 200, channel_verified=True,
                       still_image=True),
    )
    lyric = score_candidate(
        "Self-Esteem", "Dayglow",
        CandidateInput("b", "u", "Dayglow - Self-Esteem (Official Lyric Video)", "Dayglow", 200,
                       channel_verified=True),
    )
    assert "still image for the whole video" in still.reasons
    assert lyric.score > still.score


def test_unknown_stillness_scores_as_before():
    unknown = score_candidate("Song", "Artist", CandidateInput("a", "u", "Song", "Artist", 200))
    assert unknown.score == 85


class StoryboardYouTube:
    def __init__(self, still):
        self.still = still
        self.checked = []

    def is_still_image(self, video_id):
        self.checked.append(video_id)
        return self.still.get(video_id)


def test_only_top_unmarked_results_are_checked(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    proc = JobProcessor(Settings(media_dir=tmp_path, still_image_checks=2), db)
    proc.youtube = StoryboardYouTube({"bare1": True})
    results = [
        CandidateInput("marked", "u", "Artist - Song (Official Video)", "Artist", 200),
        CandidateInput("bare1", "u", "Song", "Artist", 200),
        CandidateInput("bare2", "u", "Artist - Song", "Some Fan", 200),
        CandidateInput("bare3", "u", "Artist - Song lyrics", "Some Fan", 200),
    ]
    checked = proc._check_still_images("Song", "Artist", results)
    assert proc.youtube.checked == ["bare1", "bare2"]
    assert [c.still_image for c in checked] == [None, True, None, None]


def test_check_is_skipped_when_disabled(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    proc = JobProcessor(Settings(media_dir=tmp_path, still_image_checks=0), db)
    proc.youtube = StoryboardYouTube({})
    results = [CandidateInput("bare1", "u", "Song", "Artist", 200)]
    assert proc._check_still_images("Song", "Artist", results) == results
    assert proc.youtube.checked == []
