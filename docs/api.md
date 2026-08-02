# API

All `/api/v1` endpoints require `Authorization: Bearer <token>`. Health checks
and the dashboard shell are intentionally unauthenticated; dashboard data still
requires the token, which is kept in browser session storage.

## Main endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/runs` | Queue the latest Alt Nation Top 18 |
| `GET` | `/api/v1/runs` | List recent runs |
| `GET` | `/api/v1/runs/{id}` | Show one run, its jobs, and events |
| `GET` | `/api/v1/tracks` | Query the catalog and acquisition states |
| `GET` | `/api/v1/tracks/{id}` | Show a track and all candidates |
| `GET` | `/api/v1/review` | Show ambiguous tracks and scored candidates |
| `POST` | `/api/v1/tracks/{id}/approve` | Queue an approved candidate |
| `POST` | `/api/v1/tracks/{id}/reject` | Reject one candidate |
| `GET` | `/api/v1/jobs` | Show durable worker jobs |
| `POST` | `/api/v1/jobs/{id}/retry` | Retry a failed job |
| `POST` | `/api/v1/jobs/{id}/cancel` | Cancel a queued job |
| `POST` | `/api/v1/catalog/import` | Queue an idempotent NAS/history import |
| `GET` | `/api/v1/events` | Show operational events |
| `GET` | `/health/live` | Process liveness |
| `GET` | `/health/ready` | Database and NAS readiness |

The generated schema at `/openapi.json` is the canonical request/response
reference.
