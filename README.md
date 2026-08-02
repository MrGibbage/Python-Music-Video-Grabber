# Music Video Grabber

Music Video Grabber turns the weekly SiriusXM Alt Nation Top 18 into a managed
music-video library. It captures the 18 most recent plays immediately after the
countdown broadcast, scores multiple YouTube
candidates, automatically downloads only high-confidence matches, and places
ambiguous results in a human review queue.

This repository started as a Windows scheduled script. The original scripts and
JSON history remain in the repository as migration inputs and historical
reference; the supported application is now the `music_video_grabber` package.

## What the MVP does

- Captures and dates the exact 18-play broadcast snapshot used by each run.
- Uses a durable SQLite job queue instead of holding work in an HTTP request.
- Prevents duplicates using normalized artist/title keys, legacy JSON history,
  and a scan of the actual NAS library.
- Scores several YouTube candidates and auto-approves only a high-scoring result
  with a safe lead over the runner-up.
- Offers a web dashboard for the latest captured chart, runs, jobs, and review.
- Downloads Plex-friendly MP4 files through yt-dlp and FFmpeg.
- Validates audio/video streams before atomically publishing a file.
- Triggers Personal MTV's existing scan endpoint after a successful publish.
- Emits JSON logs to stdout for Docker/Promtail/Loki.
- Sends run summaries to Telegram through apprise-mailrise.

There is deliberately no in-app scheduler. A host cron job calls the API after
the weekly Top 18 broadcast. A manual capture at an arbitrary time represents
ordinary recent plays, so the UI labels that distinction explicitly.

## Development

Python 3.12 or newer is required.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/pytest
.venv/Scripts/ruff check .
```

Start the API and worker in separate terminals:

```bash
music-video-grabber worker
uvicorn music_video_grabber.main:app --reload --port 8080
```

For local development, set `MVG_ENVIRONMENT=development`. Production refuses
authenticated API requests when `MVG_API_TOKEN` is empty.

## Triggering a run

```bash
curl -X POST https://grabber.pelorus.org/api/v1/runs \
  -H "Authorization: Bearer $MVG_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"station":"altnation"}'
```

The endpoint returns `202 Accepted`; use the returned run ID to inspect status.
Interactive OpenAPI documentation is available at `/docs`.

## Repeatable discovery testing

Set `MVG_XMPLAYLIST_FIXTURE_PATH` to an xmplaylist response-shaped JSON file to
run discovery from a saved 18-track capture instead of requesting xmplaylist.
The production compose stack mounts `./fixtures` read-only at `/fixtures`; the
captured Run 1 fixture is `/fixtures/xmplaylist-run-1.json`.

Leave the setting empty for ordinary live runs. A fixture still performs real
Spotify/YouTube lookups and downloads after the normal duplicate checks, so use
it only when intentionally exercising the acquisition workflow.

## Importing the existing library

Run this before the first acquisition:

```bash
music-video-grabber import-catalog --legacy /import/altnation-songs.json
```

The NAS scan is authoritative for files currently present. The legacy JSON adds
historical source IDs and prevents redownloading known songs even when older
filenames cannot be parsed perfectly. Imports are idempotent.

## Plex metadata snapshots (read-only)

Set `MVG_PLEX_URL`, `MVG_PLEX_TOKEN`, and, if needed,
`MVG_PLEX_LIBRARY_TITLE` in the host environment file. Then run:

```bash
music-video-grabber sync-plex-metadata
```

The command makes only `GET` requests to Plex and writes the discovered library
and media metadata to this application's SQLite database. It does not alter
Plex metadata, playlists, library settings, or the Plex database. Playlist
creation and Plex metadata edits are intentionally not implemented yet.

## Deployment

The production stack is designed for `/srv/music-video-grabber` on
`docker-server`. Secrets belong in `/etc/homelab/music-video-grabber.env`, and
the YouTube cookies file belongs at
`/etc/homelab/music-video-grabber.cookies.txt`. Neither is committed.

See [Architecture](docs/architecture.md), [API](docs/api.md), and
[Deployment](docs/deployment.md) for more detail.

## Safety and usage

Only download media you are authorized to store. YouTube access and formats can
change without warning; a failed job remains visible and retryable rather than
being silently discarded.
