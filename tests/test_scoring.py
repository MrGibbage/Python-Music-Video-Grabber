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
