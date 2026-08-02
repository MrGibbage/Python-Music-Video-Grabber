# Deployment

## Production layout

```text
/srv/music-video-grabber/                 Git working tree
/srv/music-video-grabber/data/            SQLite state and backups
/etc/homelab/music-video-grabber.env       Environment and API secrets
/etc/homelab/music-video-grabber.cookies.txt  YouTube cookies
/mnt/nas/media/Music Videos - Alternative/    Published media
```

The compose stack joins the existing `apprise-mailrise_default` network so it
can send SMTP to `apprise-mailrise:8025`. Both containers log to Docker's normal
JSON log stream for Promtail discovery; do not add another logging driver.

The worker runs as UID 1000 with docker-server's Docker-group GID 988 solely so
it can read the root-owned `0640` cookies file. It has no Docker socket mount,
so group membership does not grant container-management access.

## First import

```bash
docker compose run --rm music-video-grabber-worker \
  music-video-grabber import-catalog --legacy /import/altnation-songs.json
```

Run the import before enabling cron. Review the imported counts and take an
online SQLite backup afterward.

## Backup

Create a transactionally consistent SQLite snapshot:

```bash
docker compose run --rm music-video-grabber-worker \
  music-video-grabber backup-db /data/backups/music-video-grabber.db
```

The existing docker-server Restic sweep can then protect the snapshot and the
rest of `/srv/music-video-grabber`. Raw SQLite/WAL file copies are not treated as
a verified application backup.

## Cron

Cron is external by design. It should call `POST /api/v1/runs` after the weekly
show and load the bearer token from a root-readable configuration file. Do not
put the token directly in the crontab command line.
