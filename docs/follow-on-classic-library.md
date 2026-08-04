# Follow-on Project: Configurable Music Video Library Builder

## Vision

Build a general-purpose application for collecting a personal music-video
library from curated rules and external data sources. Classic rock is the first
use case, not the product boundary.

The central question is:

> How can I collect music videos that match my own parameters from APIs, CSV
> files, curated lists, or AI-assisted prompts—safely and repeatably?

Example collections could include Pop, classic/alternative/indie Rock, Hip-hop,
R&B, Country, EDM/Dance, Latin (Reggaeton and Latin Pop), K-pop, or any
combination defined by the owner.

## Product model

One application acts as a control plane. It has clear internal responsibilities
without requiring separate deployed services at first:

```text
Discoverers -> desired-track catalog -> acquisition queue -> library publisher -> Plex sync
                         ^                    |                     |
                         +--------------------+---------------------+
                                      dashboard and review
```

### Discoverers

Discoverers create **desired tracks** with provenance. They do not download
video. Initial adapters should support:

- dated charts and chart archives;
- public music APIs;
- Spotify or other playlist imports;
- CSV/JSON uploads;
- manually entered artist/song lists; and
- AI-assisted collection plans that must be reviewed before import.

Each discovered item records its source, source identifier, date, rank or other
source attributes, and the collection rule that admitted it.

### Acquisition

The acquisition worker is shared conceptual territory with Music Video Grabber:

- canonical artist/title matching;
- candidate search and scoring;
- human review for uncertain matches;
- downloads, stream validation, and atomic publish; and
- durable retryable jobs.

It owns the global YouTube search/download rate limit. Discoverers should
throttle their own upstream APIs and pace enqueueing, but only the acquisition
layer knows when it is about to make YouTube-facing requests.

### Library and Plex

The library layer records a published item once, even when it belongs to many
collections. Plex playlists are derived views of that catalog. Playlist updates
must remain explicit, dry-run first, rollback-manifest protected, and separate
from Plex metadata or library-setting changes.

### Dashboard

One UI should show collections, their source/provenance, desired tracks,
acquisition state, review work, published files, rate-limit health, and Plex
playlist plans. Splitting this into separate front ends would make the workflow
harder to understand.

## Collection recipes

A collection is a saved, versioned recipe rather than a hard-coded genre. A
recipe might define:

- one or more discoverers and source filters;
- years, chart positions, regions, genres, artists, or playlist IDs;
- inclusion and exclusion rules;
- a maximum collection size and ordering;
- media output location and naming policy;
- approval threshold and review policy; and
- optional derived Plex playlist behavior.

Examples:

- “Classic Rock: Billboard Hot 100 rock-adjacent songs, 1967–1992, peak ≤ 40.”
- “K-pop essentials: selected playlists since 2012, one video per canonical
  recording.”
- “My 1990s dance floor: CSV of 400 tracks, all manually reviewed.”
- “Alt Nation monthly adds: current station feed, newest 25, auto-approval only
  above a conservative confidence threshold.”

## Safety and scale rules

- Every import starts with a dry-run count and a sample of prospective tracks.
- AI prompts produce a reviewable proposed list, never immediate downloads.
- Canonical deduplication happens before candidate search.
- A global token-bucket/concurrency policy limits YouTube search, metadata, and
  download operations across every collection.
- The queue spreads work over time; a 1,000-track collection is a controlled
  campaign, not a burst.
- Human review remains available for ambiguous video matches and source imports.
- Published files are never deleted by automatic collection maintenance.
- Plex changes stay narrowly scoped to explicitly managed playlists, with a
  dry-run and rollback manifest.

## Suggested MVP sequence

1. Start a new application using the lessons from MVG, rather than immediately
   generalizing the working Alt Nation service.
2. Build the shared core: SQLite catalog, durable acquisition queue, candidate
   scoring/review, validation, publication, and rate limiting.
3. Add CSV/JSON import first; it is the simplest broad discoverer and makes the
   product useful immediately.
4. Add a dated-chart adapter and a collection-recipe UI.
5. Add API/playlist discoverers, then AI-assisted proposal generation.
6. Add derived Plex playlists using the same guarded model proven in MVG.
7. Extract a shared acquisition package only after both projects demonstrate
   which behavior is genuinely common.

## Relationship to Music Video Grabber

Music Video Grabber should remain focused and reliable for Alt Nation. It is the
operational reference implementation for durable jobs, cautious downloading,
manual review, dashboards, and Plex safeguards. The follow-on project can reuse
its lessons without forcing a station-specific product to become a generic
library builder prematurely.
