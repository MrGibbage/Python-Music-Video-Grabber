#!/usr/bin/env python3
"""Cron-safe Alt Nation trigger; keeps the bearer token out of process arguments."""

from __future__ import annotations

import json
import smtplib
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path

ENV_FILE = Path("/etc/homelab/music-video-grabber.env")
API_URL = "http://192.168.0.231:8288/api/v1/runs"
MAILRISE_HOST = "192.168.0.231"
MAILRISE_PORT = 8025
NOTIFICATION_TO = "telegram@mailrise.xyz"


def notify_trigger_failure(detail: str) -> None:
    """Report failures that occur before the application can notify on its own."""
    message = EmailMessage()
    message["From"] = "music-video-grabber@pelorus.org"
    message["To"] = NOTIFICATION_TO
    message["Subject"] = "Alt Nation music video run failed to start"
    message.set_content(
        "The Saturday Alt Nation run could not be queued.\n\n"
        f"Reason: {detail}\n\n"
        "Check the music-video-grabber containers and cron log on docker-server."
    )
    try:
        with smtplib.SMTP(MAILRISE_HOST, MAILRISE_PORT, timeout=15) as smtp:
            smtp.send_message(message)
    except Exception as exc:  # The original error remains the primary failure.
        print(f"The failure notification also failed: {exc}", file=sys.stderr)


def read_token(path: Path) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "MVG_API_TOKEN":
            return value.strip().strip("'\"")
    raise RuntimeError(f"MVG_API_TOKEN not found in {path}")


def main() -> int:
    try:
        token = read_token(ENV_FILE)
        request = urllib.request.Request(
            API_URL,
            data=json.dumps({"station": "altnation"}).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "music-video-grabber-cron/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            print(response.read().decode())
        return 0
    except urllib.error.HTTPError as exc:
        detail = f"API returned HTTP {exc.code}: {exc.read().decode()}"
        print(detail, file=sys.stderr)
        notify_trigger_failure(detail)
        return 1
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        detail = str(exc)
        print(detail, file=sys.stderr)
        notify_trigger_failure(detail)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
