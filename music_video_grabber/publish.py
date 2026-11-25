"""Render the public Top 18 archive: one standalone HTML page, confirmed weeks only.

Only charts confirmed against a weekend replay (source ``xmplaylist_countdown``,
from 2026-10-31 on) are published. Earlier charts came from a fixed-time capture
that was wrong in 3 of the 4 weeks it could be checked against, so they stay
private to the dashboard.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from .countdown import EASTERN
from .db import Database
from .text import canonical_track_key

CONFIRMED = "xmplaylist_countdown"

_CSS = """
:root {
  --bg:#fbfbfd;
  --ink:#16181d;
  --muted:#5d6472;
  --line:#e3e6ec;
  --up:#167a3e;
  --down:#b3261e;
  --new:#7a4b00;
  --newbg:#fdf0d5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0f1218;
    --ink:#eef1f6;
    --muted:#9aa3b2;
    --line:#262c38;
    --up:#5fd38d;
    --down:#ff8a80;
    --new:#ffd37a;
    --newbg:#3a2d12;
  }
}
* { box-sizing:border-box; }
body {
  margin:0;
  background:var(--bg);
  color:var(--ink);
  font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
}
main { max-width:760px; margin:0 auto; padding:32px 16px 48px; }
h1 { font-size:1.7rem; margin:0 0 4px; letter-spacing:-.02em; }
h2 { font-size:1.15rem; margin:28px 0 8px; }
p { color:var(--muted); margin:6px 0; }
table { width:100%; border-collapse:collapse; margin:6px 0 10px; }
th,td {
  text-align:left;
  padding:7px 8px;
  border-bottom:1px solid var(--line);
  vertical-align:top;
}
th { color:var(--muted); font-size:.75rem; text-transform:uppercase; letter-spacing:.08em; }
td.rank { font-weight:700; width:3.2em; }
td:nth-child(2) { width:4.2em; white-space:nowrap; font-size:.9rem; }
.up { color:var(--up); } .down { color:var(--down); } .flat { color:var(--muted); }
.new {
  color:var(--new);
  background:var(--newbg);
  border-radius:4px;
  padding:0 5px;
  font-size:.75rem;
  font-weight:700;
}
details { border-top:1px solid var(--line); padding:10px 0; }
summary { cursor:pointer; font-weight:600; }
footer { margin-top:36px; font-size:.85rem; }
"""


def confirmed_weeks(db: Database, station: str = "altnation") -> list[dict]:
    """Confirmed charts newest first, each entry with ``move`` against the
    confirmed chart of the week before (None when there isn't one)."""
    charts = db.query(
        "SELECT * FROM charts WHERE station=? AND source=? ORDER BY as_of DESC",
        (station, CONFIRMED),
    )
    for chart in charts:
        chart["entries"] = db.query(
            "SELECT rank, artist, title FROM chart_entries WHERE chart_id=? ORDER BY rank",
            (chart["id"],),
        )
    for index, chart in enumerate(charts):
        older = charts[index + 1] if index + 1 < len(charts) else None
        gap = (
            (_as_of(chart) - _as_of(older)).days if older else None
        )
        previous = (
            {canonical_track_key(e["artist"], e["title"]): e["rank"] for e in older["entries"]}
            if older and 5 <= gap <= 9
            else None
        )
        for entry in chart["entries"]:
            if previous is None:
                entry["move"] = None
            else:
                last = previous.get(canonical_track_key(entry["artist"], entry["title"]))
                entry["move"] = "new" if last is None else last - entry["rank"]
    return charts


def _as_of(chart: dict) -> datetime:
    return datetime.fromisoformat(chart["as_of"]).astimezone(EASTERN)


def _move_html(move: int | str | None) -> str:
    if move is None:
        return '<span class="flat">–</span>'
    if move == "new":
        return '<span class="new">NEW</span>'
    if move > 0:
        return f'<span class="up">▲{move}</span>'
    if move < 0:
        return f'<span class="down">▼{-move}</span>'
    return '<span class="flat">=</span>'


def render_top18_html(db: Database) -> str:
    """Deterministic for the same data (no "generated at" time), so the weekly
    publisher only commits when a new week is confirmed."""
    weeks = confirmed_weeks(db)
    sections = []
    for index, chart in enumerate(weeks):
        day = _as_of(chart)
        label = f"{day:%B} {day.day}, {day.year}"
        rows = "".join(
            f"<tr><td class=\"rank\">{e['rank']}</td><td>{_move_html(e['move'])}</td>"
            f"<td>{escape(e['artist'])}</td><td>{escape(e['title'])}</td></tr>"
            for e in chart["entries"]
        )
        table = (
            "<table><thead><tr><th>Rank</th><th>Move</th><th>Artist</th><th>Song</th></tr>"
            f"</thead><tbody>{rows}</tbody></table>"
        )
        week_id = f"{day:%Y-%m-%d}"
        if index == 0:
            sections.append(
                f'<section id="{week_id}"><h2>Week of {label}</h2>{table}</section>'
            )
        else:
            sections.append(
                f'<details id="{week_id}"><summary>Week of {label}</summary>{table}</details>'
            )
    body = "".join(sections) or "<p>No confirmed weeks yet.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alt Nation Top 18 archive</title>
<style>
{_CSS}</style>
</head>
<body>
<main>
<h1>Alt Nation Top 18 archive</h1>
<p>An unofficial weekly record of the Top 18 countdown on SiriusXM's Alt Nation. Rank 1 is
the song played last. Move compares with the week before.</p>
{body}
<footer>
<p>Compiled from <a href="https://xmplaylist.com/">xmplaylist.com</a> airplay logs. Each
week's list is confirmed by matching the Saturday broadcast against a weekend replay:
both air the same 18 songs in the same order. Not affiliated with or endorsed by SiriusXM.</p>
<p>{len(weeks)} confirmed week{"" if len(weeks) == 1 else "s"} since October 31, 2026.</p>
</footer>
</main>
</body>
</html>
"""
