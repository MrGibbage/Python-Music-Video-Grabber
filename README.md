# Music Video Grabber

Music Video Grabber turns the weekly SiriusXM Alt Nation Top 18 into a managed
music-video library. It retrieves the latest playlist, scores multiple YouTube
candidates, automatically downloads only high-confidence matches, and places
ambiguous results in a human review queue.

This repository started as a Windows scheduled script. The original scripts and
JSON history remain in the repository as migration inputs and historical
reference; the supported application is now the `music_video_grabber` package.

## What the MVP does

- Accepts an API request to process the latest 18 Alt Nation plays.
- Uses a durable SQLite job queue instead of holding work in an HTTP request.
- Prevents duplicates using normalized artist/title keys, legacy JSON history,
  and a scan of the actual NAS library.
- Scores several YouTube candidates and auto-approves only a high-scoring result
  with a safe lead over the runner-up.
- Offers a web dashboard for runs, jobs, and candidate review.
- Downloads Plex-friendly MP4 files through yt-dlp and FFmpeg.
- Validates audio/video streams before atomically publishing a file.
- Triggers Personal MTV's existing scan endpoint after a successful publish.
- Emits JSON logs to stdout for Docker/Promtail/Loki.
- Sends run summaries to Telegram through apprise-mailrise.

There is deliberately no in-app scheduler. A host cron job calls the API after
the weekly Top 18 broadcast.

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

## Importing the existing library

Run this before the first acquisition:

```bash
music-video-grabber import-catalog --legacy /import/altnation-songs.json
```

The NAS scan is authoritative for files currently present. The legacy JSON adds
historical source IDs and prevents redownloading known songs even when older
filenames cannot be parsed perfectly. Imports are idempotent.

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
