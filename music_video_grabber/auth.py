from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from .config import Settings
from .db import Database, utcnow

SESSION_COOKIE = "mvg_dashboard_session"
ALL_SCOPES = frozenset({"read", "runs:write", "review:write", "ops:write", "admin:tokens"})


@dataclass(frozen=True)
class Principal:
    kind: str
    scopes: frozenset[str]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def make_session(settings: Settings) -> str:
    if not settings.session_signing_secret:
        raise ValueError("No dashboard session-signing secret is configured")
    issued = str(int(time.time()))
    signature = hmac.new(
        settings.session_signing_secret.encode(), issued.encode(), hashlib.sha256
    ).hexdigest()
    return f"{issued}.{signature}"


def valid_session(value: str | None, settings: Settings) -> bool:
    if not value or not settings.session_signing_secret:
        return False
    try:
        issued, signature = value.split(".", 1)
        age = time.time() - int(issued)
    except (TypeError, ValueError):
        return False
    if age < 0 or age > settings.dashboard_session_ttl_hours * 3600:
        return False
    expected = hmac.new(
        settings.session_signing_secret.encode(), issued.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def dashboard_authenticated(request: Request, settings: Settings) -> bool:
    return valid_session(request.cookies.get(SESSION_COOKIE), settings)


def issue_api_token(db: Database, name: str, scopes: set[str]) -> tuple[dict[str, object], str]:
    raw = f"mvg_{secrets.token_urlsafe(32)}"
    prefix = raw[:16]
    with db.connect() as conn:
        cursor = conn.execute(
            """INSERT INTO api_tokens(name, token_prefix, token_hash, scopes_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (name, prefix, _digest(raw), json.dumps(sorted(scopes)), utcnow()),
        )
        token_id = int(cursor.lastrowid)
    return {"id": token_id, "name": name, "prefix": prefix, "scopes": sorted(scopes)}, raw


def api_principal(authorization: str | None, settings: Settings, db: Database) -> Principal | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ")
    if settings.api_token and hmac.compare_digest(token, settings.api_token):
        return Principal("service", ALL_SCOPES)
    row = db.one(
        "SELECT id, scopes_json FROM api_tokens WHERE token_hash=? AND revoked_at IS NULL",
        (_digest(token),),
    )
    if not row:
        return None
    scopes = frozenset(json.loads(row["scopes_json"]))
    with db.connect() as conn:
        conn.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (utcnow(), row["id"]))
    return Principal("personal", scopes)


def authorize(
    request: Request,
    authorization: str | None,
    settings: Settings,
    db: Database,
    required_scope: str,
) -> Principal:
    if dashboard_authenticated(request, settings):
        return Principal("dashboard", ALL_SCOPES)
    principal = api_principal(authorization, settings, db)
    if principal and required_scope in principal.scopes:
        return principal
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sign in to the dashboard or provide an API token with the required scope",
        headers={"WWW-Authenticate": "Bearer"},
    )
