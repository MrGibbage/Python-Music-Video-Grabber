#!/bin/bash
# Publish the public Top 18 archive (GitHub Pages, gh-pages branch) when a new
# week has been confirmed. Cron runs this after the weekend countdown attempts;
# it exits quietly when nothing changed, because the page is deterministic for
# the same data. Only confirmed weeks are rendered (see publish.py).
#
# Needs: the gh-pages worktree at $PAGES (git worktree add ... gh-pages) and
# skip's existing GitHub SSH key, which this host already pushes with.
set -euo pipefail

PAGES=/home/skip/mvg-pages
DATA=/srv/music-video-grabber/data

notify_failure() {
  /usr/bin/python3 - "$1" <<'EOF' || true
import smtplib, sys
from email.message import EmailMessage
m = EmailMessage()
m["From"] = "music-video-grabber@pelorus.org"
m["To"] = "telegram@mailrise.xyz"
m["Subject"] = "Top 18 archive publish failed"
m.set_content(f"Publishing the Top 18 archive to GitHub Pages failed at line {sys.argv[1]}.\n"
              "See /srv/music-video-grabber/data/publish.log on docker-server.")
with smtplib.SMTP("192.168.0.231", 8025, timeout=15) as s:
    s.send_message(m)
EOF
}
trap 'notify_failure $LINENO' ERR

docker exec music-video-grabber-worker music-video-grabber export-top18 /data/top18.html >/dev/null
cd "$PAGES"
git pull -q --ff-only origin gh-pages
if cmp -s "$DATA/top18.html" index.html; then
  exit 0
fi
cp "$DATA/top18.html" index.html
git add index.html
git commit -q -m "Top 18 archive: $(grep -o 'Week of [^<]*' index.html | head -1)"
GIT_SSH_COMMAND="ssh -o BatchMode=yes" git push -q origin gh-pages
echo "$(date -Is) published $(grep -o 'Week of [^<]*' index.html | head -1)"
