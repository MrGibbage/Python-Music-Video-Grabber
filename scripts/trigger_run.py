#!/usr/bin/env python3
"""Cron-safe Alt Nation trigger; keeps the bearer token out of process arguments."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


ENV_FILE = Path("/etc/homelab/music-video-grabber.env")
API_URL = "http://192.168.0.231:8288/api/v1/runs"


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
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(response.read().decode())
        return 0
    except urllib.error.HTTPError as exc:
        print(exc.read().decode(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
