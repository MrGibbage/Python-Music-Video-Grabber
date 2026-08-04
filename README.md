# Music Video Grabber

Music Video Grabber (MVG) turns recent-play data from SiriusXM stations into a
managed music-video library. It captures a dated, caller-selected window,
scores YouTube candidates, downloads only confident matches, and keeps
ambiguous choices in a small human review queue.

It is built for a real media library rather than a throwaway downloader:

- durable SQLite runs, jobs, candidates, and event history;
- duplicate prevention from the NAS and legacy download history;
- atomic MP4 publication after audio/video validation;
- a password-protected dashboard, review queue, and scoped personal API tokens;
- Telegram run summaries through Apprise Mailrise; and
- optional Plex-aware playlists with read-only snapshots and guarded refresh.

The legacy Windows scripts and `altnation-songs.json` remain as migration and
history inputs. The supported service is the `music_video_grabber` package and
its Docker Compose stack.

## How it works

```text
xmplaylist recent plays -> dated station snapshot -> candidate scoring
                                                        |          |
                                                auto-approved   review queue
                                                        |
                                               yt-dlp + validation
                                                        |
                                                atomic NAS publish
```

Any safe xmplaylist station identifier can be requested with a window of 1–100
recent plays (18 by default). When an Alt Nation run happens immediately after
the weekly Alt18 broadcast, MVG reverses the most recent 18 plays to infer ranks
1–18. A manual run at another time, or for another station, represents ordinary
recent plays rather than a countdown.

Automatic approval requires a high score *and* a safe lead over the runner-up.
Every other choice remains inspectable in the dashboard with scoring reasons.

## Dashboard

The dashboard shows the latest capture, run result, downloads, review queue,
recent jobs, and Settings. Settings includes scoped personal API tokens and
Plex playlist preferences. The browser uses an `HttpOnly`, `Secure`,
`SameSite=Lax` session cookie and never receives the service API token.

## Production deployment

```text
/srv/music-video-grabber/                       Git checkout and Compose project
/srv/music-video-grabber/data/                  SQLite state and application backups
/etc/homelab/music-video-grabber.env            Secrets and service configuration
/etc/homelab/music-video-grabber.cookies.txt    YouTube cookies (optional)
/mnt/nas/media/Music Videos - Alternative/      Published MP4 library
```

From the production checkout:

```bash
cd /srv/music-video-grabber
docker compose build
docker compose up -d
docker compose ps
```

The API container is read-only against the media library. The worker is the
only service that can publish media.

### Configuration

Copy non-secret defaults from [`.env.example`](.env.example) to the protected
host configuration file. At minimum configure:

```dotenv
MVG_API_TOKEN=<long random service token>
MVG_APP_PASSWORD=<dashboard password>
MVG_DASHBOARD_SESSION_SECRET=<at least 32 random bytes, encoded as text>
MVG_DATABASE_PATH=/data/music-video-grabber.db
MVG_MEDIA_DIR=/media
```

Generate the session secret locally, never in source control or chat:

```bash
openssl rand -base64 48
```

`MVG_API_TOKEN` is for cron and machine-to-machine use. Dashboard administrators
can create named, scoped personal tokens in Settings; only their hashes are kept.

## Triggering a capture

MVG intentionally has no in-app scheduler. A host cron job should call the API
after the weekly broadcast and load its token from the protected environment.

```bash
curl --fail-with-body -X POST https://grabber.pelorus.org/api/v1/runs \
  -H "Authorization: Bearer $MVG_API_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"station":"altnation","song_count":18}'
```

The endpoint returns `202 Accepted`; the worker processes durable jobs in the
background. Station identifiers are validated by xmplaylist when the worker
fetches them; MVG accepts only letters, numbers, and hyphens. See `/docs` or
[docs/api.md](docs/api.md) for the API.

## First import and backups

Before enabling scheduled acquisition, import the NAS directory and legacy
history. This is idempotent.

```bash
docker compose run --rm --no-deps music-video-grabber-worker \
  music-video-grabber import-catalog --legacy /import/altnation-songs.json
```

Create an online SQLite backup—not a raw database/WAL file copy—with:

```bash
docker compose run --rm --no-deps music-video-grabber-worker \
  music-video-grabber backup-db /data/backups/music-video-grabber.db
```

## Repeatable discovery testing

Set `MVG_XMPLAYLIST_FIXTURE_PATH` to a captured xmplaylist response-shaped JSON
file to repeat discovery without waiting for a broadcast. The production Compose
stack mounts `fixtures/` read-only at `/fixtures`; the bundled capture is
`/fixtures/xmplaylist-run-1.json`.

A fixture still performs normal Spotify/YouTube lookups, duplicate checks, and
downloads. Use an isolated database and empty media directory for end-to-end
download testing.

## Plex: snapshots, playlists, and refresh safety

Keep Plex values only in the protected host environment file:

```dotenv
MVG_PLEX_URL=http://<plex-server>:32400
MVG_PLEX_TOKEN=<long-lived Plex token>
MVG_PLEX_LIBRARY_TITLE=Music Videos - Alternative
```

Never commit or display the Plex token.

### Read-only snapshot

```bash
docker compose run --rm --no-deps music-video-grabber-worker \
  music-video-grabber sync-plex-metadata
```

This uses only Plex `GET` requests and stores the selected library metadata in
MVG’s SQLite database. It does not change Plex metadata, library settings,
playlists, or Plex’s database.

### Static playlists

MVG manages these static video playlists:

- `Alt Nation — Latest Top 18`
- `Music Videos — New (Last 2 Years)`
- `Music Videos — Older (2+ Years)`

First print the exact plan (read-only):

```bash
docker compose run --rm --no-deps music-video-grabber-worker \
  music-video-grabber sync-plex-playlists
```

The explicit apply form creates only missing target playlists and refuses to
alter an existing playlist:

```bash
docker compose run --rm --no-deps music-video-grabber-worker \
  music-video-grabber sync-plex-playlists --apply
```

### Safe playlist refresh

Before every refresh, update the local snapshot and inspect differences:

```bash
docker compose run --rm --no-deps music-video-grabber-worker \
  music-video-grabber sync-plex-metadata
docker compose run --rm --no-deps music-video-grabber-worker \
  music-video-grabber plan-plex-playlist-refresh
```

The plan is GET-only. Dashboard Settings selects which playlist types a refresh
may manage. By default, a refresh preserves videos manually added in Plex and
only appends missing planned items.

The explicit apply writes a JSON rollback manifest under `data/backups/` first:

```bash
docker compose run --rm --no-deps music-video-grabber-worker \
  music-video-grabber apply-plex-playlist-refresh --apply
```

If refresh fails, MVG restores the original playlist membership from that
manifest. If the “Remove videos not selected by the refresh plan” setting is
enabled, MVG rebuilds the selected playlist to the exact planned order. That
intentionally removes manual additions, so always review a dry-run first.

Plex metadata, library settings, media files, and Plex’s database are never
changed by this workflow. Playlist writes are limited to the selected existing
MVG playlists.

## Local development

Python 3.12+ is required.

```bash
python -m venv .venv
. .venv/bin/activate                  # Windows: .venv\Scripts\Activate.ps1
pip install -e '.[dev]'
pytest -q
ruff check music_video_grabber tests
```

Run the worker and API in separate terminals:

```bash
music-video-grabber worker
uvicorn music_video_grabber.main:app --reload --port 8080
```

For end-to-end testing, use a separate SQLite database and empty media output.

## Operating principles

- Download and retain only media you are authorized to store.
- Keep secrets, cookies, and Plex tokens outside Git and chat.
- Make an online SQLite backup before material database operations.
- Treat Plex playlist writes as deliberate, reviewed operations.
- Do not run catalog import casually: it changes MVG’s local ownership catalog
  and can suppress rediscovery.

More detail: [architecture](docs/architecture.md),
[deployment](docs/deployment.md), [API](docs/api.md), and the
[follow-on configurable library-builder project](docs/follow-on-classic-library.md).
