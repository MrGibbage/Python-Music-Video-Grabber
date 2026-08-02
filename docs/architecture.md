# Architecture

## Components

- **API:** FastAPI serves the dashboard, authenticated API, health checks, and
  OpenAPI schema. It only creates durable work; it does not perform downloads.
- **Worker:** One process claims SQLite jobs and performs discovery, matching,
  metadata lookup, download, validation, and publication.
- **SQLite:** Stores dated chart snapshots, runs, jobs, tracks, candidates, and
  an operational event history. WAL mode allows the API and single worker to
  share the database.
- **NAS:** Downloads are created below a hidden staging directory on the same
  filesystem as the final library. `os.replace` then publishes them atomically.

## Matching policy

Candidate scores combine title similarity, artist/uploader similarity, and
positive signals such as official-video and VEVO labels. Lyric videos, audio-only
uploads, live performances, covers, reactions, remixes, karaoke, and very short
videos receive penalties.

Automatic approval requires both:

1. The best candidate reaches `MVG_AUTO_APPROVE_SCORE` (default 88).
2. It leads the second candidate by `MVG_AUTO_APPROVE_MARGIN` (default 8).

Everything else enters the review queue. Scores and their reasons are stored so
the decision is inspectable rather than opaque.

## Duplicate prevention

Artist and title are normalized into a canonical key that ignores case,
punctuation, and accents. Initial import combines:

- Actual MP4 filenames in the Alternative music-video NAS directory.
- The historical `altnation-songs.json` download database.

Tracks marked `imported` or `published` are never queued again automatically.

## Chart capture

xmplaylist exposes recent play history, not an official chart object. The
scheduled run is deliberately aligned just after the Alt18 broadcast and saves
the newest 18 plays as an immutable snapshot. Because the countdown airs from
18 to 1, reverse playback order supplies the displayed rank. The UI shows both
the newest play timestamp ("as of") and the capture time.

This inference is only valid when capture timing matches the show. Manual runs
at other times remain possible, but the UI describes them as recent-play
captures. The chart records include a source field so a future verified search
or manually supplied fallback can coexist without pretending it came from
xmplaylist.

## Failure model

Jobs are claimed using a SQLite immediate transaction. Failed work is retried up
to the configured attempt limit. The final failure, exception text, and event
history remain visible in the API and dashboard.
