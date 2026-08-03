# API

The dashboard requires a password login. It uses a signed, `HttpOnly`, `Secure`,
same-site session cookie; the browser never receives the configured service
token. Health checks remain unauthenticated.

External clients use `Authorization: Bearer <token>`. The configured
`MVG_API_TOKEN` remains the service/automation token. Dashboard administrators
can also create named, scoped personal tokens in Settings; token secrets are
shown only once and the database stores only their SHA-256 hashes.

Available personal-token scopes are `read`, `runs:write`, `review:write`,
`ops:write`, and `admin:tokens`.

## Main endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/runs` | Capture and process the current 18-play window |
| `GET` | `/api/v1/runs` | List recent runs |
| `GET` | `/api/v1/runs/{id}` | Show one run, its jobs, and events |
| `GET` | `/api/v1/charts/latest` | Show the latest dated broadcast snapshot and ranks |
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
