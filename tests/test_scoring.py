from music_video_grabber.scoring import CandidateInput, score_candidate, should_auto_approve


def candidate(title: str, uploader: str = "ArtistVEVO", duration: int = 220) -> CandidateInput:
    return CandidateInput("abc", "https://example/abc", title, uploader, duration)


def test_official_artist_upload_scores_highly():
    result = score_candidate(
        "Helena Beat",
        "Foster The People",
        candidate("Foster The People - Helena Beat (Official Music Video)", "FosterThePeopleVEVO"),
    )
    assert result.score >= 88
    assert "official video" in result.reasons


def test_lyric_and_live_versions_are_penalized():
    official = score_candidate("Song", "Artist", candidate("Artist - Song (Official Video)"))
    lyric = score_candidate("Song", "Artist", candidate("Artist - Song (Lyric Video)"))
    live = score_candidate("Song", "Artist", candidate("Artist - Song Live at Wembley"))
    assert official.score > lyric.score
    assert official.score > live.score


def test_auto_approval_requires_threshold_and_margin():
    assert should_auto_approve([94, 80], threshold=88, minimum_margin=8)
    assert not should_auto_approve([94, 91], threshold=88, minimum_margin=8)
    assert not should_auto_approve([85, 50], threshold=88, minimum_margin=8)


def test_screenshot_case_artist_channel_beats_reupload():
    own = score_candidate(
        "Silver Spoons",
        "Portugal. The Man",
        CandidateInput("a", "u", "Silver Spoons", "Portugal. The Man", 207, channel_verified=True),
    )
    reupload = score_candidate(
        "Silver Spoons",
        "Portugal. The Man",
        CandidateInput("b", "u", "Portugal. The Man - Silver Spoons", "David Dean Burkhart", 205),
    )
    assert "uploaded by artist" in own.reasons
    assert "verified channel" in own.reasons
    assert "artist in title 100%" in reupload.reasons
    assert "re-upload by David Dean Burkhart" in reupload.reasons
    assert own.score - reupload.score >= 10


def test_bare_title_is_capped_below_auto_approve():
    # The artist's own channel also posts still-image audio uploads under the bare
    # song title, so nothing without a video marker may clear the 88 bar.
    own = score_candidate(
        "Song", "Artist", CandidateInput("a", "u", "Song", "Artist", 200, channel_verified=True)
    )
    assert own.score <= 85
    assert "no video marker in title: needs review" in own.reasons


def test_topic_channel_is_penalized():
    topic = score_candidate("Song", "Artist", candidate("Artist - Song", "Artist - Topic"))
    official = score_candidate("Song", "Artist", candidate("Artist - Song (Official Video)"))
    assert "auto-generated Topic channel (album art)" in topic.reasons
    assert "uploaded by artist" not in topic.reasons
    assert official.score - topic.score >= 30


def test_label_and_session_channels_count_as_official():
    label = score_candidate(
        "Song", "Artist", candidate("Artist - Song (Official Video)", "Fueled By Ramen")
    )
    reupload = score_candidate(
        "Song", "Artist", candidate("Artist - Song (Official Video)", "Random Fan 99")
    )
    assert "official channel: Fueled By Ramen" in label.reasons
    assert label.score > reupload.score


def test_artist_channel_match_is_not_a_substring_match():
    result = score_candidate(
        "Song", "Muse", candidate("Muse - Song (Official Video)", "MuseumOfSound")
    )
    assert "uploaded by artist" not in result.reasons
    assert "re-upload by MuseumOfSound" in result.reasons


def test_any_credited_artist_channel_counts():
    result = score_candidate(
        "There's a Field",
        "Daniel Caesar, Rex Orange County",
        candidate("Daniel Caesar & Rex Orange County - There's a Field (Official Video)",
                  "Daniel Caesar"),
    )
    assert "uploaded by artist" in result.reasons


def test_unknown_uploader_is_not_called_a_reupload():
    result = score_candidate("Song", "Artist", candidate("Artist - Song (Official Video)", ""))
    assert not any(reason.startswith("re-upload") for reason in result.reasons)


def test_other_song_from_artist_channel_does_not_outrank_right_song():
    wrong = score_candidate(
        "Hymn to Virgil", "Hozier",
        candidate("Hozier - Eat Your Young (Official Video)", "Hozier"),
    )
    right = score_candidate(
        "Hymn to Virgil", "Hozier",
        candidate("Hozier - Hymn to Virgil (Acapella Version)", "acapellago"),
    )
    assert "title mismatch: probably a different song" in wrong.reasons
    assert right.score > wrong.score
