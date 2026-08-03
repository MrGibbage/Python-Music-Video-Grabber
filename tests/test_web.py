from pathlib import Path

from fastapi.testclient import TestClient

from music_video_grabber.config import Settings, get_settings
from music_video_grabber.db import Database
from music_video_grabber.main import app


def test_dashboard_renders() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Music Video Grabber" in response.text


def test_dashboard_login_and_personal_token_scopes(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_path=tmp_path / "mvg.db",
        media_dir=tmp_path,
        app_password="test-password",
        dashboard_session_secret="test-session-secret",
        api_token="legacy-service-token",
    )
    Database(settings.database_path).initialize()
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app, base_url="https://testserver") as client:
            assert "Sign in" in client.get("/").text
            assert client.post("/auth/login", data={"password": "wrong"}).status_code == 401
            login = client.post(
                "/auth/login", data={"password": "test-password"}, follow_redirects=False
            )
            assert login.status_code == 303
            assert "HttpOnly" in login.headers["set-cookie"]
            assert "Secure" in login.headers["set-cookie"]
            assert "Recent runs" in client.get("/").text

            created = client.post(
                "/api/v1/tokens", json={"name": "read client", "scopes": ["read"]}
            )
            assert created.status_code == 201
            secret = created.json()["secret"]
            token_id = created.json()["token"]["id"]
            assert secret.startswith("mvg_")

            client.post("/auth/logout", follow_redirects=False)
            assert client.get("/api/v1/runs").status_code == 401
            headers = {"Authorization": f"Bearer {secret}"}
            assert client.get("/api/v1/runs", headers=headers).status_code == 200
            response = client.post(
                "/api/v1/runs", headers=headers, json={"station": "altnation"}
            )
            assert response.status_code == 401

            client.post("/auth/login", data={"password": "test-password"}, follow_redirects=False)
            assert client.delete(f"/api/v1/tokens/{token_id}").status_code == 200
            client.post("/auth/logout", follow_redirects=False)
            assert client.get("/api/v1/runs", headers=headers).status_code == 401
    finally:
        app.dependency_overrides.clear()
